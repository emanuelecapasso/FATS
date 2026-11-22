
"""Application dependency container."""

from functools import lru_cache

from agents.orchestrator import FATSOrchestrator
from agents.providers.mock_market_data import MockMarketDataProvider
from agents.services.market_data import MarketDataService
from database.market_data_repository import MarketDataRepository


@lru_cache
def get_market_data_repository() -> MarketDataRepository:
    """Return the application market data repository."""
    return MarketDataRepository("data/fats.db")


def build_orchestrator() -> FATSOrchestrator:
    """Build the application orchestrator with configured dependencies."""
    repository = get_market_data_repository()

    # Mock provider is intentionally limited to development wiring.
    provider = MockMarketDataProvider(snapshots={})
    market_data_service = MarketDataService(
        provider=provider,
        storage=repository,
    )

    return FATSOrchestrator(
        evaluator=get_quantitative_evaluator_from_container(),
        market_data_service=market_data_service,
    )


def get_quantitative_evaluator_from_container():
    """Placeholder until the evaluator is injected by the application."""
    raise RuntimeError(
        "Quantitative evaluator must be injected by the application."
    )
