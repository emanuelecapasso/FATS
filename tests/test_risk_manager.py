"""Tests for FATS risk management."""

import pytest

from backtesting.models import Order, OrderSide
from backtesting.risk_manager import RiskManager


@pytest.fixture
def risk_manager():
    return RiskManager(
        max_risk_per_trade=0.01,
        max_exposure_pct=0.50,
        max_drawdown_pct=0.20,
    )


@pytest.fixture
def buy_order():
    return Order(
        pair="EUR/USD",
        side=OrderSide.BUY,
        quantity=1_000,
    )


def test_calculate_position_size(risk_manager):
    quantity = risk_manager.calculate_position_size(
        equity=10_000,
        entry_price=1.10,
        stop_loss=1.09,
    )

    assert quantity == pytest.approx(10_000)


def test_position_size_uses_currency_conversion(risk_manager):
    quantity = risk_manager.calculate_position_size(
        equity=10_000,
        entry_price=150.0,
        stop_loss=149.0,
        quote_to_account_rate=0.0067,
    )

    assert quantity == pytest.approx(14_925.373134)


def test_approve_valid_order(risk_manager, buy_order):
    decision = risk_manager.evaluate_order(
        order=buy_order,
        equity=10_000,
        entry_price=1.10,
        stop_loss=1.09,
    )

    assert decision.approved is True
    assert decision.reasons == []
    assert decision.recommended_quantity == pytest.approx(4545.454545)


def test_reject_order_exceeding_risk(risk_manager):
    order = Order(
        pair="EUR/USD",
        side=OrderSide.BUY,
        quantity=11_000,
    )

    decision = risk_manager.evaluate_order(
        order=order,
        equity=10_000,
        entry_price=1.10,
        stop_loss=1.09,
    )

    assert decision.approved is False
    assert decision.reasons


def test_reject_long_without_valid_stop(risk_manager, buy_order):
    decision = risk_manager.evaluate_order(
        order=buy_order,
        equity=10_000,
        entry_price=1.10,
        stop_loss=1.11,
    )

    assert decision.approved is False
    assert "Long position requires stop loss below entry" in decision.reasons


def test_reject_short_without_valid_stop(risk_manager):
    order = Order(
        pair="EUR/USD",
        side=OrderSide.SELL,
        quantity=1_000,
    )

    decision = risk_manager.evaluate_order(
        order=order,
        equity=10_000,
        entry_price=1.10,
        stop_loss=1.09,
    )

    assert decision.approved is False
    assert "Short position requires stop loss above entry" in decision.reasons


def test_reject_maximum_drawdown(risk_manager, buy_order):
    decision = risk_manager.evaluate_order(
        order=buy_order,
        equity=7_900,
        peak_equity=10_000,
        entry_price=1.10,
        stop_loss=1.09,
    )

    assert decision.approved is False
    assert "Maximum portfolio drawdown reached" in decision.reasons


def test_reject_excessive_existing_exposure(risk_manager):
    order = Order(
        pair="EUR/USD",
        side=OrderSide.BUY,
        quantity=1_000,
    )

    decision = risk_manager.evaluate_order(
        order=order,
        equity=10_000,
        entry_price=1.10,
        stop_loss=1.09,
        current_exposure=5_000,
    )

    assert decision.approved is False
    assert decision.recommended_quantity == 0


@pytest.mark.parametrize(
    ("equity", "entry", "stop"),
    [
        (0, 1.10, 1.09),
        (10_000, 0, 1.09),
        (10_000, 1.10, 0),
        (10_000, 1.10, 1.10),
    ],
)
def test_reject_invalid_position_sizing_inputs(risk_manager, equity, entry, stop):
    with pytest.raises(ValueError):
        risk_manager.calculate_position_size(
            equity=equity,
            entry_price=entry,
            stop_loss=stop,
        )


def test_reject_invalid_conversion_rate(risk_manager):
    with pytest.raises(ValueError):
        risk_manager.calculate_position_size(
            equity=10_000,
            entry_price=1.10,
            stop_loss=1.09,
            quote_to_account_rate=0,
        )


def test_reject_peak_equity_below_current_equity(risk_manager, buy_order):
    with pytest.raises(ValueError):
        risk_manager.evaluate_order(
            order=buy_order,
            equity=10_000,
            peak_equity=9_000,
            entry_price=1.10,
            stop_loss=1.09,
        )
