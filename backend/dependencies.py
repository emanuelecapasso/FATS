"""FastAPI dependencies for the versioned quantitative API."""

from fastapi import Request

from quantitative.exceptions import QuantitativeEngineUnavailableError
from quantitative.ports import QuantitativeEvaluator


async def get_quantitative_evaluator(request: Request) -> QuantitativeEvaluator:
    """Resolve the evaluator configured by the application composition root."""
    evaluator = getattr(request.app.state, "quantitative_evaluator", None)
    if evaluator is None:
        raise QuantitativeEngineUnavailableError(
            "Quantitative evaluator is not configured"
        )
    return evaluator
