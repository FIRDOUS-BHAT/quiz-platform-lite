from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import Annotated

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
QuizLifecycleStatus = Literal["draft", "published", "archived"]


class QuizOption(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Identifier
    text: LongText


class QuizQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Identifier
    prompt: Optional[LongText] = None
    text: Optional[LongText] = None
    type: ShortText = "single_choice"
    options: list[QuizOption] = Field(min_length=2, max_length=10)
    correct_option_id: Identifier

    @model_validator(mode="after")
    def validate_question(self) -> "QuizQuestion":
        if not self.prompt and not self.text:
            raise ValueError("question must include either prompt or text")

        option_ids = {option.id for option in self.options}
        if len(option_ids) != len(self.options):
            raise ValueError("question options must use unique ids")

        if self.correct_option_id not in option_ids:
            raise ValueError("correct_option_id must match an option id")

        return self


class PublicQuizQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Identifier
    prompt: Optional[LongText] = None
    text: Optional[LongText] = None
    type: ShortText = "single_choice"
    options: list[QuizOption]


class QuizDefinition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quiz_id: Optional[Identifier] = None
    version: Optional[Identifier] = None
    title: LongText
    description: Optional[LongText] = None
    duration_seconds: Optional[int] = Field(default=None, gt=0, le=86400)
    availability_start_at: Optional[int] = Field(default=None, gt=0)
    availability_end_at: Optional[int] = Field(default=None, gt=0)
    questions: list[QuizQuestion] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_window(self) -> "QuizDefinition":
        if (
            self.availability_start_at is not None
            and self.availability_end_at is not None
            and self.availability_end_at <= self.availability_start_at
        ):
            raise ValueError("availability_end_at must be greater than availability_start_at")
        return self


class PublicQuizDefinition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quiz_id: Optional[Identifier] = None
    version: Optional[Identifier] = None
    title: LongText
    description: Optional[LongText] = None
    duration_seconds: Optional[int] = Field(default=None, gt=0, le=86400)
    availability_start_at: Optional[int] = Field(default=None, gt=0)
    availability_end_at: Optional[int] = Field(default=None, gt=0)
    questions: list[PublicQuizQuestion]

    @classmethod
    def from_quiz_definition(cls, quiz: QuizDefinition) -> "PublicQuizDefinition":
        return cls(
            quiz_id=quiz.quiz_id,
            version=quiz.version,
            title=quiz.title,
            description=quiz.description,
            duration_seconds=quiz.duration_seconds,
            availability_start_at=quiz.availability_start_at,
            availability_end_at=quiz.availability_end_at,
            questions=[
                PublicQuizQuestion(
                    id=question.id,
                    prompt=question.prompt,
                    text=question.text,
                    type=question.type,
                    options=question.options,
                )
                for question in quiz.questions
            ],
        )
