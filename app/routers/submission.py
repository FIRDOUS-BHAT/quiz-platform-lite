import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.dependencies import get_store
from app.schemas.quiz import QuizDefinition
from app.schemas.submission import QuizResultResponse, SubmissionRequest
from app.services.scoring import calculate_score
from app.utils.time import utc_now_epoch

router = APIRouter(
    prefix="/quiz",
    tags=["submission"]
)

logger = logging.getLogger(__name__)

@router.post(
    "/{quiz_id}/submit",
    response_model=QuizResultResponse,
    status_code=status.HTTP_200_OK,
)
async def submit_quiz(
    submission: SubmissionRequest,
    quiz_id: str = Path(..., min_length=1, max_length=128),
    store=Depends(get_store),
) -> QuizResultResponse:
    raw_id = f"{quiz_id}:{submission.user_id}"
    submission_id = hashlib.sha256(raw_id.encode()).hexdigest()

    try:
        quiz = await store.get_quiz_definition(quiz_id)
        if quiz is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Quiz {quiz_id} not found",
            )

        answers = _validated_answers(quiz, submission)
        score = calculate_score(quiz.model_dump(mode="json"), answers)
        now = utc_now_epoch()
        return await store.save_result(
            quiz_id=quiz_id,
            user_id=submission.user_id,
            score=score["score"],
            total=score["total"],
            percentage=score["percentage"],
            evaluated_at=now,
            submission_id=submission_id,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.error("Synchronous scoring failed for quiz %s: %s", quiz_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Submission processing failed",
        ) from exc


def _validated_answers(quiz: QuizDefinition, submission: SubmissionRequest) -> list[dict[str, str]]:
    question_lookup = {question.id: question for question in quiz.questions}
    answers_by_question = {answer.question_id: answer.choice for answer in submission.answers}

    unknown_question_ids = sorted(set(answers_by_question) - set(question_lookup))
    if unknown_question_ids:
        raise ValueError(f"Unknown question_id value: {unknown_question_ids[0]}")

    normalized_answers: list[dict[str, str]] = []
    for question in quiz.questions:
        choice = answers_by_question.get(question.id)
        if choice is None:
            continue
        valid_option_ids = {option.id for option in question.options}
        if choice not in valid_option_ids:
            raise ValueError(f"Invalid choice '{choice}' for question '{question.id}'")
        normalized_answers.append({"question_id": question.id, "choice": choice})

    return normalized_answers
