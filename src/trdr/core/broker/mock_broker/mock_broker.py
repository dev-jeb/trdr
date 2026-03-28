from opentelemetry import trace
from decimal import Decimal
from typing import List, Dict
from datetime import timedelta
from ..base_broker import BaseBroker
from ..models import Order, Position, OrderStatus, OrderSide, PositionSide
from ...shared.models import Money
from ....test_utils.position_generator import PositionGenerator, PositionCriteria
from ...shared.models import TradingDateTime


class MockBroker(BaseBroker):

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        """Disabled constructor - use MockBroker.create() instead."""
        raise TypeError("Use MockBroker.create() instead to create a new broker")

    async def _initialize(self):
        with self._tracer.start_as_current_span("mock_broker._initialize") as span:
            self._pending_orders: List[Order] = []
            self._cash = Money(amount=Decimal(100000))
            positions = PositionGenerator(criteria=PositionCriteria(count=3)).generate_positions()
            position_dict = {position.symbol: position for position in positions}
            self._positions: Dict[str, Position] = position_dict
            self._snapshot_of_positions: Dict[str, Position] = position_dict
            self._snapshot_of_cash = self._cash
            self._time_stamp = TradingDateTime.now()
            span.set_status(trace.StatusCode.OK)
            return

    async def _refresh_positions(self):
        with self._tracer.start_as_current_span("mock_broker._refresh_positions") as span:
            self._positions = self._snapshot_of_positions

            trading_datetime = TradingDateTime.now()
            while trading_datetime.is_weekend:
                trading_datetime = TradingDateTime.from_utc(trading_datetime.timestamp - timedelta(days=1))

            for order in self._pending_orders:
                order.status = OrderStatus.FILLED
                order.quantity_filled = order.quantity_requested
                order.avg_fill_price = Money(amount=Decimal(100))
                order.filled_at = trading_datetime
                for symbol, position in self._positions.items():
                    if order.symbol == symbol:
                        position.orders.append(order)
                        span.add_event(f"Added order to position {symbol}")
                        break
                else:
                    position = Position(symbol=order.symbol, orders=[order])
                    self._positions[position.symbol] = position
                    span.add_event(f"Created new position {position.symbol}")

            # check for positions with 0 size
            symbols_to_remove = [symbol for symbol, position in self._positions.items() if position.size == Decimal(0)]
            for symbol in symbols_to_remove:
                self._positions.pop(symbol)
                span.add_event(f"Removed position {symbol} with 0 size")

            span.set_status(trace.StatusCode.OK)

    async def _refresh_cash(self):
        with self._tracer.start_as_current_span("mock_broker._refresh_cash") as span:
            self._cash = self._snapshot_of_cash
            for order in self._pending_orders:
                if order.side == OrderSide.BUY:
                    if order.status in [OrderStatus.FILLED, OrderStatus.PARTIAL_FILL]:
                        self._cash -= Money(amount=order.quantity_filled * order.avg_fill_price.amount)
                else:
                    if order.status in [OrderStatus.FILLED, OrderStatus.PARTIAL_FILL]:
                        # if closing a short position, we need to add the cash back to the account
                        position = self._positions.get(order.symbol, None)
                        if position is None or position.side == PositionSide.LONG:
                            self._cash += Money(amount=order.quantity_filled * order.avg_fill_price.amount)
                        else:
                            if position.side == PositionSide.SHORT:
                                self._cash -= Money(amount=order.quantity_filled * order.avg_fill_price.amount)

            # we need the orders/position to be present for cash refresh to work
            # Therefore we reset the pending orders and snapshot of positions after the cash refresh
            self._pending_orders = []
            self._snapshot_of_positions = self._positions
            self._snapshot_of_cash = self._cash
            self._time_stamp = TradingDateTime.now()

            span.set_status(trace.StatusCode.OK)

    async def _refresh_equity(self):
        with self._tracer.start_as_current_span("mock_broker._refresh_equity") as span:
            self._equity = Money(
                amount=self._cash.amount
                + sum(position.get_market_value.amount for _, position in self._positions.items())
            )
            span.set_status(trace.StatusCode.OK)

    async def _refresh_day_trade_count(self):
        self._day_trade_count = 1

    async def _place_order(self, order: Order) -> None:
        with self._tracer.start_as_current_span("mock_broker._place_order") as span:
            try:
                if not hasattr(self, "_pending_orders"):
                    self._pending_orders = []

                self._pending_orders.append(order)

                span.add_event(f"Added pending order: {order.side.value} {order.quantity_requested} of {order.symbol}")
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR)
                raise
            else:
                span.set_status(trace.StatusCode.OK)

    async def _cancel_all_orders(self, strategy_name: str) -> None:
        with self._tracer.start_as_current_span("mock_broker._cancel_all_orders") as span:
            try:
                if hasattr(self, "_pending_orders"):
                    self._pending_orders = [
                        o for o in self._pending_orders if o.strategy_name != strategy_name
                    ]
                    span.add_event(f"Cleared pending orders for strategy: {strategy_name}")
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR)
                raise
            else:
                span.set_status(trace.StatusCode.OK)
