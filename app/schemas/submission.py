from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import Annotated

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
SubmissionId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=64, max_length=64)]


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: Identifier
    choice: Identifier


class SubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: Identifier
    answers: list[Answer] = Field(min_length=1, max_length=200)
    client_started_at: int = Field(gt=0)
    client_submitted_at: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_submission(self) -> "SubmissionRequest":
        if self.client_submitted_at < self.client_started_at:
            raise ValueError("client_submitted_at must be greater than or equal to client_started_at")

        question_ids = [answer.question_id for answer in self.answers]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("answers must not contain duplicate question_id values")

        return self

    def to_event(
        self,
        *,
        quiz_id: str,
        quiz_version: str,
        submission_id: str,
        server_received_at: int,
    ) -> "SubmissionEvent":
        return SubmissionEvent(
            submission_id=submission_id,
            quiz_id=quiz_id,
            quiz_version=quiz_version,
            user_id=self.user_id,
            answers=self.answers,
            client_started_at=self.client_started_at,
            client_submitted_at=self.client_submitted_at,
            server_received_at=server_received_at,
        )


class SubmissionEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_type: Literal["quiz_submission"] = "quiz_submission"
    submission_id: SubmissionId
    quiz_id: Identifier
    quiz_version: Identifier = "1"
    attempt_id: str | None = None
    attempt_expires_at: int | None = Field(default=None, gt=0)
    user_id: Identifier
    answers: list[Answer] = Field(default_factory=list, max_length=200)
    client_started_at: int = Field(gt=0)
    client_submitted_at: int = Field(gt=0)
    server_received_at: int = Field(gt=0)


class SubmissionAcceptedResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    submission_id: SubmissionId


class AttemptSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[Answer] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_answers(self) -> "AttemptSubmissionRequest":
        question_ids = [answer.question_id for answer in self.answers]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("answers must not contain duplicate question_id values")
        return self


class AttemptAutosaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[Answer] = Field(default_factory=list, max_length=200)
    page: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_answers(self) -> "AttemptAutosaveRequest":
        question_ids = [answer.question_id for answer in self.answers]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("answers must not contain duplicate question_id values")
        return self


class AttemptAutosaveResponse(BaseModel):
    status: Literal["saved"] = "saved"
    saved_answer_count: int = Field(ge=0)
    saved_at: int = Field(gt=0)


class ProcessingResultResponse(BaseModel):
    status: Literal["processing"] = "processing"


class QuizResultResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["completed"] = "completed"
    quiz_id: Identifier
    user_id: Identifier
    score: int = Field(ge=0)
    total: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)
    evaluated_at: int = Field(gt=0)
    submission_id: str | None = None
    attempt_id: str | None = None
