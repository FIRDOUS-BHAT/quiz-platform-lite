import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.dependencies import get_store
from app.schemas.submission import ProcessingResultResponse, QuizResultResponse

router = APIRouter(
    prefix="/quiz",
    tags=["result"]
)

logger = logging.getLogger(__name__)

@router.get(
    "/{quiz_id}/result",
    response_model=ProcessingResultResponse | QuizResultResponse,
)
async def get_result(
    quiz_id: str = Path(..., min_length=1, max_length=128),
    user_id: str = Query(..., min_length=1),
    store=Depends(get_store),
) -> ProcessingResultResponse | QuizResultResponse:
    try:
        result = await store.get_result(quiz_id, user_id)
        if result is None:
            return ProcessingResultResponse()

        return result
    except Exception as exc:
        logger.error("Failed to fetch result %s:%s: %s", quiz_id, user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal data error",
        )
