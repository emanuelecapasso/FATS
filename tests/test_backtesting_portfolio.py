from datetime import UTC, datetime

import pytest

from backtesting.models import Execution, OrderSide
from backtesting.portfolio import Portfolio


def create_execution(
    side=OrderSide.BUY,
    quantity=10000,
    price=1.1000,
    spread_cost=0.0,
    commission=0.0,
    slippage=0.0,
):
    return Execution(
        order_id="order-001",
        pair="EUR/USD",
        side=side,
        quantity=quantity,
        price=price,
        spread_cost=spread_cost,
        commission=commission,
        slippage=slippage,
        executed_at=datetime.now(UTC),
    )


def test_portfolio_initialization():
    portfolio = Portfolio(initial_balance=10000)

    assert portfolio.balance == 10000
    assert portfolio.equity == 10000
    assert portfolio.realized_pnl == 0
    assert portfolio.unrealized_pnl == 0
    assert portfolio.gross_exposure == 0


def test_portfolio_rejects_invalid_initial_balance():
    with pytest.raises(ValueError):
        Portfolio(initial_balance=0)


def test_open_long_position():
    portfolio = Portfolio(initial_balance=10000)

    position = portfolio.open_position(create_execution(side=OrderSide.BUY))

    assert position.side == OrderSide.BUY
    assert position.quantity == 10000
    assert position.entry_price == 1.1000
    assert len(portfolio.positions) == 1


def test_open_position_deducts_transaction_costs():
    portfolio = Portfolio(initial_balance=10000)

    portfolio.open_position(
        create_execution(
            spread_cost=2.0,
            commission=1.0,
            slippage=0.5,
        )
    )

    assert portfolio.balance == pytest.approx(9996.5)
    assert portfolio.realized_pnl == pytest.approx(-3.5)


def test_unrealized_pnl_and_equity():
    portfolio = Portfolio(initial_balance=10000)

    position = portfolio.open_position(create_execution())

    portfolio.update_market_prices({"EUR/USD": 1.1050})

    assert portfolio.positions[position.position_id].unrealized_pnl == pytest.approx(
        50.0
    )
    assert portfolio.unrealized_pnl == pytest.approx(50.0)
    assert portfolio.equity == pytest.approx(10050.0)


def test_close_long_position():
    portfolio = Portfolio(initial_balance=10000)

    position = portfolio.open_position(create_execution(side=OrderSide.BUY))

    pnl = portfolio.close_position(
        position.position_id,
        create_execution(
            side=OrderSide.SELL,
            price=1.1050,
        ),
    )

    assert pnl == pytest.approx(50.0)
    assert portfolio.balance == pytest.approx(10050.0)
    assert portfolio.realized_pnl == pytest.approx(50.0)
    assert len(portfolio.positions) == 0


def test_close_short_position():
    portfolio = Portfolio(initial_balance=10000)

    position = portfolio.open_position(create_execution(side=OrderSide.SELL))

    pnl = portfolio.close_position(
        position.position_id,
        create_execution(
            side=OrderSide.BUY,
            price=1.0950,
        ),
    )

    assert pnl == pytest.approx(50.0)
    assert portfolio.balance == pytest.approx(10050.0)


def test_partial_position_close():
    portfolio = Portfolio(initial_balance=10000)

    position = portfolio.open_position(create_execution(side=OrderSide.BUY))

    pnl = portfolio.close_position(
        position.position_id,
        create_execution(
            side=OrderSide.SELL,
            quantity=4000,
            price=1.1050,
        ),
    )

    assert pnl == pytest.approx(20.0)
    assert portfolio.positions[position.position_id].quantity == 6000
    assert portfolio.balance == pytest.approx(10020.0)


def test_close_position_rejects_excess_quantity():
    portfolio = Portfolio(initial_balance=10000)

    position = portfolio.open_position(create_execution())

    with pytest.raises(ValueError):
        portfolio.close_position(
            position.position_id,
            create_execution(
                side=OrderSide.SELL,
                quantity=20000,
            ),
        )


def test_close_position_rejects_wrong_side():
    portfolio = Portfolio(initial_balance=10000)

    position = portfolio.open_position(create_execution())

    with pytest.raises(ValueError):
        portfolio.close_position(
            position.position_id,
            create_execution(side=OrderSide.BUY),
        )


def test_gross_exposure():
    portfolio = Portfolio(initial_balance=10000)

    portfolio.open_position(create_execution())

    assert portfolio.gross_exposure == pytest.approx(11000.0)
