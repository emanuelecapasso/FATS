"""Integration coverage for the versioned quantitative API boundary."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.app import create_app
from core.schemas import (
    EvaluationRequest,
    EvaluationResponse,
    QuantitativeSignal,
    RiskDecision,
    SignalDirection,
    StrategyId,
)
from quantitative.exceptions import (
    InsufficientDataError,
    QuantitativeEngineUnavailableError,
)


@dataclass
class ASGIResponse:
    status_code: int
    body: dict[str, Any]

    def json(self) -> dict[str, Any]:
        return self.body


def asgi_request(app: Any, path: str, payload: dict | None = None) -> ASGIResponse:
    """Send one in-process HTTP request through the ASGI application."""
    method = "POST" if payload is not None else "GET"
    request_body = json.dumps(payload).encode() if payload is not None else b""
    request_sent = False
    response_complete = asyncio.Event()
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {
                "type": "http.request",
                "body": request_body,
                "more_body": False,
            }
        await response_complete.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get(
            "more_body", False
        ):
            response_complete.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": (
            [(b"host", b"testserver"), (b"content-type", b"application/json")]
            if payload is not None
            else [(b"host", b"testserver")]
        ),
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }

    async def run_request() -> None:
        await app(scope, receive, send)

    asyncio.run(run_request())
    status = next(
        message["status"]
        for message in messages
        if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return ASGIResponse(status_code=status, body=json.loads(response_body))


def valid_payload() -> dict:
    return {
        "schema_version": "1.0",
        "pair": "EURUSD",
        "as_of": "2026-11-21T10:00:00Z",
        "bars": {
            "m5": [
                {
                    "timestamp": "2026-11-21T09:50:00Z",
                    "open": 1.1,
                    "high": 1.12,
                    "low": 1.09,
                    "close": 1.11,
                    "volume": 10,
                }
            ],
            "m15": [
                {
                    "timestamp": "2026-11-21T09:45:00Z",
                    "open": 1.1,
                    "high": 1.12,
                    "low": 1.09,
                    "close": 1.11,
                    "volume": 30,
                }
            ],
            "h1": [
                {
                    "timestamp": "2026-11-21T09:00:00Z",
                    "open": 1.1,
                    "high": 1.12,
                    "low": 1.09,
                    "close": 1.11,
                    "volume": 120,
                }
            ],
        },
        "configuration": {
            "strategy": "trend_following",
            "parameters": {"lookback": 20},
        },
    }


def expected_response() -> EvaluationResponse:
    return EvaluationResponse(
        pair="EURUSD",
        as_of=datetime.fromisoformat("2026-11-21T10:00:00+00:00"),
        strategy=StrategyId.TREND_FOLLOWING,
        signal=QuantitativeSignal(
            direction=SignalDirection.BUY,
            confidence=0.75,
            rationale_codes=["trend_confirmed"],
        ),
        risk=RiskDecision(approved=True, recommended_quantity=1000),
    )


class FakeEvaluator:
    def __init__(self, result: EvaluationResponse | Exception) -> None:
        self.result = result
        self.received: EvaluationRequest | None = None

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        self.received = request
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_valid_evaluation_uses_injected_evaluator() -> None:
    evaluator = FakeEvaluator(expected_response())

    response = asgi_request(
        create_app(evaluator),
        "/api/v1/evaluations",
        valid_payload(),
    )

    assert response.status_code == 200
    assert response.json()["signal"]["direction"] == "buy"
    assert response.json()["risk"]["approved"] is True
    assert evaluator.received is not None
    assert evaluator.received.pair == "EURUSD"


def test_invalid_request_has_documented_error_shape() -> None:
    payload = valid_payload()
    payload["pair"] = "EUR USD"

    response = asgi_request(
        create_app(FakeEvaluator(expected_response())),
        "/api/v1/evaluations",
        payload,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.json()["details"]


def test_insufficient_data_maps_to_422() -> None:
    evaluator = FakeEvaluator(InsufficientDataError("M15 history is too short"))

    response = asgi_request(
        create_app(evaluator),
        "/api/v1/evaluations",
        valid_payload(),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "insufficient_data"


def test_unavailable_engine_maps_to_503() -> None:
    evaluator = FakeEvaluator(
        QuantitativeEngineUnavailableError("Market data source unavailable")
    )

    response = asgi_request(
        create_app(evaluator),
        "/api/v1/evaluations",
        valid_payload(),
    )

    assert response.status_code == 503
    assert response.json()["code"] == "quantitative_unavailable"


def test_unconfigured_engine_maps_to_503() -> None:
    response = asgi_request(create_app(), "/api/v1/evaluations", valid_payload())

    assert response.status_code == 503
    assert response.json()["code"] == "quantitative_unavailable"


def test_openapi_documents_contract_and_error_responses() -> None:
    response = asgi_request(create_app(), "/openapi.json")
    schema = response.json()
    operation = schema["paths"]["/api/v1/evaluations"]["post"]

    assert response.status_code == 200
    assert operation["requestBody"]["content"]["application/json"]["schema"]
    assert {"200", "422", "503"} <= set(operation["responses"])
    assert schema["info"]["version"] == "1.0.0"
