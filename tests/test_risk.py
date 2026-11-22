from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from core.schemas import (
    DataProvenance,
    DataQuality,
    DataSourceKind,
    MarketBar,
    MarketDataSnapshot,
    RiskAccountState,
    RiskConfiguration,
    Signal,
    SignalDirection,
    StrategyConfiguration,
    StrategyId,
    Timeframe,
    TimeframeSeries,
    VolumeType,
)
from quantitative.risk import DeterministicRiskEvaluator

AS_OF = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
def risk_configuration():
    return RiskConfiguration(
        max_risk_per_position_pct=0.01,
        max_aggregate_exposure_pct=0.50,
        max_daily_loss_pct=0.05,
        max_drawdown_pct=0.10,
        stop_distance_pct=0.02,
        max_price_age_seconds=300,
        quantity_step=0.01,
    )


@pytest.fixture
def account():
    return RiskAccountState(
        equity=10_000.0,
        daily_start_equity=10_000.0,
        peak_equity=10_000.0,
        gross_exposure=0.0,
        price_to_account_currency_rate=1.0,
        position_direction=SignalDirection.FLAT,
        position_quantity=0.0,
    )


@pytest.fixture
def snapshot():
    provenance = DataProvenance(
        kind=DataSourceKind.BROKER, provider_id="risk-test", received_at=AS_OF
    )
    bar = MarketBar(
        timestamp=AS_OF,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume_type=VolumeType.UNAVAILABLE,
        is_closed=True,
    )
    return MarketDataSnapshot(
        pair="EURUSD",
        as_of=AS_OF,
        series={
            Timeframe.M5: TimeframeSeries(
                timeframe=Timeframe.M5,
                bars=[bar],
                provenance=provenance,
                quality=DataQuality.COMPLETE,
            )
        },
    )


def _evaluate(evaluator, signal, snapshot, account, config, direction=None):
    strategy_configuration = StrategyConfiguration(
        strategy_id=StrategyId.TREND_FOLLOWING,
        current_position=direction or account.position_direction,
    )
    return evaluator.evaluate(signal, snapshot, strategy_configuration, config, account)


def _long():
    return Signal(direction=SignalDirection.LONG, confidence=0.8)


def test_new_position_size_obeys_risk_budget_and_is_deterministic(
    risk_configuration, account, snapshot
):
    evaluator = DeterministicRiskEvaluator()
    first = _evaluate(evaluator, _long(), snapshot, account, risk_configuration)
    second = _evaluate(evaluator, _long(), snapshot, account, risk_configuration)

    assert first == second
    assert first.approved is True
    assert first.recommended_quantity == 50.0  # 100 USD risk / 2 USD per unit
    assert "risk:position_risk_sized" in first.reasons


def test_aggregate_exposure_limit_fails_closed_when_no_capacity(
    risk_configuration, account, snapshot
):
    config = risk_configuration.model_copy(update={"max_aggregate_exposure_pct": 0.10})
    account = account.model_copy(update={"gross_exposure": 1_000.0})
    decision = _evaluate(
        DeterministicRiskEvaluator(), _long(), snapshot, account, config
    )

    assert decision.approved is False
    assert decision.recommended_quantity == 0.0
    assert decision.reasons == ["risk:aggregate_exposure_limit"]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("equity", 9_500.0, "risk:daily_loss_limit"),
        ("equity", 9_000.0, "risk:drawdown_limit"),
    ],
)
def test_daily_loss_and_drawdown_thresholds_are_rejected(
    field, value, reason, risk_configuration, account, snapshot
):
    updates = {field: value}
    if reason == "risk:drawdown_limit":
        updates["peak_equity"] = 10_000.0
        risk_configuration = risk_configuration.model_copy(
            update={"max_daily_loss_pct": 0.20}
        )
    if reason == "risk:daily_loss_limit":
        updates["daily_start_equity"] = 10_000.0
    account = account.model_copy(update=updates)
    decision = _evaluate(
        DeterministicRiskEvaluator(), _long(), snapshot, account, risk_configuration
    )

    assert decision.approved is False
    assert decision.reasons == [reason]


def test_existing_position_over_position_risk_limit_is_rejected(
    risk_configuration, snapshot
):
    account = RiskAccountState(
        equity=10_000,
        daily_start_equity=10_000,
        peak_equity=10_000,
        gross_exposure=6_000,
        price_to_account_currency_rate=1.0,
        position_direction=SignalDirection.LONG,
        position_quantity=60.0,
    )
    decision = _evaluate(
        DeterministicRiskEvaluator(),
        _long(),
        snapshot,
        account,
        risk_configuration,
    )

    assert decision.approved is False
    assert decision.reasons == ["risk:position_risk_limit"]


def test_exact_risk_and_aggregate_caps_are_allowed(risk_configuration, snapshot):
    account = RiskAccountState(
        equity=10_000.0,
        daily_start_equity=10_000.0,
        peak_equity=10_000.0,
        gross_exposure=5_000.0,
        price_to_account_currency_rate=1.0,
        position_direction=SignalDirection.LONG,
        position_quantity=50.0,
    )
    decision = _evaluate(
        DeterministicRiskEvaluator(),
        _long(),
        snapshot,
        account,
        risk_configuration,
    )

    assert decision.approved is True
    assert decision.recommended_quantity == 50.0


def test_price_at_maximum_configured_age_is_accepted(
    risk_configuration, account, snapshot
):
    aged = snapshot.model_copy(update={"as_of": AS_OF + timedelta(seconds=300)})
    decision = _evaluate(
        DeterministicRiskEvaluator(), _long(), aged, account, risk_configuration
    )

    assert decision.approved is True


def test_missing_risk_configuration_or_account_fails_closed(
    risk_configuration, account, snapshot
):
    evaluator = DeterministicRiskEvaluator()
    strategy_configuration = StrategyConfiguration(
        strategy_id=StrategyId.TREND_FOLLOWING
    )
    missing_config = evaluator.evaluate(
        _long(), snapshot, strategy_configuration, None, account
    )
    missing_account = evaluator.evaluate(
        _long(), snapshot, strategy_configuration, risk_configuration, None
    )

    assert missing_config.approved is False
    assert missing_config.reasons == ["risk:configuration_missing"]
    assert missing_account.approved is False
    assert missing_account.reasons == ["risk:account_state_missing"]


def test_stale_or_unclosed_price_fails_closed(risk_configuration, account, snapshot):
    evaluator = DeterministicRiskEvaluator()
    stale = snapshot.model_copy(update={"as_of": AS_OF + timedelta(minutes=6)})
    unclosed_series = snapshot.series[Timeframe.M5].model_copy(
        update={
            "bars": [
                snapshot.series[Timeframe.M5]
                .bars[0]
                .model_copy(update={"is_closed": False})
            ]
        }
    )
    unclosed = snapshot.model_copy(update={"series": {Timeframe.M5: unclosed_series}})

    stale_result = _evaluate(evaluator, _long(), stale, account, risk_configuration)
    unclosed_result = _evaluate(
        evaluator, _long(), unclosed, account, risk_configuration
    )
    assert stale_result.approved is False
    assert stale_result.reasons == ["risk:price_stale"]
    assert unclosed_result.approved is False
    assert unclosed_result.reasons == ["risk:price_invalid_or_unclosed"]


def test_missing_price_fails_closed(risk_configuration, account, snapshot):
    missing = snapshot.model_copy(update={"series": {}})
    decision = _evaluate(
        DeterministicRiskEvaluator(), _long(), missing, account, risk_configuration
    )

    assert decision.approved is False
    assert decision.reasons == ["risk:price_unavailable"]


def test_flat_signal_is_allowed_but_never_recommends_quantity(
    risk_configuration, account, snapshot
):
    signal = Signal(direction=SignalDirection.FLAT, confidence=0.5)
    decision = _evaluate(
        DeterministicRiskEvaluator(), signal, snapshot, account, risk_configuration
    )

    assert decision.approved is True
    assert decision.recommended_quantity == 0.0


def test_account_state_rejects_inconsistent_position():
    with pytest.raises(ValidationError, match="FLAT requires zero quantity"):
        RiskAccountState(
            equity=100.0,
            daily_start_equity=100.0,
            peak_equity=100.0,
            gross_exposure=0.0,
            price_to_account_currency_rate=1.0,
            position_direction=SignalDirection.FLAT,
            position_quantity=1.0,
        )
