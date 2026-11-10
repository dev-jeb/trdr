import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from .exceptions import MissingContextValue
from .trading_context import TradingContext
from ..bar_provider.models import Bar
from ..security_provider.models import Security
from ..shared.models import ContextIdentifier, Money, TradingDateTime


def _security(symbol: str, closes: list[float], current_close: float) -> Security:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def bar(i: int, close: float) -> Bar:
        price = Money(amount=Decimal(str(close)))
        return Bar(
            trading_datetime=TradingDateTime.from_utc(start + timedelta(days=i)),
            open=price, high=price, low=price, close=price, volume=1000,
        )

    bars = [bar(i, c) for i, c in enumerate(closes)]
    return Security(symbol=symbol, bars=bars, current_bar=bar(len(closes), current_close))


# SPY: 150 days at 100 then 100 days at 120 -> MA200 = (100*100 + 100*120)/200 = 110, MA50 = 120.
SPY = _security("SPY", [100.0] * 150 + [120.0] * 100, current_close=125.0)
AAPL = _security("AAPL", [50.0] * 250, current_close=50.0)


def _context(market_symbol: str | None, securities: dict) -> TradingContext:
    security_provider = AsyncMock()
    security_provider.get_symbols.return_value = list(securities)

    async def get_security(symbol):
        return securities.get(symbol)

    security_provider.get_security.side_effect = get_security
    broker = AsyncMock()
    broker.get_position.return_value = None
    return asyncio.run(TradingContext.create(security_provider, broker, market_symbol=market_symbol))


def _value(context: TradingContext, identifier: ContextIdentifier) -> Decimal:
    async def run():
        await context.next_symbol()
        return await context.get_value_for_identifier(identifier)

    return asyncio.run(run())


def test_market_symbol_is_not_traded():
    context = _context("SPY", {"AAPL": AAPL, "SPY": SPY})
    assert context.symbol_stack == ["AAPL"]


@pytest.mark.parametrize(
    "identifier, expected",
    [
        (ContextIdentifier.MARKET_PRICE, Decimal("125")),
        (ContextIdentifier.MARKET_MA50, Decimal("120")),
        (ContextIdentifier.MARKET_MA200, Decimal("110")),
    ],
)
def test_market_identifiers_come_from_the_benchmark_not_the_current_symbol(identifier, expected):
    context = _context("SPY", {"AAPL": AAPL, "SPY": SPY})
    assert _value(context, identifier) == expected
    assert context.current_symbol == "AAPL"


def test_market_identifier_without_market_symbol_is_a_configuration_error():
    context = _context(None, {"AAPL": AAPL})
    with pytest.raises(ValueError, match="market_symbol"):
        _value(context, ContextIdentifier.MARKET_PRICE)


def test_market_identifier_without_benchmark_data_is_a_missing_value():
    context = _context("SPY", {"AAPL": AAPL})  # provider has no SPY data
    with pytest.raises(MissingContextValue, match="SPY"):
        _value(context, ContextIdentifier.MARKET_MA200)


def test_market_ma200_needs_200_bars():
    short_spy = _security("SPY", [100.0] * 120, current_close=100.0)
    context = _context("SPY", {"AAPL": AAPL, "SPY": short_spy})
    with pytest.raises(MissingContextValue):
        _value(context, ContextIdentifier.MARKET_MA200)
