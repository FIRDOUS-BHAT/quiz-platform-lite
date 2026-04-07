import secrets
import uuid
from math import ceil
from typing import Any

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.config import settings
from app.models import Attempt, Quiz, Result, SessionToken, User
from app.schemas.auth import (
    PaidRegistrationRequest,
    RegisterRequest,
    UserAccessStatus,
    UserRole,
    UserSession,
)
from app.schemas.platform import (
    AdminParticipationPage,
    AdminParticipationRecord,
    AdminQuizPage,
    AdminQuizPerformancePage,
    AdminQuizPerformanceRecord,
    AdminStudentPage,
    AdminStudentRecord,
    AdminSummaryStats,
    AttemptEnvelope,
    PaginationMeta,
    QuizCatalogItem,
)
from app.schemas.quiz import QuizDefinition, QuizLifecycleStatus
from app.schemas.submission import QuizResultResponse
from app.services.auth import hash_password, hash_session_token, normalize_email
from app.services.db import DatabaseSessionFactory
from app.services.excel import slugify
from app.utils.time import epoch_to_local_iso, local_timezone_name, utc_now_epoch


class PlatformStore:
    def __init__(self, session_factory: DatabaseSessionFactory):
        self.session_factory = session_factory

    async def create_user(
        self,
        payload: RegisterRequest,
        role: UserRole = UserRole.STUDENT,
        access_status: UserAccessStatus = UserAccessStatus.ACTIVE,
    ) -> UserSession:
        user_id = uuid.uuid4().hex
        email = normalize_email(payload.email)
        password_hash = hash_password(payload.password)
        created_at = utc_now_epoch()

        async with self.session_factory.begin() as session:
            existing = await session.execute(select(User.user_id).where(User.email == email))
            if existing.first():
                raise ValueError("An account with this email already exists")

            user = User(
                user_id=user_id,
                email=email,
                full_name=payload.full_name.strip(),
                password_hash=password_hash,
                role=role.value,
                access_status=access_status.value,
                created_at=created_at,
            )
            session.add(user)
            return UserSession(
                user_id=user.user_id,
                email=user.email,
                full_name=user.full_name,
                role=UserRole(user.role),
                access_status=UserAccessStatus(user.access_status),
            )

    async def create_paid_student_registration(self, payload: PaidRegistrationRequest) -> UserSession:
        user_id = uuid.uuid4().hex
        email = normalize_email(payload.email)
        created_at = utc_now_epoch()

        async with self.session_factory.begin() as session:
            existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if existing is not None:
                if (
                    existing.role == UserRole.STUDENT.value
                    and existing.access_status == UserAccessStatus.PENDING_CREDENTIALS.value
                ):
                    raise ValueError(
                        "A paid registration with this email is already recorded. "
                        "Please wait for the exam date and login credentials by email."
                    )
                raise ValueError(
                    "An account with this email already exists. "
                    "Use the sign in page if you have already received your login credentials."
                )

            user = User(
                user_id=user_id,
                email=email,
                full_name=payload.full_name.strip(),
                password_hash=hash_password(secrets.token_urlsafe(24)),
                role=UserRole.STUDENT.value,
                access_status=UserAccessStatus.PENDING_CREDENTIALS.value,
                created_at=created_at,
            )
            session.add(user)
            return UserSession(
                user_id=user.user_id,
                email=user.email,
                full_name=user.full_name,
                role=UserRole(user.role),
                access_status=UserAccessStatus(user.access_status),
            )

    async def authenticate_user(self, email: str) -> dict[str, Any] | None:
        normalized_email = normalize_email(email)
        async with self.session_factory() as session:
            user = (
                await session.execute(select(User).where(User.email == normalized_email))
            ).scalar_one_or_none()
            if user is None:
                return None
            return {
                "user_id": user.user_id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "access_status": user.access_status,
                "password_hash": user.password_hash,
            }

    async def create_session(self, user_id: str, token: str) -> int:
        expires_at = utc_now_epoch() + settings.session_ttl_seconds
        async with self.session_factory.begin() as session:
            session.add(
                SessionToken(
                    token_hash=hash_session_token(token),
                    user_id=user_id,
                    expires_at=expires_at,
                    created_at=utc_now_epoch(),
                )
            )
        return expires_at

    async def delete_session(self, token: str) -> None:
        token_hash = hash_session_token(token)
        async with self.session_factory.begin() as session:
            record = await session.get(SessionToken, token_hash)
            if record is not None:
                await session.delete(record)

    async def get_user_by_session(self, token: str) -> UserSession | None:
        now = utc_now_epoch()
        token_hash = hash_session_token(token)
        async with self.session_factory.begin() as session:
            record = await session.get(SessionToken, token_hash)
            if record is None:
                return None
            if record.expires_at <= now:
                await session.delete(record)
                return None
            user = await session.get(User, record.user_id)
            if user is None:
                return None
            return UserSession(
                user_id=user.user_id,
                email=user.email,
                full_name=user.full_name,
                role=UserRole(user.role),
                access_status=UserAccessStatus(user.access_status),
            )

    async def create_quiz(
        self,
        quiz: QuizDefinition,
        *,
        created_by: str,
        source_filename: str | None,
        lifecycle_status: QuizLifecycleStatus = "published",
    ) -> dict[str, Any]:
        quiz_id = await self._unique_quiz_id(quiz.quiz_id or slugify(quiz.title))
        payload = quiz.model_copy(update={"quiz_id": quiz_id, "version": settings.default_quiz_version})
        availability_start_at, availability_end_at = self._validated_quiz_window(
            payload.availability_start_at,
            payload.availability_end_at,
        )
        payload = payload.model_copy(
            update={
                "availability_start_at": availability_start_at,
                "availability_end_at": availability_end_at,
            }
        )
        created_at = utc_now_epoch()

        async with self.session_factory.begin() as session:
            entity = Quiz(
                quiz_id=quiz_id,
                version=settings.default_quiz_version,
                title=payload.title,
                description=payload.description,
                duration_seconds=payload.duration_seconds or 1800,
                is_published=lifecycle_status == "published",
                lifecycle_status=lifecycle_status,
                availability_start_at=availability_start_at,
                availability_end_at=availability_end_at,
                created_by=created_by,
                source_filename=source_filename,
                raw_data=self._serialize_quiz_raw_data(payload, created_at=created_at),
                created_at=created_at,
            )
            session.add(entity)
            return {
                "quiz_id": entity.quiz_id,
                "title": entity.title,
                "version": entity.version,
                "created_at": entity.created_at,
                "lifecycle_status": entity.lifecycle_status,
                "availability_start_at": entity.availability_start_at,
                "availability_end_at": entity.availability_end_at,
                "raw_data": payload,
            }

    async def update_quiz_settings(
        self,
        quiz_id: str,
        *,
        lifecycle_status: QuizLifecycleStatus,
        availability_start_at: int | None,
        availability_end_at: int | None,
    ) -> dict[str, Any]:
        validated_start, validated_end = self._validated_quiz_window(availability_start_at, availability_end_at)

        async with self.session_factory.begin() as session:
            quiz = await session.get(Quiz, quiz_id)
            if quiz is None:
                raise LookupError("Quiz not found")

            raw_payload = dict(quiz.raw_data or {})
            raw_payload["availability_start_at"] = validated_start
            raw_payload["availability_end_at"] = validated_end
            quiz_payload = QuizDefinition.model_validate(raw_payload).model_copy(
                update={
                    "availability_start_at": validated_start,
                    "availability_end_at": validated_end,
                }
            )

            quiz.lifecycle_status = lifecycle_status
            quiz.is_published = lifecycle_status == "published"
            quiz.availability_start_at = validated_start
            quiz.availability_end_at = validated_end
            quiz.raw_data = self._serialize_quiz_raw_data(quiz_payload, created_at=quiz.created_at)

            return {
                "quiz_id": quiz.quiz_id,
                "title": quiz.title,
                "lifecycle_status": quiz.lifecycle_status,
                "availability_start_at": quiz.availability_start_at,
                "availability_end_at": quiz.availability_end_at,
                "raw_data": quiz_payload,
            }

    async def delete_quiz(self, quiz_id: str) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            quiz = await session.get(Quiz, quiz_id)
            if quiz is None:
                raise LookupError("Quiz not found")
            title = quiz.title
            await session.execute(delete(Result).where(Result.quiz_id == quiz_id))
            await session.delete(quiz)
            return {"quiz_id": quiz.quiz_id, "title": title}

    async def list_quiz_catalog_page(
        self,
        *,
        page: int,
        page_size: int,
        query: str | None = None,
        lifecycle_status: str | None = None,
    ) -> AdminQuizPage:
        search_term = self._normalized_query(query)
        filters = self._quiz_search_filters(search_term)
        if lifecycle_status:
            filters.append(Quiz.lifecycle_status == lifecycle_status)

        async with self.session_factory() as session:
            total_items = int(await session.scalar(select(func.count()).select_from(Quiz).where(*filters)) or 0)
            pagination = self._pagination_meta(total_items, page, page_size)
            quizzes = (
                (
                    await session.execute(
                        select(Quiz)
                        .where(*filters)
                        .order_by(Quiz.created_at.desc())
                        .limit(pagination.page_size)
                        .offset((pagination.page - 1) * pagination.page_size)
                    )
                )
                .scalars()
                .all()
            )
            now = utc_now_epoch()
            items = [self._quiz_to_catalog_item(quiz, now=now) for quiz in quizzes]
            return AdminQuizPage(items=items, pagination=pagination)

    async def get_admin_quiz_catalog_item(self, quiz_id: str) -> QuizCatalogItem | None:
        async with self.session_factory() as session:
            quiz = await session.get(Quiz, quiz_id)
            if quiz is None:
                return None
            return self._quiz_to_catalog_item(quiz, now=utc_now_epoch())

    async def list_quizzes_for_admin(self) -> list[QuizCatalogItem]:
        async with self.session_factory() as session:
            quizzes = (await session.execute(select(Quiz).order_by(Quiz.created_at.desc()))).scalars().all()
            now = utc_now_epoch()
            return [self._quiz_to_catalog_item(quiz, now=now) for quiz in quizzes]

    async def get_admin_summary(self) -> AdminSummaryStats:
        async with self.session_factory() as session:
            total_quizzes = int(await session.scalar(select(func.count()).select_from(Quiz)) or 0)
            total_students = int(
                await session.scalar(select(func.count()).select_from(User).where(User.role == UserRole.STUDENT.value))
                or 0
            )
            total_attempts = int(await session.scalar(select(func.count()).select_from(Attempt)) or 0)
            scored_attempts = int(
                await session.scalar(select(func.count()).select_from(Attempt).where(Attempt.status == "scored")) or 0
            )
            return AdminSummaryStats(
                total_quizzes=total_quizzes,
                total_students=total_students,
                total_attempts=total_attempts,
                scored_attempts=scored_attempts,
            )

    async def list_registered_students(
        self,
        *,
        page: int,
        page_size: int,
        query: str | None = None,
    ) -> AdminStudentPage:
        search_term = self._normalized_query(query)
        filters = [User.role == UserRole.STUDENT.value]
        if search_term:
            pattern = f"%{search_term}%"
            filters.append(or_(User.full_name.ilike(pattern), User.email.ilike(pattern)))

        started_expr = func.count(Attempt.attempt_id)
        submitted_expr = func.coalesce(
            func.sum(case((Attempt.status.in_(("submitted", "scored")), 1), else_=0)),
            0,
        )
        scored_expr = func.coalesce(
            func.sum(case((Attempt.status == "scored", 1), else_=0)),
            0,
        )

        async with self.session_factory() as session:
            total_items = int(await session.scalar(select(func.count()).select_from(User).where(*filters)) or 0)
            pagination = self._pagination_meta(total_items, page, page_size)
            rows = (
                await session.execute(
                    select(
                        User.user_id,
                        User.full_name,
                        User.email,
                        User.access_status,
                        User.created_at,
                        started_expr.label("quizzes_started"),
                        submitted_expr.label("quizzes_submitted"),
                        scored_expr.label("quizzes_scored"),
                        func.avg(Result.percentage).label("average_percentage"),
                        func.max(Result.percentage).label("best_percentage"),
                    )
                    .select_from(User)
                    .outerjoin(Attempt, Attempt.user_id == User.user_id)
                    .outerjoin(Result, and_(Result.user_id == User.user_id, Result.quiz_id == Attempt.quiz_id))
                    .where(*filters)
                    .group_by(User.user_id, User.full_name, User.email, User.access_status, User.created_at)
                    .order_by(User.created_at.desc())
                    .limit(pagination.page_size)
                    .offset((pagination.page - 1) * pagination.page_size)
                )
            ).all()
            items = [AdminStudentRecord.model_validate(dict(row._mapping)) for row in rows]
            return AdminStudentPage(items=items, pagination=pagination)

    async def get_student_admin_record(self, user_id: str) -> AdminStudentRecord | None:
        started_expr = func.count(Attempt.attempt_id)
        submitted_expr = func.coalesce(
            func.sum(case((Attempt.status.in_(("submitted", "scored")), 1), else_=0)),
            0,
        )
        scored_expr = func.coalesce(
            func.sum(case((Attempt.status == "scored", 1), else_=0)),
            0,
        )

        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(
                        User.user_id,
                        User.full_name,
                        User.email,
                        User.access_status,
                        User.created_at,
                        started_expr.label("quizzes_started"),
                        submitted_expr.label("quizzes_submitted"),
                        scored_expr.label("quizzes_scored"),
                        func.avg(Result.percentage).label("average_percentage"),
                        func.max(Result.percentage).label("best_percentage"),
                    )
                    .select_from(User)
                    .outerjoin(Attempt, Attempt.user_id == User.user_id)
                    .outerjoin(Result, and_(Result.user_id == User.user_id, Result.quiz_id == Attempt.quiz_id))
                    .where(User.role == UserRole.STUDENT.value, User.user_id == user_id)
                    .group_by(User.user_id, User.full_name, User.email, User.access_status, User.created_at)
                )
            ).first()
            if row is None:
                return None
            return AdminStudentRecord.model_validate(dict(row._mapping))

    async def list_participation_records(
        self,
        *,
        page: int,
        page_size: int,
        query: str | None = None,
        quiz_id: str | None = None,
        attempt_status: str | None = None,
        user_id: str | None = None,
    ) -> AdminParticipationPage:
        search_term = self._normalized_query(query)
        filters: list[Any] = []
        if search_term:
            pattern = f"%{search_term}%"
            filters.append(
                or_(
                    User.full_name.ilike(pattern),
                    User.email.ilike(pattern),
                    Quiz.title.ilike(pattern),
                    Attempt.quiz_id.ilike(pattern),
                )
            )
        if quiz_id:
            filters.append(Attempt.quiz_id == quiz_id)
        if attempt_status:
            filters.append(Attempt.status == attempt_status)
        if user_id:
            filters.append(Attempt.user_id == user_id)

        async with self.session_factory() as session:
            total_items = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Attempt)
                    .join(User, User.user_id == Attempt.user_id)
                    .join(Quiz, Quiz.quiz_id == Attempt.quiz_id)
                    .where(*filters)
                )
                or 0
            )
            pagination = self._pagination_meta(total_items, page, page_size)
            rows = (
                await session.execute(
                    select(
                        Attempt.attempt_id,
                        Attempt.quiz_id,
                        Quiz.title.label("quiz_title"),
                        Attempt.user_id,
                        User.full_name.label("student_name"),
                        User.email.label("student_email"),
                        Attempt.status.label("attempt_status"),
                        Attempt.started_at,
                        Attempt.expires_at,
                        Attempt.submitted_at,
                        Result.score,
                        Result.total,
                        Result.percentage,
                    )
                    .select_from(Attempt)
                    .join(User, User.user_id == Attempt.user_id)
                    .join(Quiz, Quiz.quiz_id == Attempt.quiz_id)
                    .outerjoin(Result, and_(Result.quiz_id == Attempt.quiz_id, Result.user_id == Attempt.user_id))
                    .where(*filters)
                    .order_by(Attempt.started_at.desc())
                    .limit(pagination.page_size)
                    .offset((pagination.page - 1) * pagination.page_size)
                )
            ).all()
            items = [AdminParticipationRecord.model_validate(dict(row._mapping)) for row in rows]
            return AdminParticipationPage(items=items, pagination=pagination)

    async def list_quiz_performance_page(
        self,
        *,
        page: int,
        page_size: int,
        query: str | None = None,
    ) -> AdminQuizPerformancePage:
        search_term = self._normalized_query(query)
        filters = self._quiz_search_filters(search_term)

        participant_expr = func.count(Attempt.attempt_id)
        submitted_expr = func.coalesce(
            func.sum(case((Attempt.status.in_(("submitted", "scored")), 1), else_=0)),
            0,
        )
        scored_expr = func.coalesce(
            func.sum(case((Attempt.status == "scored", 1), else_=0)),
            0,
        )

        async with self.session_factory() as session:
            total_items = int(await session.scalar(select(func.count()).select_from(Quiz).where(*filters)) or 0)
            pagination = self._pagination_meta(total_items, page, page_size)
            rows = (
                await session.execute(
                    select(
                        Quiz.quiz_id,
                        Quiz.title,
                        Quiz.duration_seconds,
                        participant_expr.label("participant_count"),
                        submitted_expr.label("submitted_count"),
                        scored_expr.label("scored_count"),
                        func.avg(Result.percentage).label("average_percentage"),
                        func.max(Result.percentage).label("top_percentage"),
                    )
                    .select_from(Quiz)
                    .outerjoin(Attempt, Attempt.quiz_id == Quiz.quiz_id)
                    .outerjoin(Result, and_(Result.quiz_id == Quiz.quiz_id, Result.user_id == Attempt.user_id))
                    .where(*filters)
                    .group_by(Quiz.quiz_id, Quiz.title, Quiz.duration_seconds, Quiz.created_at)
                    .order_by(Quiz.created_at.desc())
                    .limit(pagination.page_size)
                    .offset((pagination.page - 1) * pagination.page_size)
                )
            ).all()
            items = [AdminQuizPerformanceRecord.model_validate(dict(row._mapping)) for row in rows]
            return AdminQuizPerformancePage(items=items, pagination=pagination)

    async def list_quiz_performance(self) -> list[AdminQuizPerformanceRecord]:
        participant_expr = func.count(Attempt.attempt_id)
        submitted_expr = func.coalesce(
            func.sum(case((Attempt.status.in_(("submitted", "scored")), 1), else_=0)),
            0,
        )
        scored_expr = func.coalesce(
            func.sum(case((Attempt.status == "scored", 1), else_=0)),
            0,
        )

        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        Quiz.quiz_id,
                        Quiz.title,
                        Quiz.duration_seconds,
                        participant_expr.label("participant_count"),
                        submitted_expr.label("submitted_count"),
                        scored_expr.label("scored_count"),
                        func.avg(Result.percentage).label("average_percentage"),
                        func.max(Result.percentage).label("top_percentage"),
                    )
                    .select_from(Quiz)
                    .outerjoin(Attempt, Attempt.quiz_id == Quiz.quiz_id)
                    .outerjoin(Result, and_(Result.quiz_id == Quiz.quiz_id, Result.user_id == Attempt.user_id))
                    .group_by(Quiz.quiz_id, Quiz.title, Quiz.duration_seconds, Quiz.created_at)
                    .order_by(Quiz.created_at.desc())
                )
            ).all()
            return [AdminQuizPerformanceRecord.model_validate(dict(row._mapping)) for row in rows]

    async def get_quiz_performance_record(self, quiz_id: str) -> AdminQuizPerformanceRecord | None:
        participant_expr = func.count(Attempt.attempt_id)
        submitted_expr = func.coalesce(
            func.sum(case((Attempt.status.in_(("submitted", "scored")), 1), else_=0)),
            0,
        )
        scored_expr = func.coalesce(
            func.sum(case((Attempt.status == "scored", 1), else_=0)),
            0,
        )

        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(
                        Quiz.quiz_id,
                        Quiz.title,
                        Quiz.duration_seconds,
                        participant_expr.label("participant_count"),
                        submitted_expr.label("submitted_count"),
                        scored_expr.label("scored_count"),
                        func.avg(Result.percentage).label("average_percentage"),
                        func.max(Result.percentage).label("top_percentage"),
                    )
                    .select_from(Quiz)
                    .outerjoin(Attempt, Attempt.quiz_id == Quiz.quiz_id)
                    .outerjoin(Result, and_(Result.quiz_id == Quiz.quiz_id, Result.user_id == Attempt.user_id))
                    .where(Quiz.quiz_id == quiz_id)
                    .group_by(Quiz.quiz_id, Quiz.title, Quiz.duration_seconds, Quiz.created_at)
                )
            ).first()
            if row is None:
                return None
            return AdminQuizPerformanceRecord.model_validate(dict(row._mapping))

    async def list_quizzes_for_student(self, user_id: str) -> list[QuizCatalogItem]:
        student_attempt = aliased(Attempt)
        async with self.session_factory.begin() as session:
            rows = (
                await session.execute(
                    select(Quiz, student_attempt)
                    .outerjoin(
                        student_attempt,
                        and_(student_attempt.quiz_id == Quiz.quiz_id, student_attempt.user_id == user_id),
                    )
                    .where(or_(Quiz.lifecycle_status == "published", student_attempt.attempt_id.is_not(None)))
                    .order_by(Quiz.created_at.desc())
                )
            ).all()
            now = utc_now_epoch()
            items: list[QuizCatalogItem] = []
            for quiz, attempt in rows:
                if attempt is not None:
                    self._normalize_attempt_entity(attempt, now)
                items.append(self._quiz_to_catalog_item(quiz, attempt=attempt, now=now))
            return items

    async def get_quiz_metadata(self, quiz_id: str) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            quiz = (
                await session.execute(
                    select(Quiz).where(Quiz.quiz_id == quiz_id, Quiz.lifecycle_status == "published")
                )
            ).scalar_one_or_none()
            if quiz is None:
                return None
            return {
                "quiz_id": quiz.quiz_id,
                "title": quiz.title,
                "description": quiz.description,
                "duration_seconds": quiz.duration_seconds,
                "raw_data": quiz.raw_data,
                "lifecycle_status": quiz.lifecycle_status,
                "availability_start_at": quiz.availability_start_at,
                "availability_end_at": quiz.availability_end_at,
            }

    async def get_quiz_definition(self, quiz_id: str) -> QuizDefinition | None:
        async with self.session_factory() as session:
            quiz = await session.get(Quiz, quiz_id)
            if quiz is None or quiz.raw_data is None:
                return None
            return QuizDefinition.model_validate(quiz.raw_data)

    async def start_attempt(self, quiz_id: str, user_id: str) -> AttemptEnvelope:
        now = utc_now_epoch()
        try:
            async with self.session_factory.begin() as session:
                quiz = await session.get(Quiz, quiz_id)
                if quiz is None:
                    raise LookupError("Quiz not found")
                if quiz.lifecycle_status != "published":
                    raise LookupError("Quiz not found")

                existing = (
                    await session.execute(
                        select(Attempt)
                        .where(Attempt.quiz_id == quiz_id, Attempt.user_id == user_id)
                        .with_for_update(skip_locked=False)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    self._normalize_attempt_entity(existing, now)
                    return self._attempt_envelope(existing, now)

                availability_status = self._quiz_availability_status(
                    {
                        "lifecycle_status": quiz.lifecycle_status,
                        "availability_start_at": quiz.availability_start_at,
                        "availability_end_at": quiz.availability_end_at,
                    },
                    now,
                )
                if availability_status == "upcoming":
                    raise RuntimeError("This test window has not opened yet")
                if availability_status == "closed":
                    raise TimeoutError("This test window has closed")
                if availability_status in {"draft", "archived"}:
                    raise LookupError("Quiz not found")

                expires_at = now + int(quiz.duration_seconds)
                if quiz.availability_end_at is not None:
                    expires_at = min(expires_at, int(quiz.availability_end_at))

                attempt = Attempt(
                    attempt_id=uuid.uuid4().hex,
                    quiz_id=quiz_id,
                    user_id=user_id,
                    started_at=now,
                    expires_at=expires_at,
                    status="active",
                    submitted_at=None,
                    answers=None,
                )
                session.add(attempt)
                await session.flush()
                return self._attempt_envelope(attempt, now)
        except IntegrityError:
            async with self.session_factory.begin() as session:
                existing = (
                    await session.execute(
                        select(Attempt).where(Attempt.quiz_id == quiz_id, Attempt.user_id == user_id)
                    )
                ).scalar_one_or_none()
                if existing is None:
                    raise
                now = utc_now_epoch()
                self._normalize_attempt_entity(existing, now)
                return self._attempt_envelope(existing, now)

    async def get_attempt(self, attempt_id: str, user_id: str) -> dict[str, Any]:
        now = utc_now_epoch()
        async with self.session_factory.begin() as session:
            row = (
                await session.execute(
                    select(Attempt, Quiz)
                    .join(Quiz, Quiz.quiz_id == Attempt.quiz_id)
                    .where(Attempt.attempt_id == attempt_id, Attempt.user_id == user_id)
                )
            ).first()
            if row is None:
                raise LookupError("Attempt not found")
            attempt, quiz = row
            self._normalize_attempt_entity(attempt, now)
            return {
                "attempt_id": attempt.attempt_id,
                "quiz_id": attempt.quiz_id,
                "user_id": attempt.user_id,
                "status": attempt.status,
                "started_at": attempt.started_at,
                "expires_at": attempt.expires_at,
                "submitted_at": attempt.submitted_at,
                "answers": attempt.answers or [],
                "title": quiz.title,
                "description": quiz.description,
                "duration_seconds": quiz.duration_seconds,
            }

    async def load_attempt_answers(self, attempt_id: str, user_id: str) -> dict[str, str]:
        async with self.session_factory() as session:
            attempt = (
                await session.execute(
                    select(Attempt).where(Attempt.attempt_id == attempt_id, Attempt.user_id == user_id)
                )
            ).scalar_one_or_none()
            if attempt is None:
                raise LookupError("Attempt not found")
            return self._answers_to_map(attempt.answers)

    async def autosave_attempt_answers(
        self,
        attempt_id: str,
        user_id: str,
        answers: list[dict[str, str]],
        *,
        saved_at: int,
    ) -> dict[str, Any]:
        now = utc_now_epoch()
        incoming_answers = self._answers_to_map(answers)
        async with self.session_factory.begin() as session:
            attempt = (
                await session.execute(
                    select(Attempt)
                    .where(Attempt.attempt_id == attempt_id, Attempt.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if attempt is None:
                raise LookupError("Attempt not found")

            self._normalize_attempt_entity(attempt, now)
            if attempt.status == "expired":
                raise TimeoutError("Attempt time window has expired")
            if attempt.status in {"submitted", "scored"}:
                raise RuntimeError("Attempt has already been submitted")

            # Merge with existing saved answers; incoming answers override while
            # preserving answers for other questions to avoid multi-tab data loss.
            existing_answers = self._answers_to_map(attempt.answers)
            merged_answers = {**existing_answers, **incoming_answers}

            attempt.answers = self._map_to_answers_list(merged_answers)
            await session.flush()
            return {
                "attempt": self._attempt_record(attempt),
                "saved_answer_count": len(merged_answers),
                "saved_at": saved_at,
            }

    async def prepare_attempt_submission(self, attempt_id: str, user_id: str) -> dict[str, Any]:
        now = utc_now_epoch()
        async with self.session_factory.begin() as session:
            # Lock the row to serialize concurrent submission attempts.
            attempt = (
                await session.execute(
                    select(Attempt)
                    .where(Attempt.attempt_id == attempt_id, Attempt.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if attempt is None:
                raise LookupError("Attempt not found")

            self._normalize_attempt_entity(attempt, now)
            if attempt.status == "expired":
                raise TimeoutError("Attempt time window has expired")
            if attempt.status in {"submitted", "scored"}:
                raise RuntimeError("Attempt has already been submitted")
            return self._attempt_record(attempt)

    async def finalize_attempt_submission(
        self,
        attempt_id: str,
        user_id: str,
        answers: list[dict[str, str]],
        submitted_at: int,
    ) -> dict[str, Any]:
        now = utc_now_epoch()
        async with self.session_factory.begin() as session:
            attempt = (
                await session.execute(
                    select(Attempt)
                    .where(Attempt.attempt_id == attempt_id, Attempt.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if attempt is None:
                raise LookupError("Attempt not found")

            self._normalize_attempt_entity(attempt, now)
            if attempt.status in {"submitted", "scored"}:
                raise RuntimeError("Attempt has already been submitted")
            if attempt.status == "expired":
                raise TimeoutError("Attempt time window has expired")

            attempt.status = "submitted"
            attempt.submitted_at = submitted_at
            attempt.answers = answers
            await session.flush()
            return self._attempt_record(attempt)

    async def reopen_attempt_submission(self, attempt_id: str, user_id: str) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            attempt = (
                await session.execute(
                    select(Attempt)
                    .where(Attempt.attempt_id == attempt_id, Attempt.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if attempt is None:
                raise LookupError("Attempt not found")
            if attempt.status == "submitted":
                attempt.status = "active"
                attempt.submitted_at = None
            await session.flush()
            return self._attempt_record(attempt)

    async def mark_attempt_scored(self, attempt_id: str) -> None:
        async with self.session_factory.begin() as session:
            attempt = await session.get(Attempt, attempt_id)
            if attempt is not None and attempt.status != "expired":
                attempt.status = "scored"

    async def save_result(
        self,
        *,
        quiz_id: str,
        user_id: str,
        score: int,
        total: int,
        percentage: float,
        evaluated_at: int,
        submission_id: str | None = None,
        attempt_id: str | None = None,
    ) -> QuizResultResponse:
        payload = {
            "status": "completed",
            "quiz_id": quiz_id,
            "user_id": user_id,
            "score": score,
            "total": total,
            "percentage": percentage,
            "evaluated_at": evaluated_at,
            "submission_id": submission_id,
            "attempt_id": attempt_id,
        }

        async with self.session_factory.begin() as session:
            record = (
                await session.execute(
                    select(Result).where(Result.quiz_id == quiz_id, Result.user_id == user_id)
                )
            ).scalar_one_or_none()
            if record is None:
                record = Result(
                    quiz_id=quiz_id,
                    user_id=user_id,
                    attempt_id=attempt_id,
                    score=score,
                    total=total,
                    percentage=percentage,
                    evaluated_at=evaluated_at,
                    submission_id=submission_id,
                    raw_data=payload,
                )
                session.add(record)
            else:
                record.attempt_id = attempt_id
                record.score = score
                record.total = total
                record.percentage = percentage
                record.evaluated_at = evaluated_at
                record.submission_id = submission_id
                record.raw_data = payload

            if attempt_id:
                attempt = await session.get(Attempt, attempt_id)
                if attempt is not None and attempt.status != "expired":
                    attempt.status = "scored"

        return QuizResultResponse.model_validate(payload)

    async def get_result(self, quiz_id: str, user_id: str) -> QuizResultResponse | None:
        async with self.session_factory() as session:
            record = (
                await session.execute(
                    select(Result).where(Result.quiz_id == quiz_id, Result.user_id == user_id)
                )
            ).scalar_one_or_none()
            if record is None:
                return None

            payload = dict(record.raw_data or {})
            payload.setdefault("status", "completed")
            payload.setdefault("quiz_id", record.quiz_id)
            payload.setdefault("user_id", record.user_id)
            payload.setdefault("score", record.score)
            payload.setdefault("total", record.total)
            payload.setdefault("percentage", record.percentage)
            payload.setdefault("evaluated_at", record.evaluated_at)
            payload.setdefault("submission_id", record.submission_id)
            payload.setdefault("attempt_id", record.attempt_id)
            return QuizResultResponse.model_validate(payload)

    async def _set_attempt_status(self, attempt_id: str, status: str) -> None:
        async with self.session_factory.begin() as session:
            attempt = await session.get(Attempt, attempt_id)
            if attempt is not None:
                attempt.status = status

    def _normalize_attempt_entity(self, attempt: Attempt, now: int) -> None:
        if attempt.status == "active" and attempt.expires_at <= now:
            attempt.status = "expired"

    def _attempt_envelope(self, attempt: Attempt, now: int) -> AttemptEnvelope:
        return AttemptEnvelope(
            attempt_id=attempt.attempt_id,
            quiz_id=attempt.quiz_id,
            status=attempt.status,
            started_at=attempt.started_at,
            expires_at=attempt.expires_at,
            submitted_at=attempt.submitted_at,
            remaining_seconds=max(0, attempt.expires_at - now) if attempt.status == "active" else 0,
        )

    def _attempt_record(self, attempt: Attempt) -> dict[str, Any]:
        return {
            "attempt_id": attempt.attempt_id,
            "quiz_id": attempt.quiz_id,
            "status": attempt.status,
            "started_at": attempt.started_at,
            "expires_at": attempt.expires_at,
            "submitted_at": attempt.submitted_at,
            "answers": attempt.answers or [],
        }

    async def _unique_quiz_id(self, requested_quiz_id: str) -> str:
        candidate = slugify(requested_quiz_id)
        async with self.session_factory() as session:
            existing = await session.get(Quiz, candidate)
            if existing is None:
                return candidate
        return f"{candidate}-{utc_now_epoch()}"

    def _pagination_meta(self, total_items: int, page: int, page_size: int) -> PaginationMeta:
        normalized_page_size = max(int(page_size), 1)
        total_pages = max(1, ceil(total_items / normalized_page_size)) if total_items else 1
        normalized_page = min(max(int(page), 1), total_pages)
        start_item = 0 if total_items == 0 else ((normalized_page - 1) * normalized_page_size) + 1
        end_item = min(normalized_page * normalized_page_size, total_items)
        return PaginationMeta(
            page=normalized_page,
            page_size=normalized_page_size,
            total_items=total_items,
            total_pages=total_pages,
            has_prev=normalized_page > 1,
            has_next=normalized_page < total_pages,
            start_item=start_item,
            end_item=end_item,
        )

    def _normalized_query(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _answers_to_map(self, answers: Any) -> dict[str, str]:
        if not isinstance(answers, list):
            return {}
        answer_map: dict[str, str] = {}
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            question_id = str(answer.get("question_id", "")).strip()
            choice = str(answer.get("choice", "")).strip()
            if question_id and choice:
                answer_map[question_id] = choice
        return answer_map

    def _map_to_answers_list(self, answer_map: dict[str, str]) -> list[dict[str, str]]:
        return [{"question_id": question_id, "choice": choice} for question_id, choice in answer_map.items()]

    def _validated_quiz_window(
        self,
        availability_start_at: int | None,
        availability_end_at: int | None,
    ) -> tuple[int | None, int | None]:
        if (
            availability_start_at is not None
            and availability_end_at is not None
            and int(availability_end_at) <= int(availability_start_at)
        ):
            raise ValueError("Availability end must be later than availability start")
        return (
            int(availability_start_at) if availability_start_at is not None else None,
            int(availability_end_at) if availability_end_at is not None else None,
        )

    def _serialize_quiz_raw_data(self, quiz: QuizDefinition, *, created_at: int | None) -> dict[str, Any]:
        raw_payload = quiz.model_dump(mode="json")
        raw_payload["local_time_context"] = {
            "timezone": local_timezone_name(),
            "created_at": epoch_to_local_iso(created_at),
            "availability_start_at": epoch_to_local_iso(quiz.availability_start_at),
            "availability_end_at": epoch_to_local_iso(quiz.availability_end_at),
        }
        return raw_payload

    def _quiz_availability_status(self, row: dict[str, Any], now: int) -> str:
        lifecycle_status = str(row.get("lifecycle_status") or "published")
        if lifecycle_status != "published":
            return lifecycle_status

        availability_start_at = row.get("availability_start_at")
        availability_end_at = row.get("availability_end_at")
        if availability_start_at is not None and int(availability_start_at) > now:
            return "upcoming"
        if availability_end_at is not None and int(availability_end_at) <= now:
            return "closed"
        return "available"

    def _quiz_search_filters(self, search_term: str | None) -> list[Any]:
        filters: list[Any] = []
        if search_term:
            pattern = f"%{search_term}%"
            filters.append(
                or_(
                    Quiz.quiz_id.ilike(pattern),
                    Quiz.title.ilike(pattern),
                    func.coalesce(Quiz.description, "").ilike(pattern),
                )
            )
        return filters

    def _quiz_to_catalog_item(self, quiz: Quiz, attempt: Attempt | None = None, *, now: int) -> QuizCatalogItem:
        item = {
            "quiz_id": quiz.quiz_id,
            "title": quiz.title,
            "description": quiz.description,
            "duration_seconds": quiz.duration_seconds,
            "created_at": quiz.created_at,
            "lifecycle_status": quiz.lifecycle_status,
            "availability_start_at": quiz.availability_start_at,
            "availability_end_at": quiz.availability_end_at,
            "availability_status": self._quiz_availability_status(
                {
                    "lifecycle_status": quiz.lifecycle_status,
                    "availability_start_at": quiz.availability_start_at,
                    "availability_end_at": quiz.availability_end_at,
                },
                now,
            ),
        }
        if attempt is not None:
            item.update(
                {
                    "attempt_id": attempt.attempt_id,
                    "attempt_status": attempt.status,
                    "expires_at": attempt.expires_at,
                    "submitted_at": attempt.submitted_at,
                }
            )
        return QuizCatalogItem.model_validate(item)
