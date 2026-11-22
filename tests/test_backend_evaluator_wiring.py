from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.evaluator import build_default_evaluator
from core.schemas import (
    DataProvenance,
    DataQuality,
    DataSourceKind,
    EvaluationRequest,
    MarketBar,
    MarketDataSnapshot,
    StrategyConfiguration,
    StrategyId,
    Timeframe,
    TimeframeSeries,
)
from quantitative.exceptions import InsufficientDataError

AS_OF = datetime(2026, 1, 12, 10, 0, tzinfo=UTC)


def _request(strategy_id: StrategyId) -> EvaluationRequest:
    bar = MarketBar(
        timestamp=AS_OF, open=1.1, high=1.2, low=1.0, close=1.15, is_closed=True
    )
    series = {
        timeframe: TimeframeSeries(
            timeframe=timeframe,
            bars=[bar],
            provenance=DataProvenance(
                kind=DataSourceKind.BROKER, provider_id="test", received_at=AS_OF
            ),
            quality=DataQuality.COMPLETE,
        )
        for timeframe in (Timeframe.M5, Timeframe.M15, Timeframe.H1)
    }
    return EvaluationRequest(
        market_data=MarketDataSnapshot(pair="EURUSD", as_of=AS_OF, series=series),
        configuration=StrategyConfiguration(strategy_id=strategy_id),
    )


@pytest.mark.parametrize("strategy_id", list(StrategyId))
async def test_every_strategy_is_registered_and_domain_errors_are_translated(
    strategy_id,
):
    evaluator = build_default_evaluator()

    with pytest.raises(InsufficientDataError):
        await evaluator.evaluate(_request(strategy_id))


def test_default_app_is_wired_and_reports_insufficient_data_not_503():
    client = TestClient(app)
    payload = _request(StrategyId.TREND_FOLLOWING).model_dump(mode="json")

    response = client.post("/api/v1/evaluations", json=payload)

    assert response.status_code == 422
    assert response.json()["error_code"] == "insufficient_data"
