
"""Versioned HTTP routes for quantitative evaluation and market data."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from agents.orchestrator import FATSOrchestrator
from agents.ports.market_data import MarketDataStorage
from backend.dependencies import (
    get_market_calendar,
    get_market_data_storage,
    get_orchestrator,
)
from core.schemas import (
    ErrorResponse,
    EvaluationRequest,
    EvaluationResponse,
    MarketBar,
    Timeframe,
)
from data.market_calendar import ForexMarketCalendar
from data.quality import MarketDataQualityChecker

router = APIRouter(prefix="/api/v1", tags=["quantitative"])


class MarketDataSummary(BaseModel):
    """Summary of stored historical market data."""

    pair: str
    timeframe: Timeframe
    count: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None


class MarketDataGapResponse(BaseModel):
    """Description of a detected historical data gap."""

    previous_timestamp: datetime
    next_timestamp: datetime
    first_missing_timestamp: datetime
    last_missing_timestamp: datetime
    missing_candles: int
    weekend_intervals_skipped: int


class MarketDataQualityResponse(BaseModel):
    """Quality report for stored historical market data."""

    pair: str
    timeframe: Timeframe
    candles_checked: int
    total_missing_candles: int
    weekend_intervals_skipped: int
    is_complete: bool
    gaps: list[MarketDataGapResponse]


def normalize_pair(pair: str) -> str:
    """Normalize and validate a Forex currency pair."""
    normalized_pair = pair.strip().upper().replace("/", "")

    if (
        len(normalized_pair) != 6
        or not normalized_pair.isalpha()
        or normalized_pair[:3] == normalized_pair[3:]
    ):
        raise HTTPException(
            status_code=422,
            detail="Invalid Forex pair.",
        )

    return normalized_pair


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
            "description": (
                "Quantitative engine or required dependency unavailable."
            ),
        },
    },
)
async def evaluate(
    request: EvaluationRequest,
    orchestrator: Annotated[
        FATSOrchestrator,
        Depends(get_orchestrator),
    ],
) -> EvaluationResponse:
    """Delegate evaluation to the application orchestrator; never places orders."""
    return await orchestrator.evaluate(request)


@router.get(
    "/market-data/{pair}",
    response_model=list[MarketBar],
    tags=["market-data"],
    summary="Retrieve historical Forex bars",
)
async def get_market_data(
    pair: str,
    storage: Annotated[
        MarketDataStorage,
        Depends(get_market_data_storage),
    ],
    timeframe: Timeframe = Query(...),  # noqa: B008
    start: datetime = Query(...),  # noqa: B008
    end: datetime = Query(...),  # noqa: B008
    provider_id: str | None = Query(None),
) -> list[MarketBar]:
    """Retrieve stored OHLCV bars for a pair, timeframe and provider."""
    normalized_pair = normalize_pair(pair)

    try:
        return storage.load_bars(
            pair=normalized_pair,
            timeframe=timeframe,
            start=start,
            end=end,
            provider_id=provider_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/market-data/{pair}/summary",
    response_model=MarketDataSummary,
    tags=["market-data"],
    summary="Summarize stored Forex history",
)
async def get_market_data_summary(
    pair: str,
    storage: Annotated[
        MarketDataStorage,
        Depends(get_market_data_storage),
    ],
    timeframe: Timeframe = Query(...),  # noqa: B008
    start: datetime = Query(...),  # noqa: B008
    end: datetime = Query(...),  # noqa: B008
    provider_id: str | None = Query(None),
) -> MarketDataSummary:
    """Return the number and timestamp boundaries of stored bars."""
    normalized_pair = normalize_pair(pair)

    try:
        bars = storage.load_bars(
            pair=normalized_pair,
            timeframe=timeframe,
            start=start,
            end=end,
            provider_id=provider_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return MarketDataSummary(
        pair=normalized_pair,
        timeframe=timeframe,
        count=len(bars),
        first_timestamp=bars[0].timestamp if bars else None,
        last_timestamp=bars[-1].timestamp if bars else None,
    )


@router.get(
    "/market-data/{pair}/quality",
    response_model=MarketDataQualityResponse,
    tags=["market-data"],
    summary="Check historical Forex data quality",
)
async def get_market_data_quality(
    pair: str,
    storage: Annotated[
        MarketDataStorage,
        Depends(get_market_data_storage),
    ],
    calendar: Annotated[
        ForexMarketCalendar,
        Depends(get_market_calendar),
    ],
    timeframe: Timeframe = Query(...),  # noqa: B008
    start: datetime = Query(...),  # noqa: B008
    end: datetime = Query(...),  # noqa: B008
    provider_id: str | None = Query(None),
) -> MarketDataQualityResponse:
    """Analyze provider-specific bars using its configured Forex calendar."""
    normalized_pair = normalize_pair(pair)

    try:
        bars = storage.load_bars(
            pair=normalized_pair,
            timeframe=timeframe,
            start=start,
            end=end,
            provider_id=provider_id,
        )

        report = MarketDataQualityChecker(
            calendar=calendar,
        ).check(
            bars=bars,
            timeframe=timeframe,
        )

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return MarketDataQualityResponse(
        pair=normalized_pair,
        timeframe=timeframe,
        candles_checked=report.candles_checked,
        total_missing_candles=report.total_missing_candles,
        weekend_intervals_skipped=report.weekend_intervals_skipped,
        is_complete=report.is_complete,
        gaps=[
            MarketDataGapResponse(
                previous_timestamp=gap.previous_timestamp,
                next_timestamp=gap.next_timestamp,
                first_missing_timestamp=gap.first_missing_timestamp,
                last_missing_timestamp=gap.last_missing_timestamp,
                missing_candles=gap.missing_candles,
                weekend_intervals_skipped=gap.weekend_intervals_skipped,
            )
            for gap in report.gaps
        ],
    )
