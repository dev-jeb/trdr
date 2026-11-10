from enum import Enum
from decimal import Decimal
from typing import List
from pydantic import BaseModel, ConfigDict, model_validator

from ..shared.models import Money, TradingDateTime


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"


class OrderStatus(Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL_FILL = "PARTIAL_FILL"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


class Order(BaseModel):
    symbol: str
    quantity_filled: Decimal | None = Decimal(0)
    side: OrderSide
    type: OrderType
    status: OrderStatus
    avg_fill_price: Money | None = None
    created_at: TradingDateTime
    filled_at: TradingDateTime | None = None
    time_in_force: str | None = "gtc"
    current_price: Money | None = None
    quantity_requested: Decimal
    client_order_id: str
    strategy_name: str

    @property
    def net_quantity_filled(self) -> Decimal:
        return self.quantity_filled if self.side == OrderSide.BUY else -self.quantity_filled

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def validate_order_state(self) -> "Order":
        """Validate that the order state is consistent."""
        status = self.status

        if self.filled_at is not None and self.filled_at.is_weekend:
            raise ValueError("Orders can't be filled on a weekend")

        # Validation rules by status
        if status == OrderStatus.PENDING:
            if self.avg_fill_price is not None:
                raise ValueError("Pending orders cannot have a fill price")
            if self.quantity_filled != Decimal(0):
                raise ValueError("Pending orders must have quantity_filled = 0")
            if self.filled_at is not None:
                raise ValueError("Pending orders cannot have a filled_at time")
            if self.current_price is None:
                raise ValueError("Pending orders must have a current_price")

        elif status == OrderStatus.FILLED:
            if self.avg_fill_price is None:
                raise ValueError("Filled orders must have a fill price")
            if self.quantity_filled != self.quantity_requested:
                raise ValueError("Filled orders must have quantity_filled = quantity_requested")
            if self.filled_at is None:
                raise ValueError("Filled orders must have a filled_at timestamp")

        elif status == OrderStatus.PARTIAL_FILL:
            if self.avg_fill_price is None:
                raise ValueError("Partially filled orders must have a fill price")
            if self.quantity_filled >= self.quantity_requested:
                raise ValueError("Partially filled orders must have quantity_filled < quantity_requested")
            if self.quantity_filled <= Decimal(0):
                raise ValueError("Partially filled orders must have quantity_filled > 0")
            if self.filled_at is None:
                raise ValueError("Partially filled orders must have a filled_at timestamp")

        return self


class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Position(BaseModel):

    symbol: str
    orders: List[Order]

    def get_orders_created_after_dt(self, dt: TradingDateTime) -> List[Order]:
        if len(self.orders) == 0:
            return []
        return [order for order in self.orders if order.created_at.timestamp >= dt.timestamp]

    def get_orders_for_strategy(self, strategy_name: str) -> List[Order]:
        return [order for order in self.orders if order.strategy_name == strategy_name]

    @staticmethod
    def _orders_since_flat(orders: List[Order]) -> List[Order]:
        """The filled orders since the share count last returned to zero, oldest first.

        A broker may attach a symbol's whole order history (Alpaca does). Earlier round trips
        must not leak into the cost basis: buying at 50, selling at 56 and re-buying at 45
        would otherwise report an average cost of 39.
        """

        def when(order: Order):
            stamp = order.filled_at or order.created_at
            return stamp.timestamp if stamp is not None else None

        filled = [o for o in orders if o.net_quantity_filled != 0]
        dated = sorted((o for o in filled if when(o) is not None), key=when)
        undated = [o for o in filled if when(o) is None]  # keep their given order, after dated ones
        current: List[Order] = []
        running = Decimal(0)
        for order in dated + undated:
            current.append(order)
            running += order.net_quantity_filled
            if running == 0:
                current = []
        return current

    @staticmethod
    def _average_cost_of(orders: List[Order]) -> Money:
        """Net cost per share held: (buys - sells) / shares, so a partial sale's realized P&L
        adjusts the remaining basis (see test_average_cost_mixed_buy_sell_orders)."""
        size = abs(sum(order.net_quantity_filled for order in orders))
        if size == Decimal(0):
            return Money(amount=Decimal(0))
        return Money(amount=sum(order.net_quantity_filled * order.avg_fill_price.amount for order in orders) / size)

    def get_size_for_strategy(self, strategy_name: str) -> Decimal:
        strategy_orders = self.get_orders_for_strategy(strategy_name)
        if not strategy_orders:
            return Decimal(0)
        return abs(sum(order.net_quantity_filled for order in strategy_orders))

    def get_average_cost_for_strategy(self, strategy_name: str) -> Money:
        """Average cost of this strategy's own shares in the current holding period."""
        return self._average_cost_of(self._orders_since_flat(self.get_orders_for_strategy(strategy_name)))

    @property
    def side(self) -> PositionSide:
        if len(self.orders) == 0:
            return None
        net_quantity = sum(order.net_quantity_filled for order in self.orders)
        return PositionSide.LONG if net_quantity > 0 else PositionSide.SHORT

    @property
    def get_market_value(self) -> Money:
        if len(self.orders) == 0:
            return Money(amount=Decimal(0))
        total_market_value = Decimal(0)
        for order in self.orders:
            if order.net_quantity_filled and order.avg_fill_price:
                total_market_value += abs(order.net_quantity_filled) * order.avg_fill_price.amount
        return Money(amount=total_market_value)

    @property
    def size(self) -> Decimal:
        if len(self.orders) == 0:
            return Decimal(0)
        return abs(sum(order.net_quantity_filled for order in self.orders))

    @property
    def average_cost(self) -> Money:
        """Average cost of the whole position (all strategies) in the current holding period."""
        return self._average_cost_of(self._orders_since_flat(self.orders))

    def to_json(self) -> str:
        return self.model_dump_json()

    def __str__(self) -> str:
        return f"Position(symbol={self.symbol}, size={self.size}, average_cost={self.average_cost})"

    model_config = ConfigDict(arbitrary_types_allowed=True)
