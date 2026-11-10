import asyncio
from datetime import date, datetime, timezone

import pytest

from .alpaca_broker import AlpacaBroker
from .. import base_broker as base_broker_module
from ..pdt.nun_strategy import NunStrategy

# Shape of GET /v2/account as Alpaca returns it since July 2026: no daytrade_count field.
ACCOUNT_WITHOUT_DAYTRADE_COUNT = {"status": "ACTIVE", "cash": "100000", "equity": "100000"}


def _order(symbol: str, side: str, filled_at: str | None, filled_qty: str = "10") -> dict:
    return {"symbol": symbol, "side": side, "filled_at": filled_at, "filled_qty": filled_qty}


class TestDayTradeWindowStart:
    def test_monday_reaches_back_to_previous_tuesday(self):
        assert AlpacaBroker._day_trade_window_start(date(2026, 11, 9)) == date(2026, 11, 3)

    def test_friday_reaches_back_to_monday(self):
        assert AlpacaBroker._day_trade_window_start(date(2026, 11, 6)) == date(2026, 11, 2)

    def test_wednesday_spans_the_weekend(self):
        assert AlpacaBroker._day_trade_window_start(date(2026, 11, 11)) == date(2026, 11, 5)


class TestCountDayTrades:
    WINDOW_START = date(2026, 11, 3)

    def test_buy_and_sell_same_day_is_one_day_trade(self):
        orders = [
            _order("AAPL", "buy", "2026-11-05T14:31:02.123456789Z"),
            _order("AAPL", "sell", "2026-11-05T19:30:10Z"),
        ]
        assert AlpacaBroker._count_day_trades(orders, self.WINDOW_START) == 1

    def test_overnight_hold_is_not_a_day_trade(self):
        orders = [
            _order("AAPL", "buy", "2026-11-05T14:31:02Z"),
            _order("AAPL", "sell", "2026-11-06T14:31:02Z"),
        ]
        assert AlpacaBroker._count_day_trades(orders, self.WINDOW_START) == 0

    def test_two_round_trips_count_twice(self):
        orders = [
            _order("AAPL", "buy", "2026-11-05T14:00:00Z"),
            _order("AAPL", "sell", "2026-11-05T15:00:00Z"),
            _order("AAPL", "buy", "2026-11-05T16:00:00Z"),
            _order("AAPL", "sell", "2026-11-05T17:00:00Z"),
        ]
        assert AlpacaBroker._count_day_trades(orders, self.WINDOW_START) == 2

    def test_counts_are_per_symbol(self):
        orders = [
            _order("AAPL", "buy", "2026-11-05T14:00:00Z"),
            _order("MSFT", "sell", "2026-11-05T15:00:00Z"),
            _order("TSLA", "buy", "2026-11-06T14:00:00Z"),
            _order("TSLA", "sell", "2026-11-06T15:00:00Z"),
        ]
        assert AlpacaBroker._count_day_trades(orders, self.WINDOW_START) == 1

    def test_fills_before_window_are_ignored(self):
        orders = [
            _order("AAPL", "buy", "2026-11-02T14:00:00Z"),
            _order("AAPL", "sell", "2026-11-02T15:00:00Z"),
        ]
        assert AlpacaBroker._count_day_trades(orders, self.WINDOW_START) == 0

    def test_unfilled_orders_are_ignored(self):
        orders = [
            _order("AAPL", "buy", "2026-11-05T14:00:00Z"),
            _order("AAPL", "sell", None, filled_qty="0"),  # canceled before filling
        ]
        assert AlpacaBroker._count_day_trades(orders, self.WINDOW_START) == 0


@pytest.fixture
def alpaca_env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")


def _stub_alpaca_http(monkeypatch, account: dict, closed_orders: list):
    async def fake_account_info(self):
        return account

    async def fake_refresh_positions(self):
        self._positions = {}

    async def fake_closed_orders(self, after):
        return closed_orders

    monkeypatch.setattr(AlpacaBroker, "_get_account_info", fake_account_info)
    monkeypatch.setattr(AlpacaBroker, "_refresh_positions", fake_refresh_positions)
    monkeypatch.setattr(AlpacaBroker, "_get_closed_orders_since", fake_closed_orders)


async def _create_and_close() -> AlpacaBroker:
    async with await AlpacaBroker.create(pdt_strategy=NunStrategy.create()) as broker:
        return broker


class TestCreate:
    def test_create_succeeds_without_daytrade_count(self, alpaca_env, monkeypatch):
        """Regression: create() raised 'Day trade count is not initialized' for every run."""
        today = datetime.now(timezone.utc).date().isoformat()  # the broker buckets fills by UTC date
        _stub_alpaca_http(
            monkeypatch,
            ACCOUNT_WITHOUT_DAYTRADE_COUNT,
            [_order("AAPL", "buy", f"{today}T14:00:00Z"), _order("AAPL", "sell", f"{today}T15:00:00Z")],
        )

        broker = asyncio.run(_create_and_close())

        assert broker._day_trade_count == 1

    def test_create_prefers_account_daytrade_count_when_present(self, alpaca_env, monkeypatch):
        _stub_alpaca_http(monkeypatch, {**ACCOUNT_WITHOUT_DAYTRADE_COUNT, "daytrade_count": 2}, [])

        broker = asyncio.run(_create_and_close())

        assert broker._day_trade_count == 2

    def test_failed_create_closes_http_session(self, alpaca_env, monkeypatch):
        sessions = []

        class TrackingSession:
            def __init__(self):
                self.closed = False
                sessions.append(self)

            async def close(self):
                self.closed = True

        async def failing_account_info(self):
            raise Exception("Failed to get account info: 401 unauthorized")

        monkeypatch.setattr(base_broker_module.aiohttp, "ClientSession", TrackingSession)
        monkeypatch.setattr(AlpacaBroker, "_refresh_positions", lambda self: asyncio.sleep(0))
        monkeypatch.setattr(AlpacaBroker, "_get_account_info", failing_account_info)

        with pytest.raises(Exception, match="401"):
            asyncio.run(AlpacaBroker.create(pdt_strategy=NunStrategy.create()))

        assert len(sessions) == 1 and sessions[0].closed

