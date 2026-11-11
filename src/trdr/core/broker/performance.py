"""Per-strategy performance from a strategy's own fills: holdings, cost, realized and unrealized P&L.

Every order is tagged with its strategy (client_order_id "<strategy>:<id>"), so a strategy's
performance can be rebuilt from the broker's order history alone, even in a shared account.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Dict, List

from .models import Order, Position


def strategy_performance(orders: List[Order], marks: Dict[str, Decimal]) -> dict:
    """Summarize one strategy's filled `orders`, marking open holdings at `marks` (symbol -> price;
    a holding without a mark is valued at cost).

    total_pnl = cash received from sells - cash paid for buys + value of what is still held.
    unrealized_pnl is the open holdings' gain over their cost; realized_pnl is the rest.
    """
    by_symbol: Dict[str, List[Order]] = defaultdict(list)
    net_cash_flow = Decimal(0)
    fills = 0
    for order in orders:
        if order.net_quantity_filled == 0 or order.avg_fill_price is None:
            continue
        fills += 1
        by_symbol[order.symbol].append(order)
        net_cash_flow -= order.net_quantity_filled * order.avg_fill_price.amount

    holdings = []
    for symbol, symbol_orders in sorted(by_symbol.items()):
        quantity = sum(o.net_quantity_filled for o in symbol_orders)
        if quantity == 0:
            continue
        cost = Position(symbol=symbol, orders=symbol_orders).average_cost.amount
        mark = Decimal(marks.get(symbol, cost))
        holdings.append({
            "symbol": symbol,
            "quantity": quantity,
            "average_cost": cost,
            "mark": mark,
            "value": quantity * mark,
            "unrealized_pnl": quantity * (mark - cost),
        })

    held_value = sum((h["value"] for h in holdings), Decimal(0))
    unrealized = sum((h["unrealized_pnl"] for h in holdings), Decimal(0))
    total = net_cash_flow + held_value
    return {
        "fills": fills,
        "holdings": holdings,
        "held_value": held_value,
        "net_cash_flow": net_cash_flow,
        "total_pnl": total,
        "unrealized_pnl": unrealized,
        "realized_pnl": total - unrealized,
    }
