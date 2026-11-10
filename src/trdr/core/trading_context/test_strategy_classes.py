"""RANK, returns/volatility, calendar identifiers, explicit universe, equity: the building blocks
for cross-sectional, asset-allocation and calendar strategies."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from .exceptions import MissingContextValue
from .trading_context import TradingContext
from ..bar_provider.models import Bar
from ..security_provider.models import Security
from ..shared.models import ContextIdentifier as C, Money, TradingDateTime
from ...dsl.dsl_ast import Identifier, RankExpression, BinaryExpression, BinaryOperator, Literal
from ...dsl.lexer import Lexer
from ...dsl.parser import Parser


def _security(symbol: str, closes: list[float], current: float, last_day: date = date(2026, 3, 27)) -> Security:
    """`closes` are daily history ending the session before `last_day`; `current` is last_day's close."""

    def bar(day: date, close: float) -> Bar:
        price = Money(amount=Decimal(str(close)))
        ts = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        return Bar(trading_datetime=TradingDateTime.from_utc(ts), open=price, high=price, low=price, close=price, volume=1)

    days = [last_day - timedelta(days=len(closes) - i) for i in range(len(closes))]
    return Security(symbol=symbol, bars=[bar(d, c) for d, c in zip(days, closes)], current_bar=bar(last_day, current))


def _context(securities: dict, universe=None, sessions=None, market_symbol=None, equity=None) -> TradingContext:
    provider = AsyncMock()
    provider.get_symbols.return_value = list(securities)
    async def get_security(symbol):
        return securities.get(symbol)

    provider.get_security.side_effect = get_security
    broker = AsyncMock()
    broker.get_position.return_value = None
    broker.get_equity.return_value = equity
    return asyncio.run(
        TradingContext.create(provider, broker, market_symbol=market_symbol, universe=universe, sessions=sessions)
    )


def _value_for(context: TradingContext, symbol: str, evaluate) -> Decimal:
    async def run():
        context.rewind()
        while await context.next_symbol():
            if context.current_symbol == symbol:
                return await evaluate(context)
        raise AssertionError(f"{symbol} not in universe")

    return asyncio.run(run())


def _identifier(identifier):
    return lambda ctx: ctx.get_value_for_identifier(identifier)


# 25 flat sessions at 100, then the current close: 21-session returns of +10%, +20%, -5%, 0%.
UNIVERSE = {
    "AAA": _security("AAA", [100.0] * 25, 110.0),
    "BBB": _security("BBB", [100.0] * 25, 120.0),
    "CCC": _security("CCC", [100.0] * 25, 95.0),
    "DDD": _security("DDD", [100.0] * 25, 100.0),
}


class TestReturnsAndVolatility:
    def test_return_over_n_sessions(self):
        ctx = _context(UNIVERSE)
        assert _value_for(ctx, "BBB", _identifier(C.RETURN_21D)) == Decimal("0.2")
        assert _value_for(ctx, "CCC", _identifier(C.RETURN_5D)) == Decimal("-0.05")

    def test_return_needs_enough_history(self):
        ctx = _context(UNIVERSE)
        with pytest.raises(MissingContextValue):
            _value_for(ctx, "AAA", _identifier(C.RETURN_63D))

    def test_volatility_is_annualized_std_dev_of_daily_returns(self):
        # Alternating +1% / -1% daily moves: daily std dev ~1.0%, annualized ~15.9%.
        closes = [100.0]
        for i in range(70):
            closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.99))
        ctx = _context({"ALT": _security("ALT", closes[:-1], closes[-1])})
        vol = _value_for(ctx, "ALT", _identifier(C.VOLATILITY_63D))
        assert Decimal("0.155") < vol < Decimal("0.165")

    def test_flat_prices_have_zero_volatility(self):
        ctx = _context({"FLAT": _security("FLAT", [100.0] * 30, 100.0)})
        assert _value_for(ctx, "FLAT", _identifier(C.VOLATILITY_21D)) == 0


class TestRank:
    def test_rank_one_is_the_largest_value(self):
        ctx = _context(UNIVERSE)
        rank = lambda c: c.rank(Identifier("RETURN_21D"))
        assert [_value_for(ctx, s, rank) for s in ("BBB", "AAA", "DDD", "CCC")] == [1, 2, 3, 4]

    def test_negating_ranks_smallest_first(self):
        ctx = _context(UNIVERSE)
        lowest_first = RankExpression(BinaryExpression(Literal(0), BinaryOperator.MINUS, Identifier("RETURN_21D")))
        assert _value_for(ctx, "CCC", lowest_first.evaluate) == 1

    def test_ties_share_the_better_rank(self):
        tied = {"X": _security("X", [100.0] * 25, 110.0), "Y": _security("Y", [100.0] * 25, 110.0)}
        ctx = _context(tied)
        rank = lambda c: c.rank(Identifier("RETURN_21D"))
        assert _value_for(ctx, "X", rank) == _value_for(ctx, "Y", rank) == 1

    def test_symbols_without_a_value_are_left_out_of_the_ranking(self):
        short = {**UNIVERSE, "NEW": _security("NEW", [100.0] * 5, 200.0)}  # too new for RETURN_21D
        ctx = _context(short)
        assert _value_for(ctx, "BBB", lambda c: c.rank(Identifier("RETURN_21D"))) == 1
        with pytest.raises(MissingContextValue):
            _value_for(ctx, "NEW", lambda c: c.rank(Identifier("RETURN_21D")))

    def test_each_symbol_is_evaluated_once_per_run(self):
        ctx = _context(UNIVERSE)
        calls = []

        class Counting(Identifier):
            async def evaluate(self, context):
                calls.append(context.current_symbol)
                return await super().evaluate(context)

        expr = Counting("RETURN_21D")

        async def rank_everyone():
            ctx.rewind()
            while await ctx.next_symbol():
                await ctx.rank(expr)

        asyncio.run(rank_everyone())
        assert sorted(calls) == sorted(UNIVERSE)

    def test_rank_in_a_strategy_condition(self):
        text = """STRATEGY
    NAME "xs"
    DESCRIPTION "top 2 by a monotonic transform of the 21-session return"
    ENTRY
        ALL_OF
            RANK((RETURN_21D + 1) * 2 - 1) <= 2
            RANK(-RETURN_21D) > 1
    EXIT
        ANY_OF
            RANK(RETURN_21D) > 3
    SIZING
        RULE
            DOLLAR_AMOUNT
                (ACCOUNT_EQUITY / 2)
"""
        ast = Parser(Lexer(text).tokenize()).parse()
        ctx = _context(UNIVERSE)
        assert _value_for(ctx, "BBB", ast.entry.evaluate) is True
        assert _value_for(ctx, "DDD", ast.entry.evaluate) is False


class TestUniverse:
    def test_market_symbol_can_also_be_traded_when_listed(self):
        securities = {"SPY": _security("SPY", [100.0] * 25, 101.0), "TLT": _security("TLT", [100.0] * 25, 99.0)}
        ctx = _context(securities, universe=["TLT", "SPY"], market_symbol="SPY")
        assert ctx.symbol_stack == ["TLT", "SPY"]
        assert ctx.market_security is not None

    def test_universe_symbols_without_data_are_skipped(self):
        ctx = _context(UNIVERSE, universe=["AAA", "ZZZ", "BBB"])
        assert ctx.symbol_stack == ["AAA", "BBB"]
        assert _value_for(ctx, "AAA", _identifier(C.UNIVERSE_SIZE)) == 2

    def test_account_equity(self):
        ctx = _context(UNIVERSE, equity=Money(amount=Decimal("123456.78")))
        assert _value_for(ctx, "AAA", _identifier(C.ACCOUNT_EQUITY)) == Decimal("123456.78")


class TestCalendar:
    # Decision session: Friday 2026-03-27. March 2026 sessions with an invented holiday on Mon 3/30.
    MARCH = [date(2026, 3, d) for d in range(1, 32) if date(2026, 3, d).weekday() < 5 and d != 30]

    def _ctx(self, sessions=None, last_day=date(2026, 3, 27)):
        return _context({"AAA": _security("AAA", [100.0] * 25, 100.0, last_day=last_day)}, sessions=sessions)

    def test_plain_date_parts(self):
        ctx = self._ctx(self.MARCH)
        assert _value_for(ctx, "AAA", _identifier(C.DAY_OF_WEEK)) == 5
        assert _value_for(ctx, "AAA", _identifier(C.DAY_OF_MONTH)) == 27
        assert _value_for(ctx, "AAA", _identifier(C.MONTH)) == 3

    def test_trading_days_use_the_session_calendar(self):
        ctx = self._ctx(self.MARCH)
        assert _value_for(ctx, "AAA", _identifier(C.TRADING_DAY_OF_MONTH)) == 20
        assert _value_for(ctx, "AAA", _identifier(C.TRADING_DAYS_LEFT_IN_MONTH)) == 1  # only 3/31 (3/30 closed)

    def test_last_session_of_month_has_zero_days_left(self):
        ctx = self._ctx(self.MARCH, last_day=date(2026, 3, 31))
        assert _value_for(ctx, "AAA", _identifier(C.TRADING_DAYS_LEFT_IN_MONTH)) == 0

    def test_without_a_calendar_weekdays_are_counted(self):
        ctx = self._ctx(None)
        assert _value_for(ctx, "AAA", _identifier(C.TRADING_DAYS_LEFT_IN_MONTH)) == 2  # Mon 3/30, Tue 3/31

    def test_calendar_that_misses_the_month_is_a_missing_value(self):
        ctx = self._ctx([date(2026, 4, 1)])
        with pytest.raises(MissingContextValue, match="2026-03"):
            _value_for(ctx, "AAA", _identifier(C.TRADING_DAY_OF_MONTH))


def test_nested_arithmetic_descriptions_are_parenthesized():
    expr = BinaryExpression(
        BinaryExpression(Identifier("RETURN_21D"), BinaryOperator.PLUS, Literal(1)), BinaryOperator.MULTIPLY, Literal(2)
    )
    assert expr.describe() == "(RETURN_21D + 1) * 2"
