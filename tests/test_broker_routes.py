"""Tests for broker management REST endpoints."""

from datetime import time

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from database.broker_repository import BrokerRepository


@pytest.fixture
def broker_repository(tmp_path):
    return BrokerRepository(tmp_path / "brokers.db")


@pytest.fixture
def client(broker_repository):
    app = create_app(broker_repository=broker_repository)
    return TestClient(app)


def broker_payload(provider_id="broker_a"):
    return {
        "provider_id": provider_id,
        "name": "Broker A",
        "timezone": "Europe/London",
        "sunday_open": "17:00:00",
        "friday_close": "17:00:00",
        "closed_dates": ["2026-12-25"],
    }


def test_create_broker(client, broker_repository):
    response = client.post(
        "/api/v1/brokers",
        json=broker_payload(),
    )

    assert response.status_code == 201
    assert response.json()["provider_id"] == "broker_a"
    assert response.json()["name"] == "Broker A"
    assert response.json()["closed_dates"] == ["2026-12-25"]

    stored = broker_repository.get("broker_a")
    assert stored is not None
    assert stored.timezone == "Europe/London"


def test_create_duplicate_broker_returns_conflict(client):
    payload = broker_payload()

    first = client.post("/api/v1/brokers", json=payload)
    second = client.post("/api/v1/brokers", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409


def test_list_brokers(client):
    client.post("/api/v1/brokers", json=broker_payload("broker_b"))
    client.post("/api/v1/brokers", json=broker_payload("broker_a"))

    response = client.get("/api/v1/brokers")

    assert response.status_code == 200
    assert [item["provider_id"] for item in response.json()] == [
        "broker_a",
        "broker_b",
    ]


def test_get_broker(client):
    client.post("/api/v1/brokers", json=broker_payload())

    response = client.get("/api/v1/brokers/broker_a")

    assert response.status_code == 200
    assert response.json()["name"] == "Broker A"


def test_get_unknown_broker_returns_not_found(client):
    response = client.get("/api/v1/brokers/unknown")

    assert response.status_code == 404


def test_update_broker(client, broker_repository):
    client.post("/api/v1/brokers", json=broker_payload())

    response = client.put(
        "/api/v1/brokers/broker_a",
        json={
            "name": "Updated Broker",
            "timezone": "America/New_York",
            "sunday_open": "18:00:00",
            "friday_close": "16:00:00",
            "closed_dates": ["2026-12-25"],
        },
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Updated Broker"
    assert response.json()["sunday_open"] == "18:00:00"

    stored = broker_repository.get("broker_a")
    assert stored is not None
    assert stored.name == "Updated Broker"
    assert stored.sunday_open == time(18, 0)


def test_update_unknown_broker_returns_not_found(client):
    response = client.put(
        "/api/v1/brokers/unknown",
        json={
            "name": "Unknown",
            "timezone": "Europe/London",
        },
    )

    assert response.status_code == 404


def test_delete_broker_deletes_configuration(client, broker_repository):
    client.post("/api/v1/brokers", json=broker_payload())

    response = client.delete("/api/v1/brokers/broker_a")

    assert response.status_code == 204
    assert broker_repository.get("broker_a") is None


def test_delete_unknown_broker_returns_not_found(client):
    response = client.delete("/api/v1/brokers/unknown")

    assert response.status_code == 404


def test_invalid_timezone_returns_unprocessable_entity(client):
    payload = broker_payload()
    payload["timezone"] = "Invalid/Timezone"

    response = client.post("/api/v1/brokers", json=payload)

    assert response.status_code == 422


def test_invalid_provider_id_returns_unprocessable_entity(client):
    payload = broker_payload()
    payload["provider_id"] = " broker_a"

    response = client.post("/api/v1/brokers", json=payload)

    assert response.status_code == 422


def test_invalid_closed_date_returns_unprocessable_entity(client):
    payload = broker_payload()
    payload["closed_dates"] = ["not-a-date"]

    response = client.post("/api/v1/brokers", json=payload)

    assert response.status_code == 422


def test_extra_fields_are_rejected(client):
    payload = broker_payload()
    payload["unexpected"] = "value"

    response = client.post("/api/v1/brokers", json=payload)

    assert response.status_code == 422
