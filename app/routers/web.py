import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.dependencies import (
    get_current_admin,
    get_current_user,
    get_optional_admin_user,
    get_optional_current_user,
    get_store,
)
from app.schemas.auth import LoginRequest, RegisterRequest, UserRole, UserSession
from app.schemas.platform import PaginationMeta, StudentAttemptView
from app.schemas.quiz import PublicQuizDefinition, QuizDefinition, QuizLifecycleStatus
from app.schemas.submission import (
    Answer,
    AttemptAutosaveRequest,
    AttemptAutosaveResponse,
    AttemptSubmissionRequest,
    ProcessingResultResponse,
)
from app.services.auth import new_session_token, verify_password
from app.services.excel import parse_quiz_workbook
from app.services.scoring import calculate_score
from app.utils.csrf import get_csrf_token
from app.utils.rate_limit import rate_limit_login, rate_limit_register
from app.utils.time import coerce_epoch, local_timezone, local_timezone_name, utc_now_epoch

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
logger = logging.getLogger(__name__)
ADMIN_ATTEMPT_STATUSES = ("active", "submitted", "scored", "expired")
ADMIN_QUIZ_LIFECYCLE_STATUSES: tuple[QuizLifecycleStatus, ...] = ("draft", "published", "archived")
ADMIN_OVERVIEW_PREVIEW_SIZE = 5
ADMIN_NAV_ITEMS = (
    {"key": "overview", "label": "Overview", "href": "/app/admin"},
    {"key": "quizzes", "label": "Quizzes", "href": "/app/admin/quizzes"},
    {"key": "students", "label": "Students", "href": "/app/admin/students"},
    {"key": "attempts", "label": "Attempts", "href": "/app/admin/attempts"},
)


def _format_epoch(value: int | None) -> str:
    if value in (None, ""):
        return "—"
    try:
        instant = datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return str(value)
    local = instant.astimezone(local_timezone())
    return local.strftime("%b %d, %Y · %H:%M %Z")


def _format_duration(value: int | None) -> str:
    if value in (None, ""):
        return "—"
    try:
        total_seconds = max(int(value), 0)
    except (TypeError, ValueError):
        return str(value)

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds and not hours:
        parts.append(f"{seconds}s")
    return " ".join(parts) or "0s"


def _format_datetime_local(value: int | None) -> str:
    if value in (None, ""):
        return ""
    try:
        instant = datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return ""
    local = instant.astimezone(local_timezone())
    return local.strftime("%Y-%m-%dT%H:%M")


def _query_url(request: Request, **updates: object) -> str:
    params = {
        key: value
        for key, value in request.query_params.items()
        if key not in {"message", "message_type"}
    }
    for key, value in updates.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = str(value)
    encoded = urlencode(params)
    return f"{request.url.path}?{encoded}" if encoded else request.url.path


def _page_window(pagination: PaginationMeta, radius: int = 2) -> list[int]:
    start = max(1, pagination.page - radius)
    end = min(pagination.total_pages, pagination.page + radius)
    return list(range(start, end + 1))


def _normalized_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _parse_datetime_local(value: str | None) -> int | None:
    normalized = _normalized_text(value)
    if normalized is None:
        return None
    try:
        return coerce_epoch(normalized, field_name="datetime")
    except ValueError as exc:
        raise ValueError("Use a valid date and time") from exc


def _validated_lifecycle_status(value: str | None) -> QuizLifecycleStatus:
    normalized = _normalized_text(value) or "published"
    if normalized not in ADMIN_QUIZ_LIFECYCLE_STATUSES:
        raise ValueError("Invalid lifecycle status")
    return normalized


def _safe_admin_quiz_return_url(value: str | None) -> str:
    """Validate that the return URL stays within admin quizzes pages (prevent open redirect)."""
    normalized = _normalized_text(value)
    if normalized and normalized.startswith("/app/admin/quizzes") and "://" not in normalized:
        return normalized
    return "/app/admin/quizzes"


def _build_answer_map_from_form(form) -> dict[str, str]:
    """Extract answer selections from a form submission, with defensive limits."""
    answers: dict[str, str] = {}
    for key, value in form.items():
        if not key.startswith("answer_"):
            continue
        # Defend against excessively long keys/values
        if len(key) > 200 or len(str(value)) > 200:
            continue
        answer_value = str(value).strip()
        if not answer_value:
            continue
        answers[key.replace("answer_", "", 1)] = answer_value
    return answers


async def _load_quiz_definition_for_attempt(*, store, quiz_id: str) -> QuizDefinition | None:
    return await store.get_quiz_definition(quiz_id)


def _validated_answer_map(quiz: QuizDefinition, answers: dict[str, str]) -> dict[str, str]:
    question_lookup = {question.id: question for question in quiz.questions}
    unknown_question_ids = sorted(set(answers) - set(question_lookup))
    if unknown_question_ids:
        raise ValueError(f"Unknown question_id value: {unknown_question_ids[0]}")

    normalized: dict[str, str] = {}
    for question in quiz.questions:
        choice = answers.get(question.id)
        if choice is None:
            continue
        valid_options = {option.id for option in question.options}
        if choice not in valid_options:
            raise ValueError(f"Invalid choice '{choice}' for question '{question.id}'")
        normalized[question.id] = choice
    return normalized


def _ordered_answer_models(quiz: QuizDefinition, answer_map: dict[str, str]) -> list[Answer]:
    return [
        Answer(question_id=question.id, choice=answer_map[question.id])
        for question in quiz.questions
        if question.id in answer_map
    ]


def _public_quiz(quiz: QuizDefinition) -> PublicQuizDefinition:
    return PublicQuizDefinition.from_quiz_definition(quiz)


templates.env.filters["datetime"] = _format_epoch
templates.env.filters["duration"] = _format_duration
templates.env.filters["datetime_local"] = _format_datetime_local
templates.env.globals["query_url"] = _query_url
templates.env.globals["csrf_token"] = get_csrf_token
templates.env.globals["local_timezone_label"] = local_timezone_name


def _render(
    request: Request,
    template_name: str,
    *,
    current_user: UserSession | None,
    status_code: int = status.HTTP_200_OK,
    **context,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        template_name,
        {
            "request": request,
            "current_user": current_user,
            "message": request.query_params.get("message"),
            "message_type": request.query_params.get("message_type", "info"),
            **context,
        },
        status_code=status_code,
    )


def _redirect(url: str, message: str | None = None, message_type: str = "info") -> RedirectResponse:
    if message:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode({'message': message, 'message_type': message_type})}"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


def _redirect_for_role(user: UserSession) -> str:
    return "/app/admin" if user.role == UserRole.ADMIN else "/app/student"


def _session_cookie_name_for_role(role: UserRole) -> str:
    return settings.admin_session_cookie_name if role == UserRole.ADMIN else settings.student_session_cookie_name


def _render_admin(
    request: Request,
    template_name: str,
    *,
    current_user: UserSession,
    admin_section: str,
    status_code: int = status.HTTP_200_OK,
    **context,
) -> HTMLResponse:
    return _render(
        request,
        template_name,
        current_user=current_user,
        status_code=status_code,
        admin_section=admin_section,
        admin_nav_items=ADMIN_NAV_ITEMS,
        **context,
    )


async def _authenticate_login_request(
    *,
    email: str,
    password: str,
    store,
    failure_url: str,
) -> tuple[UserSession | None, RedirectResponse | None]:
    try:
        payload = LoginRequest(email=email, password=password)
    except Exception as exc:
        return None, _redirect(failure_url, str(exc), "error")

    record = await store.authenticate_user(payload.email)
    if not record or not verify_password(payload.password, record["password_hash"]):
        return None, _redirect(failure_url, "Invalid email or password", "error")

    return UserSession.model_validate(record), None


async def _issue_login_response(*, store, user: UserSession, redirect_url: str) -> RedirectResponse:
    session_token = new_session_token()
    expires_at = await store.create_session(user.user_id, session_token)
    response = _redirect(redirect_url, "Signed in successfully", "success")
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.session_cookie_name, path="/app")
    response.set_cookie(
        _session_cookie_name_for_role(user.role),
        session_token,
        httponly=True,
        secure=settings.secure_cookies,
        max_age=settings.session_ttl_seconds,
        samesite="lax",
        path="/app",
    )
    response.headers["X-Session-Expires-At"] = str(expires_at)
    return response


@router.get("/app", response_class=HTMLResponse)
async def app_home(request: Request, store=Depends(get_store)) -> RedirectResponse:
    user = await get_optional_current_user(request, store=store)
    if user is None:
        return _redirect("/app/login")
    return _redirect(_redirect_for_role(user))


@router.get("/app/login", response_class=HTMLResponse)
async def login_page(request: Request, store=Depends(get_store)) -> HTMLResponse:
    current_user = await get_optional_current_user(request, store=store, scope="student")
    if current_user:
        return _redirect(_redirect_for_role(current_user))
    return _render(request, "login.html", current_user=None)


@router.post("/app/login", dependencies=[Depends(rate_limit_login)])
async def login_submit(
    email: str = Form(...),
    password: str = Form(...),
    store=Depends(get_store),
) -> RedirectResponse:
    user, error_response = await _authenticate_login_request(
        email=email,
        password=password,
        store=store,
        failure_url="/app/login",
    )
    if error_response is not None or user is None:
        return error_response

    if user.role != UserRole.STUDENT:
        return _redirect("/app/admin/login", "Admin accounts must use the admin sign in page", "info")

    return await _issue_login_response(store=store, user=user, redirect_url="/app/student")


@router.get("/app/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, store=Depends(get_store)) -> HTMLResponse:
    current_user = await get_optional_current_user(request, store=store, scope="admin")
    if current_user:
        return _redirect(_redirect_for_role(current_user))
    return _render(request, "admin_login.html", current_user=None)


@router.post("/app/admin/login", dependencies=[Depends(rate_limit_login)])
async def admin_login_submit(
    email: str = Form(...),
    password: str = Form(...),
    store=Depends(get_store),
) -> RedirectResponse:
    user, error_response = await _authenticate_login_request(
        email=email,
        password=password,
        store=store,
        failure_url="/app/admin/login",
    )
    if error_response is not None or user is None:
        return error_response

    if user.role != UserRole.ADMIN:
        return _redirect("/app/admin/login", "This account does not have admin access", "error")

    return await _issue_login_response(store=store, user=user, redirect_url="/app/admin")


@router.get("/app/register", response_class=HTMLResponse)
async def register_page(request: Request, store=Depends(get_store)) -> HTMLResponse:
    current_user = await get_optional_current_user(request, store=store, scope="student")
    if current_user:
        return _redirect(_redirect_for_role(current_user))
    return _render(request, "register.html", current_user=None, allow_registration=settings.allow_open_registration)


@router.post("/app/register", dependencies=[Depends(rate_limit_register)])
async def register_submit(
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    store=Depends(get_store),
) -> RedirectResponse:
    if not settings.allow_open_registration:
        return _redirect("/app/login", "Open registration is disabled", "error")

    try:
        payload = RegisterRequest(full_name=full_name, email=email, password=password)
        await store.create_user(payload)
    except Exception as exc:
        return _redirect("/app/register", str(exc), "error")

    return _redirect("/app/login", "Registration complete. You can sign in now.", "success")


@router.post("/app/logout")
async def logout_submit(
    request: Request,
    session_scope: str = Form("student"),
    store=Depends(get_store),
) -> RedirectResponse:
    if session_scope == "admin":
        cookie_name = settings.admin_session_cookie_name
        redirect_url = "/app/admin/login"
    elif session_scope == "student":
        cookie_name = settings.student_session_cookie_name
        redirect_url = "/app/login"
    else:
        cookie_name = ""
        redirect_url = "/app/login"

    tokens_to_delete: list[str] = []
    if cookie_name:
        token = request.cookies.get(cookie_name)
        if token:
            tokens_to_delete.append(token)
    else:
        for name in (
            settings.student_session_cookie_name,
            settings.admin_session_cookie_name,
            settings.session_cookie_name,
        ):
            token = request.cookies.get(name)
            if token and token not in tokens_to_delete:
                tokens_to_delete.append(token)

    for token in tokens_to_delete:
        await store.delete_session(token)

    response = _redirect(redirect_url, "Signed out", "success")
    if cookie_name:
        response.delete_cookie(cookie_name, path="/app")
    else:
        response.delete_cookie(settings.student_session_cookie_name, path="/app")
        response.delete_cookie(settings.admin_session_cookie_name, path="/app")
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.session_cookie_name, path="/app")
    return response


@router.get("/app/admin", response_class=HTMLResponse)
async def admin_overview(
    request: Request,
    current_user: UserSession | None = Depends(get_optional_admin_user),
    store=Depends(get_store),
) -> HTMLResponse:
    if current_user is None:
        return _redirect("/app/admin/login")

    summary = await store.get_admin_summary()
    recent_quizzes = await store.list_quiz_catalog_page(page=1, page_size=ADMIN_OVERVIEW_PREVIEW_SIZE)
    recent_students = await store.list_registered_students(page=1, page_size=ADMIN_OVERVIEW_PREVIEW_SIZE)
    recent_attempts = await store.list_participation_records(page=1, page_size=ADMIN_OVERVIEW_PREVIEW_SIZE)
    return _render_admin(
        request,
        "admin_overview.html",
        current_user=current_user,
        admin_section="overview",
        summary=summary,
        recent_quizzes=recent_quizzes,
        recent_students=recent_students,
        recent_attempts=recent_attempts,
    )


@router.get("/app/admin/quizzes", response_class=HTMLResponse)
async def admin_quizzes(
    request: Request,
    quiz_page: int = Query(1, ge=1),
    quiz_page_size: int = Query(settings.admin_default_page_size, ge=1, le=settings.admin_max_page_size),
    quiz_q: str | None = Query(None, max_length=120),
    performance_page: int = Query(1, ge=1),
    performance_page_size: int = Query(settings.admin_default_page_size, ge=1, le=settings.admin_max_page_size),
    performance_q: str | None = Query(None, max_length=120),
    current_user: UserSession = Depends(get_current_admin),
    store=Depends(get_store),
) -> HTMLResponse:
    quiz_q = _normalized_text(quiz_q)
    performance_q = _normalized_text(performance_q)

    quiz_catalog = await store.list_quiz_catalog_page(
        page=quiz_page,
        page_size=quiz_page_size,
        query=quiz_q,
    )
    quiz_performance = await store.list_quiz_performance_page(
        page=performance_page,
        page_size=performance_page_size,
        query=performance_q,
    )
    return _render_admin(
        request,
        "admin_quizzes.html",
        current_user=current_user,
        admin_section="quizzes",
        quiz_catalog=quiz_catalog,
        quiz_performance=quiz_performance,
        quiz_filters={
            "query": quiz_q,
            "page_size": quiz_page_size,
        },
        performance_filters={
            "query": performance_q,
            "page_size": performance_page_size,
        },
        quiz_page_numbers=_page_window(quiz_catalog.pagination),
        performance_page_numbers=_page_window(quiz_performance.pagination),
        lifecycle_status_options=ADMIN_QUIZ_LIFECYCLE_STATUSES,
    )


@router.get("/app/admin/students", response_class=HTMLResponse)
async def admin_students(
    request: Request,
    student_page: int = Query(1, ge=1),
    student_page_size: int = Query(settings.admin_default_page_size, ge=1, le=settings.admin_max_page_size),
    student_q: str | None = Query(None, max_length=120),
    current_user: UserSession = Depends(get_current_admin),
    store=Depends(get_store),
) -> HTMLResponse:
    student_q = _normalized_text(student_q)
    students = await store.list_registered_students(
        page=student_page,
        page_size=student_page_size,
        query=student_q,
    )
    return _render_admin(
        request,
        "admin_students.html",
        current_user=current_user,
        admin_section="students",
        students=students,
        student_filters={"query": student_q, "page_size": student_page_size},
        student_page_numbers=_page_window(students.pagination),
    )


@router.get("/app/admin/attempts", response_class=HTMLResponse)
async def admin_attempts(
    request: Request,
    participation_page: int = Query(1, ge=1),
    participation_page_size: int = Query(settings.admin_default_page_size, ge=1, le=settings.admin_max_page_size),
    participation_q: str | None = Query(None, max_length=120),
    participation_quiz_id: str | None = Query(None, max_length=128),
    participation_status: str | None = Query(None, max_length=32),
    current_user: UserSession = Depends(get_current_admin),
    store=Depends(get_store),
) -> HTMLResponse:
    participation_q = _normalized_text(participation_q)
    participation_quiz_id = _normalized_text(participation_quiz_id)
    participation_status = _normalized_text(participation_status)
    if participation_status not in ADMIN_ATTEMPT_STATUSES:
        participation_status = None

    participation_records = await store.list_participation_records(
        page=participation_page,
        page_size=participation_page_size,
        query=participation_q,
        quiz_id=participation_quiz_id,
        attempt_status=participation_status,
    )
    return _render_admin(
        request,
        "admin_attempts.html",
        current_user=current_user,
        admin_section="attempts",
        quizzes=await store.list_quizzes_for_admin(),
        participation_records=participation_records,
        participation_filters={
            "query": participation_q,
            "quiz_id": participation_quiz_id,
            "status": participation_status,
            "page_size": participation_page_size,
        },
        participation_page_numbers=_page_window(participation_records.pagination),
        attempt_status_options=ADMIN_ATTEMPT_STATUSES,
    )


@router.get("/app/admin/students/{user_id}", response_class=HTMLResponse)
async def admin_student_detail(
    request: Request,
    user_id: str,
    attempt_page: int = Query(1, ge=1),
    attempt_page_size: int = Query(settings.admin_default_page_size, ge=1, le=settings.admin_max_page_size),
    attempt_q: str | None = Query(None, max_length=120),
    attempt_quiz_id: str | None = Query(None, max_length=128),
    attempt_status: str | None = Query(None, max_length=32),
    current_user: UserSession = Depends(get_current_admin),
    store=Depends(get_store),
) -> HTMLResponse:
    attempt_q = _normalized_text(attempt_q)
    attempt_quiz_id = _normalized_text(attempt_quiz_id)
    attempt_status = _normalized_text(attempt_status)
    if attempt_status not in ADMIN_ATTEMPT_STATUSES:
        attempt_status = None

    student = await store.get_student_admin_record(user_id)
    if student is None:
        return _render_admin(
            request,
            "admin_student_detail.html",
            current_user=current_user,
            admin_section="students",
            student=None,
            student_attempts=None,
            quizzes=await store.list_quizzes_for_admin(),
            attempt_filters={
                "query": attempt_q,
                "quiz_id": attempt_quiz_id,
                "status": attempt_status,
                "page_size": attempt_page_size,
            },
            attempt_page_numbers=[],
            attempt_status_options=ADMIN_ATTEMPT_STATUSES,
            error="Student not found",
        )

    student_attempts = await store.list_participation_records(
        page=attempt_page,
        page_size=attempt_page_size,
        query=attempt_q,
        quiz_id=attempt_quiz_id,
        attempt_status=attempt_status,
        user_id=user_id,
    )
    return _render_admin(
        request,
        "admin_student_detail.html",
        current_user=current_user,
        admin_section="students",
        student=student,
        student_attempts=student_attempts,
        quizzes=await store.list_quizzes_for_admin(),
        attempt_filters={
            "query": attempt_q,
            "quiz_id": attempt_quiz_id,
            "status": attempt_status,
            "page_size": attempt_page_size,
        },
        attempt_page_numbers=_page_window(student_attempts.pagination),
        attempt_status_options=ADMIN_ATTEMPT_STATUSES,
        error=None,
    )


@router.post("/app/admin/upload")
async def admin_upload_quiz(
    file: UploadFile = File(...),
    lifecycle_status: str = Form("published"),
    availability_start_at: str | None = Form(None),
    availability_end_at: str | None = Form(None),
    current_user: UserSession = Depends(get_current_admin),
    store=Depends(get_store),
) -> RedirectResponse:
    try:
        content = await file.read()
        quiz = parse_quiz_workbook(content, file.filename)
        selected_status = _validated_lifecycle_status(lifecycle_status)
        parsed_start = _parse_datetime_local(availability_start_at)
        parsed_end = _parse_datetime_local(availability_end_at)
        quiz = quiz.model_copy(
            update={
                "availability_start_at": parsed_start if _normalized_text(availability_start_at) is not None else quiz.availability_start_at,
                "availability_end_at": parsed_end if _normalized_text(availability_end_at) is not None else quiz.availability_end_at,
            }
        )
        created = await store.create_quiz(
            quiz,
            created_by=current_user.user_id,
            source_filename=file.filename,
            lifecycle_status=selected_status,
        )
    except Exception as exc:
        logger.exception("Failed to import quiz workbook")
        return _redirect("/app/admin/quizzes", f"Quiz import failed: {exc}", "error")

    return _redirect("/app/admin/quizzes", f"Imported quiz '{created['title']}'", "success")


@router.post("/app/admin/quizzes/{quiz_id}/settings")
async def admin_update_quiz_settings(
    quiz_id: str,
    lifecycle_status: str = Form(...),
    availability_start_at: str | None = Form(None),
    availability_end_at: str | None = Form(None),
    next_url: str | None = Form(None),
    current_user: UserSession = Depends(get_current_admin),
    store=Depends(get_store),
) -> RedirectResponse:
    del current_user
    redirect_url = _safe_admin_quiz_return_url(next_url)
    try:
        updated = await store.update_quiz_settings(
            quiz_id,
            lifecycle_status=_validated_lifecycle_status(lifecycle_status),
            availability_start_at=_parse_datetime_local(availability_start_at),
            availability_end_at=_parse_datetime_local(availability_end_at),
        )
    except LookupError as exc:
        return _redirect(redirect_url, str(exc), "error")
    except Exception as exc:
        return _redirect(redirect_url, f"Quiz update failed: {exc}", "error")

    return _redirect(redirect_url, f"Updated settings for '{updated['title']}'", "success")


@router.post("/app/admin/quizzes/{quiz_id}/delete")
async def admin_delete_quiz(
    quiz_id: str,
    next_url: str | None = Form(None),
    current_user: UserSession = Depends(get_current_admin),
    store=Depends(get_store),
) -> RedirectResponse:
    del current_user
    redirect_url = _safe_admin_quiz_return_url(next_url)
    try:
        deleted = await store.delete_quiz(quiz_id)
    except LookupError as exc:
        return _redirect(redirect_url, str(exc), "error")
    except Exception as exc:
        return _redirect(redirect_url, f"Failed to delete quiz: {exc}", "error")
    return _redirect(redirect_url, f"Deleted quiz '{deleted['title']}'", "success")


@router.get("/app/student", response_class=HTMLResponse)
async def student_dashboard(
    request: Request,
    current_user: UserSession = Depends(get_current_user),
    store=Depends(get_store),
) -> HTMLResponse:
    quizzes = await store.list_quizzes_for_student(current_user.user_id)
    return _render(request, "student_dashboard.html", current_user=current_user, quizzes=quizzes)


@router.post("/app/student/quizzes/{quiz_id}/start")
async def student_start_attempt(
    quiz_id: str,
    current_user: UserSession = Depends(get_current_user),
    store=Depends(get_store),
) -> RedirectResponse:
    try:
        attempt = await store.start_attempt(quiz_id, current_user.user_id)
    except LookupError:
        return _redirect("/app/student", "Quiz not found", "error")
    except TimeoutError as exc:
        return _redirect("/app/student", str(exc), "error")
    except RuntimeError as exc:
        return _redirect("/app/student", str(exc), "info")

    if attempt.status in {"submitted", "scored"}:
        return _redirect(f"/app/student/results/{quiz_id}", "Attempt already submitted", "info")
    if attempt.status == "expired":
        return _redirect("/app/student", "This attempt has expired", "error")
    return _redirect(f"/app/student/attempts/{attempt.attempt_id}")


@router.get("/app/student/attempts/{attempt_id}", response_class=HTMLResponse)
async def student_attempt_page(
    request: Request,
    attempt_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.attempt_default_page_size, ge=1, le=settings.attempt_max_page_size),
    current_user: UserSession = Depends(get_current_user),
    store=Depends(get_store),
) -> HTMLResponse:
    try:
        attempt = await store.get_attempt(attempt_id, current_user.user_id)
    except LookupError:
        return _render(request, "attempt.html", current_user=current_user, error="Attempt not found", attempt=None, attempt_view=None)

    quiz = await _load_quiz_definition_for_attempt(store=store, quiz_id=attempt["quiz_id"])
    if quiz is None:
        return _render(request, "attempt.html", current_user=current_user, error="Quiz definition not available", attempt=attempt, attempt_view=None)

    try:
        saved_answers = await store.load_attempt_answers(attempt_id, current_user.user_id)
    except LookupError:
        return _render(request, "attempt.html", current_user=current_user, error="Attempt not found", attempt=attempt, attempt_view=None)

    saved_answers = _validated_answer_map(quiz, saved_answers)
    total_questions = len(quiz.questions)
    total_pages = max(1, (total_questions + page_size - 1) // page_size)
    current_page = min(max(page, 1), total_pages)
    start_index = (current_page - 1) * page_size
    end_index = start_index + page_size
    current_questions = _public_quiz(quiz.model_copy(update={"questions": quiz.questions[start_index:end_index]})).questions
    question_status_map = {
        question.id: ("answered" if question.id in saved_answers else "unanswered")
        for question in quiz.questions
    }
    question_number_map = {question.id: index for index, question in enumerate(quiz.questions, start=1)}
    now = utc_now_epoch()
    attempt_view = StudentAttemptView(
        attempt_id=attempt["attempt_id"],
        quiz_id=attempt["quiz_id"],
        status=attempt["status"],
        started_at=attempt["started_at"],
        expires_at=attempt["expires_at"],
        submitted_at=attempt["submitted_at"],
        remaining_seconds=max(0, attempt["expires_at"] - now) if attempt["status"] == "active" else 0,
        quiz=_public_quiz(quiz),
        saved_answers=saved_answers,
        page=current_page,
        page_size=page_size,
        total_questions=total_questions,
        total_pages=total_pages,
        current_questions=current_questions,
        question_status_map=question_status_map,
        question_number_map=question_number_map,
    )
    return _render(request, "attempt.html", current_user=current_user, error=None, attempt=attempt, attempt_view=attempt_view)


@router.post("/app/student/attempts/{attempt_id}/autosave", response_model=AttemptAutosaveResponse)
async def student_autosave_attempt(
    attempt_id: str,
    payload: AttemptAutosaveRequest,
    current_user: UserSession = Depends(get_current_user),
    store=Depends(get_store),
) -> AttemptAutosaveResponse:
    try:
        attempt = await store.prepare_attempt_submission(attempt_id, current_user.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    quiz = await _load_quiz_definition_for_attempt(store=store, quiz_id=attempt["quiz_id"])
    if quiz is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Quiz definition not available")

    try:
        answer_map = _validated_answer_map(
            quiz,
            {answer.question_id: answer.choice for answer in payload.answers},
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    now = utc_now_epoch()
    saved = await store.autosave_attempt_answers(
        attempt_id,
        current_user.user_id,
        [answer.model_dump(mode="json") for answer in _ordered_answer_models(quiz, answer_map)],
        saved_at=now,
    )
    return AttemptAutosaveResponse(
        saved_answer_count=saved["saved_answer_count"],
        saved_at=saved["saved_at"],
    )


@router.post("/app/student/attempts/{attempt_id}/submit")
async def student_submit_attempt(
    request: Request,
    attempt_id: str,
    current_user: UserSession = Depends(get_current_user),
    store=Depends(get_store),
) -> RedirectResponse:
    form = await request.form()
    try:
        current_page = max(1, int(form.get("page") or 1))
    except (ValueError, TypeError):
        current_page = 1
    attempt = None
    submission_saved = False

    try:
        attempt = await store.prepare_attempt_submission(attempt_id, current_user.user_id)
        quiz = await _load_quiz_definition_for_attempt(store=store, quiz_id=attempt["quiz_id"])
        if quiz is None:
            return _redirect(
                f"/app/student/attempts/{attempt_id}?page={current_page}",
                "Quiz definition not available",
                "error",
            )

        saved_answers = await store.load_attempt_answers(attempt_id, current_user.user_id)
        submitted_answers = _build_answer_map_from_form(form)
        merged_answers = _validated_answer_map(quiz, {**saved_answers, **submitted_answers})
        payload = AttemptSubmissionRequest(answers=_ordered_answer_models(quiz, merged_answers))
        now = utc_now_epoch()
        serialized_answers = [answer.model_dump(mode="json") for answer in payload.answers]
        attempt = await store.finalize_attempt_submission(
            attempt_id,
            current_user.user_id,
            serialized_answers,
            submitted_at=now,
        )
        submission_saved = True
        submission_id = hashlib.sha256(f"{attempt['quiz_id']}:{current_user.user_id}:{attempt_id}".encode("utf-8")).hexdigest()
        score = calculate_score(quiz.model_dump(mode="json"), serialized_answers)
        await store.save_result(
            quiz_id=attempt["quiz_id"],
            user_id=current_user.user_id,
            score=score["score"],
            total=score["total"],
            percentage=score["percentage"],
            evaluated_at=now,
            submission_id=submission_id,
            attempt_id=attempt_id,
        )
    except ValueError as exc:
        return _redirect(f"/app/student/attempts/{attempt_id}?page={current_page}", str(exc), "error")
    except TimeoutError as exc:
        return _redirect("/app/student", str(exc), "error")
    except RuntimeError as exc:
        if attempt is None:
            try:
                attempt = await store.get_attempt(attempt_id, current_user.user_id)
            except LookupError:
                return _redirect("/app/student", str(exc), "info")
        if attempt is not None:
            return _redirect(f"/app/student/results/{attempt['quiz_id']}", str(exc), "info")
        return _redirect("/app/student", str(exc), "info")
    except Exception as exc:
        if submission_saved and hasattr(store, "reopen_attempt_submission"):
            try:
                await store.reopen_attempt_submission(attempt_id, current_user.user_id)
            except Exception as reopen_exc:
                logger.error("Failed to reopen attempt %s after submission error: %s", attempt_id, reopen_exc)
        return _redirect(f"/app/student/attempts/{attempt_id}?page={current_page}", f"Submission failed: {exc}", "error")

    return _redirect(f"/app/student/results/{attempt['quiz_id']}", "Submission scored successfully", "success")


@router.get("/app/student/results/{quiz_id}", response_class=HTMLResponse)
async def student_result_page(
    request: Request,
    quiz_id: str,
    current_user: UserSession = Depends(get_current_user),
    store=Depends(get_store),
) -> HTMLResponse:
    result = await store.get_result(quiz_id, current_user.user_id)
    if result is None:
        result = ProcessingResultResponse()
    return _render(request, "result.html", current_user=current_user, quiz_id=quiz_id, result=result)
