
"""FastAPI dependencies for application services."""

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request

from agents.orchestrator import FATSOrchestrator
from agents.ports.market_data import MarketDataStorage
from agents.services.market_data import MarketDataService
from data.market_calendar import ForexMarketCalendar
from database.broker_repository import BrokerRepository
from database.market_data_repository import MarketDataRepository
from quantitative.exceptions import QuantitativeEngineUnavailableError
from quantitative.ports import QuantitativeEvaluator


def get_market_data_storage(request: Request) -> MarketDataStorage:
    """Resolve the configured historical market data repository."""
    storage = getattr(request.app.state, "market_data_storage", None)

    if storage is None:
        storage = MarketDataRepository("data/fats.db")
        request.app.state.market_data_storage = storage

    return storage


def get_broker_repository(request: Request) -> BrokerRepository:
    """Resolve the persistent broker configuration repository."""
    repository = getattr(request.app.state, "broker_repository", None)

    if repository is None:
        repository = BrokerRepository("data/fats.db")
        request.app.state.broker_repository = repository

    return repository


def get_market_calendar(
    request: Request,
    broker_repository: Annotated[
        BrokerRepository,
        Depends(get_broker_repository),
    ],
    provider_id: Annotated[str | None, Query()] = None,
) -> ForexMarketCalendar:
    """Resolve the default or provider-specific Forex calendar."""
    if provider_id is None:
        return request.app.state.market_calendar

    config = broker_repository.get(provider_id)

    if config is None:
        raise HTTPException(
            status_code=404,
            detail=f"Broker configuration not found: {provider_id}",
        )

    return config.to_calendar()


async def get_market_data_service(
    request: Request,
) -> MarketDataService | None:
    """Resolve market data service only when a provider is configured."""
    provider = getattr(request.app.state, "market_data_provider", None)

    if provider is None:
        return None

    storage = get_market_data_storage(request)

    return MarketDataService(
        provider=provider,
        storage=storage,
    )


async def get_quantitative_evaluator(request: Request) -> QuantitativeEvaluator:
    """Resolve the configured quantitative evaluator."""
    evaluator = getattr(request.app.state, "quantitative_evaluator", None)

    if evaluator is None:
        raise QuantitativeEngineUnavailableError(
            "Quantitative evaluator is not configured"
        )

    return evaluator


async def get_orchestrator(
    evaluator: Annotated[
        QuantitativeEvaluator,
        Depends(get_quantitative_evaluator),
    ],
    market_data_service: Annotated[
        MarketDataService | None,
        Depends(get_market_data_service),
    ],
) -> FATSOrchestrator:
    """Build the application orchestrator with configured services."""
    return FATSOrchestrator(
        evaluator=evaluator,
        market_data_service=market_data_service,
    )
