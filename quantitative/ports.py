"""Ports used by the backend to call quantitative evaluation."""

from typing import Protocol

from core.schemas import EvaluationRequest, EvaluationResponse


class QuantitativeEvaluator(Protocol):
    """Asynchronous deterministic evaluation boundary injected into the API."""

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        """Evaluate a validated request and return canonical signal and risk output."""
