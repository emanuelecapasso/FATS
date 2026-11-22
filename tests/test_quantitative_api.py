from fastapi.testclient import TestClient

from backend.app import create_app
from core.schemas import (
    CURRENT_SCHEMA_VERSION,
    DataQuality,
    DataSourceKind,
    EvaluationRequest,
    EvaluationResponse,
    RiskDecision,
    Signal,
    SignalDirection,
    StrategyId,
    Timeframe,
)
from quantitative.ports import QuantitativeEvaluator


class FakeEvaluator(QuantitativeEvaluator):
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        return EvaluationResponse(
            schema_version=CURRENT_SCHEMA_VERSION,
            pair=request.market_data.pair,
            timestamp=request.market_data.as_of,
            signal=Signal(
                direction=SignalDirection.LONG,
                confidence=0.85,
                motivational_codes=["MOCK_TREND_UP"],
            ),
            risk_decision=RiskDecision(
                approved=True,
                reasons=["WITHIN_RISK_LIMITS"],
                recommended_quantity=10000.0,
            ),
            # L'engine in produzione li popolerà, qui li simuliamo per l'API mockata
            strategy_id=request.configuration.strategy_id,
            strategy_version="1.0.0",
            engine_version="1.1.5",
        )


def _sample_payload() -> dict:
    as_of = "2026-11-21T14:00:00Z"
    bar = {
        "timestamp": "2026-11-21T13:55:00Z",
        "open": 1.0850,
        "high": 1.0870,
        "low": 1.0840,
        "close": 1.0860,
        "volume": 1200.0,
    }
    provenance = {
        "kind": DataSourceKind.BROKER.value,
        "provider_id": "test_broker",
        "received_at": as_of,
    }
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "market_data": {
            "pair": "EUR_USD",
            "as_of": as_of,
            "series": {
                Timeframe.M5.value: {
                    "timeframe": Timeframe.M5.value,
                    "bars": [bar],
                    "provenance": provenance,
                    "quality": DataQuality.COMPLETE.value,
                }
            },
        },
        "configuration": {
            "strategy_id": StrategyId.TREND_FOLLOWING.value,
            "parameters": {"fast_window": 10.0},
        },
    }


def test_post_evaluation_success() -> None:
    app = create_app(evaluator=FakeEvaluator())
    client = TestClient(app)

    response = client.post("/api/v1/evaluations", json=_sample_payload())
    assert response.status_code == 200
    body = response.json()

    assert body["pair"] == "EUR_USD"
    # La risposta espone la versione corrente dello schema.
    assert body["schema_version"] == CURRENT_SCHEMA_VERSION
    assert body["signal"]["direction"] == "long"
    assert body["risk_decision"]["approved"] is True

    # Assicura che i nuovi metadati opzionali vengano esposti dall'API
    assert "strategy_id" in body
    assert "strategy_version" in body
    assert "engine_version" in body


def test_openapi_reflects_new_market_data_snapshot_contract() -> None:
    app = create_app(evaluator=FakeEvaluator())
    client = TestClient(app)

    openapi_schema = client.get("/openapi.json").json()
    components = openapi_schema["components"]["schemas"]

    # Verifica che EvaluationRequest contenga esattamente market_data e configuration
    req_props = components["EvaluationRequest"]["properties"]
    assert "market_data" in req_props
    assert "configuration" in req_props
    assert "pair" not in req_props  # Vecchio campo piatto rimosso

    # Verifica che MarketDataSnapshot sia referenziato correttamente
    assert req_props["market_data"]["$ref"] == "#/components/schemas/MarketDataSnapshot"
    assert "MarketDataSnapshot" in components
    assert "TimeframeSeries" in components
    assert "DataProvenance" in components
