from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from app.schemas.quiz import PublicQuizDefinition, PublicQuizQuestion, QuizLifecycleStatus

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class QuizCatalogItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quiz_id: Identifier
    title: str
    description: str | None = None
    duration_seconds: int = Field(gt=0)
    created_at: int = Field(gt=0)
    lifecycle_status: QuizLifecycleStatus = "published"
    availability_start_at: int | None = None
    availability_end_at: int | None = None
    availability_status: str | None = None
    attempt_id: str | None = None
    attempt_status: str | None = None
    expires_at: int | None = None
    submitted_at: int | None = None


class AdminQuizImportResponse(BaseModel):
    quiz_id: Identifier
    title: str
    version: str
    question_count: int = Field(gt=0)


class AdminStudentRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: Identifier
    full_name: str
    email: str
    created_at: int = Field(gt=0)
    quizzes_started: int = Field(ge=0)
    quizzes_submitted: int = Field(ge=0)
    quizzes_scored: int = Field(ge=0)
    average_percentage: float | None = Field(default=None, ge=0, le=100)
    best_percentage: float | None = Field(default=None, ge=0, le=100)


class AdminParticipationRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    attempt_id: str
    quiz_id: Identifier
    quiz_title: str
    user_id: Identifier
    student_name: str
    student_email: str
    attempt_status: str
    started_at: int = Field(gt=0)
    expires_at: int = Field(gt=0)
    submitted_at: int | None = None
    score: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    percentage: float | None = Field(default=None, ge=0, le=100)


class AdminQuizPerformanceRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quiz_id: Identifier
    title: str
    duration_seconds: int = Field(gt=0)
    participant_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)
    scored_count: int = Field(ge=0)
    average_percentage: float | None = Field(default=None, ge=0, le=100)
    top_percentage: float | None = Field(default=None, ge=0, le=100)


class PaginationMeta(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=1)
    has_prev: bool
    has_next: bool
    start_item: int = Field(ge=0)
    end_item: int = Field(ge=0)


class AdminSummaryStats(BaseModel):
    total_quizzes: int = Field(ge=0)
    total_students: int = Field(ge=0)
    total_attempts: int = Field(ge=0)
    scored_attempts: int = Field(ge=0)


class AdminQuizPage(BaseModel):
    items: list[QuizCatalogItem]
    pagination: PaginationMeta


class AdminQuizPerformancePage(BaseModel):
    items: list[AdminQuizPerformanceRecord]
    pagination: PaginationMeta


class AdminStudentPage(BaseModel):
    items: list[AdminStudentRecord]
    pagination: PaginationMeta


class AdminParticipationPage(BaseModel):
    items: list[AdminParticipationRecord]
    pagination: PaginationMeta


class AttemptEnvelope(BaseModel):
    attempt_id: str
    quiz_id: Identifier
    status: Literal["active", "submitted", "scored", "expired"]
    started_at: int = Field(gt=0)
    expires_at: int = Field(gt=0)
    submitted_at: int | None = None
    remaining_seconds: int = Field(ge=0)


class StudentAttemptView(AttemptEnvelope):
    quiz: PublicQuizDefinition
    saved_answers: dict[Identifier, Identifier] = Field(default_factory=dict)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_questions: int = Field(ge=0)
    total_pages: int = Field(ge=1)
    current_questions: list[PublicQuizQuestion] = Field(default_factory=list)
    question_status_map: dict[Identifier, Literal["answered", "unanswered"]] = Field(default_factory=dict)
    question_number_map: dict[Identifier, int] = Field(default_factory=dict)
