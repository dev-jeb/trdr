from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from .models import Order, OrderSide, OrderStatus, OrderType
from .performance import strategy_performance
from ..shared.models import Money, TradingDateTime

T0 = datetime(2026, 11, 2, 15, tzinfo=timezone.utc)


def _fill(symbol, side, qty, price, day=0):
    ts = TradingDateTime.from_utc(T0 + timedelta(days=day))
    return Order(symbol=symbol, side=side, type=OrderType.MARKET, quantity_requested=D(qty), quantity_filled=D(qty),
                 avg_fill_price=Money(amount=D(str(price))), status=OrderStatus.FILLED, created_at=ts, filled_at=ts,
                 client_order_id="s:x", strategy_name="s")


def test_realized_and_unrealized_pnl():
    orders = [
        _fill("AAA", OrderSide.BUY, 100, 50, 0), _fill("AAA", OrderSide.SELL, 100, 56, 1),  # +600 realized
        _fill("BBB", OrderSide.BUY, 10, 200, 2),                                            # open, now 210
    ]
    perf = strategy_performance(orders, {"BBB": D(210)})
    assert perf["realized_pnl"] == D(600)
    assert perf["unrealized_pnl"] == D(100)
    assert perf["total_pnl"] == D(700)
    assert perf["held_value"] == D(2100)
    assert [(h["symbol"], h["quantity"], h["average_cost"]) for h in perf["holdings"]] == [("BBB", D(10), D(200))]


def test_reentry_after_a_round_trip_uses_the_new_cost():
    orders = [_fill("AAA", OrderSide.BUY, 100, 50, 0), _fill("AAA", OrderSide.SELL, 100, 56, 1),
              _fill("AAA", OrderSide.BUY, 100, 45, 3)]
    perf = strategy_performance(orders, {"AAA": D(47)})
    assert perf["holdings"][0]["average_cost"] == D(45)
    assert perf["unrealized_pnl"] == D(200)
    assert perf["realized_pnl"] == D(600)


def test_missing_mark_values_at_cost():
    perf = strategy_performance([_fill("AAA", OrderSide.BUY, 10, 100)], {})
    assert perf["held_value"] == D(1000) and perf["unrealized_pnl"] == 0
