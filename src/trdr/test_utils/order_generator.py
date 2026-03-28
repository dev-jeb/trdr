from typing import List, Optional
from decimal import Decimal
import random
import datetime
import uuid
from pydantic import BaseModel, ConfigDict

from ..core.broker.models import Order, OrderSide, OrderType, OrderStatus
from ..core.shared.models import Money, TradingDateTime


class OrderCriteria(BaseModel):
    """Criteria for generating orders"""

    count: int
    symbol: str | None = None
    side: Optional[OrderSide] = None
    type: Optional[OrderType] = None
    status: Optional[OrderStatus] = None
    strategy_name: str = "test-strategy"
    price_range: tuple[Decimal, Decimal] = (Decimal("50"), Decimal("200"))
    quantity_range: tuple[int, int] = (1, 100)
    partial_fill_probability: float = 0.2  # Probability of partial fill when status is PARTIAL_FILL
    partial_fill_range: tuple[float, float] = (0.1, 0.9)  # Range for partial fill percentage

    model_config = ConfigDict(arbitrary_types_allowed=True)


class OrderGenerator:
    """Generates randomized orders for testing"""

    def __init__(self, criteria: OrderCriteria):
        self.criteria = criteria

    def generate_orders(self) -> List[Order]:
        """Generate a list of orders based on criteria"""
        orders = []

        base_datetime = datetime.datetime.now(datetime.UTC)
        # Ensure we're on a weekday
        while base_datetime.date().weekday() >= 5:
            base_datetime = base_datetime - datetime.timedelta(days=1)

        for _ in range(self.criteria.count):
            # Generate random trading date
            trading_date = base_datetime - datetime.timedelta(
                days=random.randint(0, 30), hours=random.randint(0, 8), minutes=random.randint(0, 59)
            )
            # Ensure we're on a weekday
            while trading_date.date().weekday() >= 5:
                trading_date = trading_date - datetime.timedelta(days=1)

            created_at = TradingDateTime.from_utc(trading_date)

            # Generate random parameters
            side = self.criteria.side or random.choice(list(OrderSide))
            order_type = self.criteria.type or random.choice(list(OrderType))
            status = self.criteria.status or random.choice(list(OrderStatus))

            # Generate price
            price_min, price_max = self.criteria.price_range
            price = Money(amount=Decimal(str(random.uniform(float(price_min), float(price_max)))))

            # Generate requested quantity
            quantity_min, quantity_max = self.criteria.quantity_range
            quantity_requested = Decimal(random.randint(quantity_min, quantity_max))

            # Determine filled quantity and fill price based on status
            quantity_filled = Decimal(0)
            fill_price = None
            filled_at = None

            if status == OrderStatus.FILLED:
                # For FILLED orders, quantity_filled must equal quantity_requested
                quantity_filled = quantity_requested
                # FILLED orders must have a fill price
                fill_price = price
                # Fill time is after created time
                fill_delay = datetime.timedelta(minutes=random.randint(1, 60))
                fill_time = trading_date + fill_delay
                # Ensure fill time isn't on weekend
                while fill_time.date().weekday() >= 5:
                    fill_time = fill_time + datetime.timedelta(days=1)
                filled_at = TradingDateTime.from_utc(fill_time)

            elif status == OrderStatus.PARTIAL_FILL:
                # For PARTIAL_FILL orders, quantity_filled must be less than quantity_requested
                min_pct, max_pct = self.criteria.partial_fill_range
                # Ensure the percentage is less than 1.0 to comply with validation
                max_pct = min(max_pct, 0.99)
                fill_pct = random.uniform(min_pct, max_pct)
                quantity_filled = (quantity_requested * Decimal(str(fill_pct))).quantize(Decimal("0.01"))

                # Ensure partial fill is at least 1 unit less than requested to validate properly
                if quantity_filled == quantity_requested:
                    quantity_filled = max(Decimal("0.01"), quantity_requested - Decimal("1.00"))

                # PARTIAL_FILL orders must have a fill price
                fill_price = price
                # Fill time is after created time
                fill_delay = datetime.timedelta(minutes=random.randint(1, 60))
                fill_time = trading_date + fill_delay
                # Ensure fill time isn't on weekend
                while fill_time.date().weekday() >= 5:
                    fill_time = fill_time + datetime.timedelta(days=1)
                filled_at = TradingDateTime.from_utc(fill_time)

            # For PENDING, CANCELLED, REJECTED orders:
            # - quantity_filled must be 0 (already set)
            # - fill_price must be None (already set)
            # - filled_at should be None (already set)

            # Create the order
            # Generate a random symbol if none provided
            symbol = self.criteria.symbol or "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=4))

            client_order_id = f"{self.criteria.strategy_name}:{uuid.uuid4().hex[:8]}"

            order = Order(
                symbol=symbol,
                quantity_requested=quantity_requested,
                quantity_filled=quantity_filled,
                side=side,
                type=order_type,
                status=status,
                avg_fill_price=fill_price,
                current_price=price,
                created_at=created_at,
                filled_at=filled_at,
                client_order_id=client_order_id,
                strategy_name=self.criteria.strategy_name,
            )

            orders.append(order)

            # Move back in time for next order
            base_datetime = base_datetime - datetime.timedelta(days=random.randint(1, 5))

        return orders


if __name__ == "__main__":
    # Example usage
    criteria = OrderCriteria(
        count=5,
        symbol="TSLA",
    )
    generator = OrderGenerator(criteria)
    orders = generator.generate_orders()

    for order in orders:
        print(
            f"{order.symbol} {order.side.value} {order.quantity_requested} @ {order.avg_fill_price} ({order.status.value})"
        )
