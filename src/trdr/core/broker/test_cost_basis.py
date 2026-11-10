"""Cost basis must reflect the current holding period and, in a shared position, the strategy's own shares."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from unittest.mock import AsyncMock

import pytest

from .models import Order, OrderSide, OrderStatus, OrderType, Position
from ..shared.models import ContextIdentifier, Money, TradingDateTime
from ..trading_context.exceptions import MissingContextValue
from ..trading_context.trading_context import TradingContext

T0 = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)


def _order(side, qty, price, strategy="mr", day=0, filled=True):
    ts = TradingDateTime.from_utc(T0 + timedelta(days=day))
    return Order(
        symbol="INTC", side=side, type=OrderType.MARKET, quantity_requested=D(qty),
        quantity_filled=D(qty) if filled else D(0), avg_fill_price=Money(amount=D(str(price))) if filled else None,
        status=OrderStatus.FILLED if filled else OrderStatus.CANCELED, created_at=ts, filled_at=ts if filled else None,
        client_order_id=f"{strategy}:x", strategy_name=strategy,
    )


BUY, SELL = OrderSide.BUY, OrderSide.SELL


def test_earlier_round_trip_does_not_leak_into_cost():
    """Regression: buy 50, sell 56, re-buy 45 reported 39 (the old $600 profit leaked in)."""
    p = Position(symbol="INTC", orders=[_order(BUY, 100, 50, day=0), _order(SELL, 100, 56, day=1), _order(BUY, 100, 45, day=3)])
    assert p.average_cost.amount == D(45)
    assert p.get_average_cost_for_strategy("mr").amount == D(45)


def test_history_order_does_not_matter():
    """Alpaca returns newest first; the holding period must be found chronologically."""
    newest_first = [_order(BUY, 100, 45, day=3), _order(SELL, 100, 56, day=1), _order(BUY, 100, 50, day=0)]
    assert Position(symbol="INTC", orders=newest_first).average_cost.amount == D(45)


def test_unfilled_orders_are_ignored():
    p = Position(symbol="INTC", orders=[_order(BUY, 100, 50), _order(SELL, 100, 0, day=1, filled=False)])
    assert p.average_cost.amount == D(50)


def test_adding_within_a_holding_period_averages():
    p = Position(symbol="INTC", orders=[_order(BUY, 100, 50, day=0), _order(BUY, 100, 40, day=1)])
    assert p.average_cost.amount == D(45)


def test_each_strategy_sees_its_own_cost_in_a_shared_position():
    p = Position(symbol="INTC", orders=[_order(BUY, 421, "58.52", "mr"), _order(BUY, 300, 50, "lowvol", day=1)])
    assert p.get_average_cost_for_strategy("mr").amount == D("58.52")
    assert p.get_average_cost_for_strategy("lowvol").amount == D(50)
    assert round(p.average_cost.amount, 2) == D("54.97")  # blended: what AVERAGE_COST used to use


def _context_holding(position: Position) -> TradingContext:
    provider = AsyncMock()
    provider.get_symbols.return_value = ["INTC"]

    async def get_security(symbol):
        security = AsyncMock()
        security.symbol = symbol
        return security

    provider.get_security.side_effect = get_security
    broker = AsyncMock()
    broker.get_position.return_value = position
    ctx = asyncio.run(TradingContext.create(provider, broker))
    asyncio.run(ctx.next_symbol())
    return ctx


def test_average_cost_identifier_is_scoped_to_the_running_strategy():
    shared = Position(symbol="INTC", orders=[_order(BUY, 421, "58.52", "mr"), _order(BUY, 300, 50, "lowvol", day=1)])
    ctx = _context_holding(shared)
    ctx.strategy_name = "mr"
    assert asyncio.run(ctx.get_value_for_identifier(ContextIdentifier.AVERAGE_COST)) == D("58.52")
    ctx.strategy_name = "lowvol"
    assert asyncio.run(ctx.get_value_for_identifier(ContextIdentifier.AVERAGE_COST)) == D(50)
    ctx.strategy_name = "other"  # holds none of it
    with pytest.raises(MissingContextValue):
        asyncio.run(ctx.get_value_for_identifier(ContextIdentifier.AVERAGE_COST))
