"""FastAPI application factory."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agents.ports.market_data import MarketDataProvider, MarketDataStorage
from backend.broker_routes import router as broker_router
from backend.evaluator import build_default_evaluator
from backend.routes import router
from core.schemas import ErrorCode, ErrorResponse
from data.market_calendar import ForexMarketCalendar
from database.broker_repository import BrokerRepository
from quantitative.exceptions import (
    InsufficientDataError,
    QuantitativeEngineUnavailableError,
)
from quantitative.ports import QuantitativeEvaluator


def create_app(
    evaluator: QuantitativeEvaluator | None = None,
    market_data_provider: MarketDataProvider | None = None,
    market_data_storage: MarketDataStorage | None = None,
    market_calendar: ForexMarketCalendar | None = None,
    broker_repository: BrokerRepository | None = None,
) -> FastAPI:
    """Create the API and inject its application dependencies."""
    app = FastAPI(
        title="FATS Quantitative API",
        version="1.0.0",
        description=(
            "Versioned API for deterministic quantitative evaluation. "
            "The API returns signals and risk decisions and does not place orders."
        ),
    )

    app.state.quantitative_evaluator = evaluator
    app.state.market_data_provider = market_data_provider
    app.state.market_data_storage = market_data_storage
    app.state.market_calendar = market_calendar or ForexMarketCalendar()
    app.state.broker_repository = broker_repository

    app.include_router(router)
    app.include_router(broker_router)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request
        response = ErrorResponse(
            error_code=ErrorCode.INVALID_REQUEST,
            message="Request does not match the quantitative API contract.",
            details={
                "errors": [
                    {
                        "location": list(error["loc"]),
                        "message": error["msg"],
                        "type": error["type"],
                    }
                    for error in exc.errors()
                ]
            },
        )
        return JSONResponse(
            status_code=422,
            content=response.model_dump(mode="json"),
        )

    @app.exception_handler(InsufficientDataError)
    async def insufficient_data_error(
        request: Request, exc: InsufficientDataError
    ) -> JSONResponse:
        del request
        response = ErrorResponse(
            error_code=ErrorCode.INSUFFICIENT_DATA,
            message=str(exc),
        )
        return JSONResponse(
            status_code=422,
            content=response.model_dump(mode="json"),
        )

    @app.exception_handler(QuantitativeEngineUnavailableError)
    async def engine_unavailable_error(
        request: Request, exc: QuantitativeEngineUnavailableError
    ) -> JSONResponse:
        del request
        response = ErrorResponse(
            error_code=ErrorCode.QUANTITATIVE_UNAVAILABLE,
            message=str(exc),
        )
        return JSONResponse(
            status_code=503,
            content=response.model_dump(mode="json"),
        )

    return app


app = create_app(evaluator=build_default_evaluator())
