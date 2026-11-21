"""FastAPI application factory for the quantitative API."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.routes import router
from core.schemas import ErrorCode, ErrorResponse
from quantitative.exceptions import (
    InsufficientDataError,
    QuantitativeEngineUnavailableError,
)
from quantitative.ports import QuantitativeEvaluator


def create_app(evaluator: QuantitativeEvaluator | None = None) -> FastAPI:
    """Create the API and inject an evaluator supplied by the composition root."""
    app = FastAPI(
        title="FATS Quantitative API",
        version="1.0.0",
        description=(
            "Versioned API for deterministic quantitative evaluation. "
            "The API returns signals and risk decisions and does not place orders."
        ),
    )
    app.state.quantitative_evaluator = evaluator
    app.include_router(router)

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
        return JSONResponse(status_code=422, content=response.model_dump(mode="json"))

    @app.exception_handler(InsufficientDataError)
    async def insufficient_data_error(
        request: Request, exc: InsufficientDataError
    ) -> JSONResponse:
        del request
        response = ErrorResponse(
            error_code=ErrorCode.INSUFFICIENT_DATA,
            message=str(exc),
        )
        return JSONResponse(status_code=422, content=response.model_dump(mode="json"))

    @app.exception_handler(QuantitativeEngineUnavailableError)
    async def engine_unavailable_error(
        request: Request, exc: QuantitativeEngineUnavailableError
    ) -> JSONResponse:
        del request
        response = ErrorResponse(
            error_code=ErrorCode.QUANTITATIVE_UNAVAILABLE,
            message=str(exc),
        )
        return JSONResponse(status_code=503, content=response.model_dump(mode="json"))

    return app


app = create_app()
