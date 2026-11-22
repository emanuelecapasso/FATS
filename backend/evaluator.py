"""Production wiring of the quantitative engine behind the API port."""

from core.schemas import EvaluationRequest, EvaluationResponse
from quantitative.engine import QuantitativeEngine
from quantitative.exceptions import (
    InsufficientDataError,
    InsufficientHistoryError,
    InvalidMarketDataError,
    QuantitativeEngineUnavailableError,
)
from quantitative.ports import RiskEvaluator
from quantitative.registry import StrategyNotFoundError, StrategyRegistry
from quantitative.risk import DeterministicRiskEvaluator
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_following import TrendFollowingStrategy


class StrategyRoutingEvaluator:
    """Select the strategy requested by the API call and run the engine.

    It also translates the quantitative domain errors into the exceptions
    that the API already maps to the shared error contract.
    """

    def __init__(
        self,
        registry: StrategyRegistry,
        risk_evaluator: RiskEvaluator,
    ) -> None:
        self._registry = registry
        self._risk_evaluator = risk_evaluator

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        strategy_id = request.configuration.strategy_id.value

        try:
            strategy = self._registry.get_strategy(strategy_id)
        except StrategyNotFoundError as exc:
            raise QuantitativeEngineUnavailableError(str(exc)) from exc

        engine = QuantitativeEngine(
            strategy=strategy,
            risk_evaluator=self._risk_evaluator,
        )

        try:
            return await engine.evaluate(request)
        except (InsufficientHistoryError, InvalidMarketDataError) as exc:
            raise InsufficientDataError(str(exc)) from exc


def build_default_evaluator() -> StrategyRoutingEvaluator:
    """Register the available strategies and the deterministic risk checks."""
    registry = StrategyRegistry()
    registry.register(TrendFollowingStrategy())
    registry.register(MeanReversionStrategy())

    return StrategyRoutingEvaluator(
        registry=registry,
        risk_evaluator=DeterministicRiskEvaluator(),
    )
