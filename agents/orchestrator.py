
"""Application orchestrator for FATS."""

from datetime import datetime

from agents.services.market_data import MarketDataService
from core.schemas import (
    EvaluationRequest,
    EvaluationResponse,
    MarketDataSnapshot,
    Timeframe,
)
from quantitative.ports import QuantitativeEvaluator


class FATSOrchestrator:
    """Coordinate application-level market data and evaluation workflows."""

    def __init__(
        self,
        evaluator: QuantitativeEvaluator,
        market_data_service: MarketDataService | None = None,
    ) -> None:
        self._evaluator = evaluator
        self._market_data_service = market_data_service

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        """Delegate quantitative evaluation and return its canonical result."""
        return await self._evaluator.evaluate(request)

    async def get_market_snapshot(
        self,
        pair: str,
        timeframes: tuple[Timeframe, ...],
        as_of: datetime,
    ) -> MarketDataSnapshot:
        """Retrieve market data through the configured application service."""
        if self._market_data_service is None:
            raise RuntimeError("Market data service is not configured.")

        return await self._market_data_service.get_snapshot(
            pair=pair,
            timeframes=timeframes,
            as_of=as_of,
        )
