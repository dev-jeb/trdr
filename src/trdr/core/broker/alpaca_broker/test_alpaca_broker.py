import asyncio
from datetime import date, datetime, timedelta, timezone

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

    async def fake_list_orders(self, params):
        return closed_orders

    monkeypatch.setattr(AlpacaBroker, "_get_account_info", fake_account_info)
    monkeypatch.setattr(AlpacaBroker, "_refresh_positions", fake_refresh_positions)
    monkeypatch.setattr(AlpacaBroker, "_list_orders", fake_list_orders)


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



# --- Order pagination against a fake GET /v2/orders -------------------------------------------


class _FakeResponse:
    def __init__(self, payload, status: int = 200):
        self.status, self._payload = status, payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class FakeAlpacaSession:
    """Serves /v2/orders the way Alpaca does: newest first, capped at ``limit``, exclusive
    ``after``/``until``, filtered by ``symbols``. Also serves /v2/positions."""

    def __init__(self, orders: list, positions: list = ()):
        self.orders = sorted(orders, key=lambda o: datetime.fromisoformat(o["submitted_at"]), reverse=True)
        self.positions = list(positions)
        self.order_requests: list = []

    def get(self, url, headers=None, params=None):
        if url.endswith("/v2/positions"):
            return _FakeResponse(self.positions)
        params = dict(params or {})
        self.order_requests.append(params)
        result = self.orders
        if "symbols" in params:
            wanted = set(params["symbols"].split(","))
            result = [o for o in result if o["symbol"] in wanted]
        if "until" in params:
            until = datetime.fromisoformat(params["until"])
            result = [o for o in result if datetime.fromisoformat(o["submitted_at"]) < until]
        if "after" in params:
            after = datetime.fromisoformat(params["after"])
            result = [o for o in result if datetime.fromisoformat(o["submitted_at"]) > after]
        return _FakeResponse(result[: int(params.get("limit", 50))])

    async def close(self):
        pass


BASE_TS = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)


def _alpaca_order(i: int, symbol: str = "AAPL", submitted_at: datetime | None = None, **overrides) -> dict:
    ts = (submitted_at or BASE_TS + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    order = {
        "id": f"order-{i}",
        "symbol": symbol,
        "side": "buy",
        "type": "market",
        "qty": "1",
        "filled_qty": "1",
        "filled_avg_price": "100",
        "status": "filled",
        "submitted_at": ts,
        "filled_at": ts,
        "client_order_id": f"strategy:{i}",
    }
    return {**order, **overrides}


def _broker_with_session(monkeypatch, session: FakeAlpacaSession) -> AlpacaBroker:
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setattr(base_broker_module.aiohttp, "ClientSession", lambda: session)

    async def fake_account_info(self):
        return ACCOUNT_WITHOUT_DAYTRADE_COUNT

    monkeypatch.setattr(AlpacaBroker, "_get_account_info", fake_account_info)
    # Not via the context manager: that would close (and drop) the session the tests still use.
    return asyncio.run(AlpacaBroker.create(pdt_strategy=NunStrategy.create()))


class TestOrderPagination:
    def test_follows_pages_until_history_is_exhausted(self, monkeypatch):
        """Regression: a full 500-order page crashed on strptime and never fetched page two."""
        session = FakeAlpacaSession([_alpaca_order(i) for i in range(1234)])
        broker = _broker_with_session(monkeypatch, session)
        session.order_requests.clear()

        orders = asyncio.run(broker._list_orders({"status": "all", "symbols": "AAPL"}))

        assert len(orders) == 1234
        assert len({o["id"] for o in orders}) == 1234
        assert len(session.order_requests) == 3
        assert "until" not in session.order_requests[0] and "until" in session.order_requests[1]

    def test_keeps_orders_that_share_the_page_boundary_timestamp(self, monkeypatch):
        boundary = BASE_TS - timedelta(days=1)
        orders = [_alpaca_order(i) for i in range(499)]
        orders += [_alpaca_order(1000, submitted_at=boundary), _alpaca_order(1001, submitted_at=boundary)]
        session = FakeAlpacaSession(orders)
        broker = _broker_with_session(monkeypatch, session)

        fetched = asyncio.run(broker._list_orders({"status": "all"}))

        assert {o["id"] for o in fetched} >= {"order-1000", "order-1001"}
        assert len(fetched) == 501

    def test_stops_when_a_page_brings_nothing_new(self, monkeypatch):
        same_instant = [_alpaca_order(i, submitted_at=BASE_TS) for i in range(600)]
        session = FakeAlpacaSession(same_instant)
        broker = _broker_with_session(monkeypatch, session)
        session.order_requests.clear()

        fetched = asyncio.run(broker._list_orders({"status": "all"}))

        assert len(fetched) == 500  # can't page past 500 identical timestamps, but must not loop
        assert len(session.order_requests) == 2

    def test_refresh_positions_attaches_full_order_history(self, monkeypatch):
        history = [_alpaca_order(i) for i in range(600)]
        # Every run cancels pending orders first, so held symbols accumulate never-filled orders,
        # which Alpaca returns with null filled_avg_price. Regression: Decimal(None) crashed create().
        history.append(
            _alpaca_order(9999, status="canceled", filled_qty="0", filled_avg_price=None, filled_at=None)
        )
        session = FakeAlpacaSession(history, positions=[{"symbol": "AAPL"}])

        broker = _broker_with_session(monkeypatch, session)

        assert len(broker._positions["AAPL"].orders) == 601
