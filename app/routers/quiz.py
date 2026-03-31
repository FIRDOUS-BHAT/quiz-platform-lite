import logging

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.dependencies import get_store
from app.schemas.quiz import PublicQuizDefinition

router = APIRouter(
    prefix="/quiz",
    tags=["quiz"]
)

logger = logging.getLogger(__name__)

@router.get("/{quiz_id}", response_model=PublicQuizDefinition)
async def get_quiz(
    quiz_id: str = Path(..., min_length=1, max_length=128),
    store=Depends(get_store),
) -> PublicQuizDefinition:
    try:
        quiz = await store.get_quiz_definition(quiz_id)
        if quiz is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Quiz {quiz_id} not found"
            )

        return PublicQuizDefinition.from_quiz_definition(quiz)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch quiz %s: %s", quiz_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal data error",
        )
