"""Versioned HTTP routes for quantitative evaluation."""

from typing import Annotated

from fastapi import APIRouter, Depends

from backend.dependencies import get_quantitative_evaluator
from core.schemas import ErrorResponse, EvaluationRequest, EvaluationResponse
from quantitative.ports import QuantitativeEvaluator

router = APIRouter(prefix="/api/v1", tags=["quantitative"])


@router.post(
    "/evaluations",
    response_model=EvaluationResponse,
    summary="Evaluate a quantitative strategy",
    responses={
        422: {
            "model": ErrorResponse,
            "description": "Invalid request or insufficient market data.",
        },
        503: {
            "model": ErrorResponse,
            "description": "Quantitative engine or required dependency unavailable.",
        },
    },
)
async def evaluate(
    request: EvaluationRequest,
    evaluator: Annotated[QuantitativeEvaluator, Depends(get_quantitative_evaluator)],
) -> EvaluationResponse:
    """Return quantitative signal and risk output; this endpoint never places orders."""
    return await evaluator.evaluate(request)
