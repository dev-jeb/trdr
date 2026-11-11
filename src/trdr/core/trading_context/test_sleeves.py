"""Sleeves: several strategies share one account, each sized against its own budget."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from unittest.mock import AsyncMock

import pytest

from .trading_context import TradingContext
from ..bar_provider.models import Bar
from ..broker.models import Order, OrderSide, OrderStatus, OrderType, Position
from ..security_provider.models import Security
from ..shared.models import ContextIdentifier as C, Money, TradingDateTime

FILL = TradingDateTime.from_utc(datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc))


def _security(symbol: str, close: float) -> Security:
    def bar(i):
        price = Money(amount=D(str(close)))
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
        return Bar(trading_datetime=TradingDateTime.from_utc(ts), open=price, high=price, low=price, close=price, volume=1)

    return Security(symbol=symbol, bars=[bar(i) for i in range(30)], current_bar=bar(30))


def _buy(symbol, qty, price, strategy):
    return Order(symbol=symbol, side=OrderSide.BUY, type=OrderType.MARKET, quantity_requested=D(qty),
                 quantity_filled=D(qty), avg_fill_price=Money(amount=D(price)), status=OrderStatus.FILLED,
                 created_at=FILL, filled_at=FILL, client_order_id=f"{strategy}:x", strategy_name=strategy)


# One account: equity 100k, cash 60k. Strategy "a" holds 100 AAA (now 110); "b" holds 200 BBB (now 50).
POSITIONS = {
    "AAA": Position(symbol="AAA", orders=[_buy("AAA", 100, 100, "a")]),
    "BBB": Position(symbol="BBB", orders=[_buy("BBB", 200, 45, "b")]),
}
SECURITIES = {"AAA": _security("AAA", 110), "BBB": _security("BBB", 50), "CCC": _security("CCC", 20)}


def _context(strategy, weight, cash=60_000):
    provider = AsyncMock()
    provider.get_symbols.return_value = list(SECURITIES)

    async def get_security(symbol):
        return SECURITIES.get(symbol)

    provider.get_security.side_effect = get_security
    broker = AsyncMock()
    broker.get_positions.return_value = POSITIONS
    async def get_position(symbol):
        return POSITIONS.get(symbol)

    broker.get_position.side_effect = get_position
    broker.get_equity.return_value = Money(amount=D(100_000))
    broker.get_available_cash.return_value = Money(amount=D(cash))
    broker.get_account_exposure.return_value = D("0.4")
    ctx = asyncio.run(TradingContext.create(provider, broker, sleeve_weight=weight))
    ctx.strategy_name = strategy
    asyncio.run(ctx.next_symbol())
    return ctx


def _get(ctx, identifier):
    return asyncio.run(ctx.get_value_for_identifier(identifier))


def test_sleeve_scopes_the_account_identifiers():
    ctx = _context("a", 0.4)
    assert _get(ctx, C.ACCOUNT_EQUITY) == D(40_000)            # 0.4 x 100k
    assert _get(ctx, C.AVAILABLE_CASH) == D(29_000)            # 40k budget - 11k held (100 x 110)
    assert _get(ctx, C.NUMBER_OF_OPEN_POSITIONS) == 1          # only its own AAA, not b's BBB
    assert _get(ctx, C.ACCOUNT_EXPOSURE) == D("0.275")         # 11k / 40k


def test_budget_spent_leaves_no_cash():
    assert _get(_context("a", 0.1), C.AVAILABLE_CASH) == 0    # 10k budget, 11k already held


def test_account_cash_is_the_hard_limit():
    assert _get(_context("a", 1.0, cash=20_000), C.AVAILABLE_CASH) == D(20_000)


def test_without_a_sleeve_the_whole_account_is_used():
    ctx = _context("a", None)
    assert _get(ctx, C.AVAILABLE_CASH) == D(60_000)
    assert _get(ctx, C.NUMBER_OF_OPEN_POSITIONS) == 2
    assert _get(ctx, C.ACCOUNT_EQUITY) == D(100_000)


@pytest.mark.parametrize("weight", [0, -0.1, 1.5])
def test_weight_must_be_a_share_of_the_account(weight):
    with pytest.raises(ValueError, match="sleeve_weight"):
        _context("a", weight)
