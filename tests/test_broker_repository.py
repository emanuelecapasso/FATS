
"""Tests for persistent broker configuration."""

from datetime import date, time

from data.broker_config import BrokerConfig
from database.broker_repository import BrokerRepository


def test_repository_saves_and_loads_broker(tmp_path):
    repository = BrokerRepository(tmp_path / "test.db")
    config = BrokerConfig(
        provider_id="broker_a",
        name="Broker A",
        timezone="Europe/London",
        sunday_open=time(17, 0),
        friday_close=time(16, 30),
        closed_dates=frozenset({date(2026, 12, 25)}),
    )

    repository.save(config)

    assert repository.get("broker_a") == config


def test_repository_updates_existing_broker(tmp_path):
    repository = BrokerRepository(tmp_path / "test.db")

    original = BrokerConfig(
        provider_id="broker_a",
        name="Original",
    )
    updated = BrokerConfig(
        provider_id="broker_a",
        name="Updated",
        timezone="Europe/London",
    )

    repository.save(original)
    repository.save(updated)

    assert repository.get("broker_a") == updated
    assert len(repository.list_all()) == 1


def test_repository_lists_brokers_in_stable_order(tmp_path):
    repository = BrokerRepository(tmp_path / "test.db")

    repository.save(BrokerConfig(provider_id="broker_b", name="Broker B"))
    repository.save(BrokerConfig(provider_id="broker_a", name="Broker A"))

    assert [
        config.provider_id for config in repository.list_all()
    ] == ["broker_a", "broker_b"]


def test_repository_deletes_broker(tmp_path):
    repository = BrokerRepository(tmp_path / "test.db")
    repository.save(BrokerConfig(provider_id="broker_a", name="Broker A"))

    assert repository.delete("broker_a") is True
    assert repository.delete("broker_a") is False
    assert repository.get("broker_a") is None


def test_broker_config_rejects_invalid_timezone():
    import pytest

    with pytest.raises(ValueError, match="Unknown market timezone"):
        BrokerConfig(
            provider_id="broker_a",
            name="Broker A",
            timezone="Invalid/Timezone",
        )


def test_broker_config_rejects_empty_provider_id():
    import pytest

    with pytest.raises(ValueError, match="provider_id"):
        BrokerConfig(provider_id="", name="Broker A")
