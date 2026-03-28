import pytest
from decimal import Decimal
import datetime

from .models import Order, OrderSide, OrderType, OrderStatus, Money
from ..shared.models import TradingDateTime


def test_creating_order_with_quantity(weekday_trading_datetime):
    """Order can be created with quantity_requested."""

    order = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("0"),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal("100")),
        avg_fill_price=None,
        created_at=weekday_trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    assert order.symbol == "AAPL"
    assert order.quantity_requested == Decimal("10")


def test_filled_order_validation(weekday_trading_datetime):
    """Filled orders must have quantity_filled equal to quantity_requested and valid fill data."""
    filled_at = weekday_trading_datetime + datetime.timedelta(minutes=5)

    # Valid filled order
    valid_order = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.FILLED,
        avg_fill_price=Money(amount=Decimal("150.00")),
        created_at=weekday_trading_datetime,
        filled_at=filled_at,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    assert valid_order is not None
    assert valid_order.status == OrderStatus.FILLED

    # Incorrect filled order (quantity_filled != quantity_requested)
    with pytest.raises(ValueError, match="Filled orders must have quantity_filled = quantity_requested"):
        Order(
            symbol="AAPL",
            quantity_requested=Decimal("10"),
            quantity_filled=Decimal("8"),  # Different from requested
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            status=OrderStatus.FILLED,
            avg_fill_price=Money(amount=Decimal("150.00")),
            created_at=weekday_trading_datetime,
            filled_at=filled_at,
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )

    # Missing fill price
    with pytest.raises(ValueError, match="Filled orders must have a fill price"):
        Order(
            symbol="AAPL",
            quantity_requested=Decimal("10"),
            quantity_filled=Decimal("10"),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            status=OrderStatus.FILLED,
            avg_fill_price=None,  # Missing fill price
            created_at=weekday_trading_datetime,
            filled_at=filled_at,
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )

    # Missing filled_at time
    with pytest.raises(ValueError, match="Filled orders must have a filled_at time"):
        Order(
            symbol="AAPL",
            quantity_requested=Decimal("10"),
            quantity_filled=Decimal("10"),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            status=OrderStatus.FILLED,
            avg_fill_price=Money(amount=Decimal("150.00")),
            created_at=weekday_trading_datetime,
            filled_at=None,  # Missing filled_at time
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )


def test_orders_cannot_be_filled_on_weekends(weekday_trading_datetime, weekend_trading_datetime):
    """Orders cannot be filled on weekends."""

    with pytest.raises(ValueError, match="Orders can't be filled on a weekend"):
        Order(
            symbol="AAPL",
            quantity_requested=Decimal("10"),
            quantity_filled=Decimal("10"),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            status=OrderStatus.FILLED,
            avg_fill_price=Money(amount=Decimal("150.00")),
            created_at=weekend_trading_datetime,
            filled_at=weekend_trading_datetime,
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )


def test_partial_fill_validation(weekday_trading_datetime):
    """Partially filled orders must have quantity_filled < quantity_requested."""

    filled_at = weekday_trading_datetime + datetime.timedelta(minutes=5)
    # Valid partially filled order
    valid_partial = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("5"),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.PARTIAL_FILL,
        avg_fill_price=Money(amount=Decimal("150.00")),
        created_at=weekday_trading_datetime,
        filled_at=filled_at,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    assert valid_partial is not None
    assert valid_partial.status == OrderStatus.PARTIAL_FILL

    # Incorrect partial fill (quantity_filled == quantity_requested)
    with pytest.raises(ValueError, match="Partially filled orders must have quantity_filled < quantity_requested"):
        Order(
            symbol="AAPL",
            quantity_requested=Decimal("10"),
            quantity_filled=Decimal("10"),  # Same as requested
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            status=OrderStatus.PARTIAL_FILL,
            avg_fill_price=Money(amount=Decimal("150.00")),
            created_at=weekday_trading_datetime,
            filled_at=filled_at,
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )

    # Incorrect partial fill (quantity_filled > quantity_requested)
    with pytest.raises(ValueError, match="Partially filled orders must have quantity_filled < quantity_requested"):
        Order(
            symbol="AAPL",
            quantity_requested=Decimal("10"),
            quantity_filled=Decimal("12"),  # Greater than requested
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            status=OrderStatus.PARTIAL_FILL,
            avg_fill_price=Money(amount=Decimal("150.00")),
            created_at=weekday_trading_datetime,
            filled_at=filled_at,
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )

    # Missing fill price
    with pytest.raises(ValueError, match="Partially filled orders must have a fill price"):
        Order(
            symbol="AAPL",
            quantity_requested=Decimal("10"),
            quantity_filled=Decimal("5"),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            status=OrderStatus.PARTIAL_FILL,
            avg_fill_price=None,  # Missing fill price
            created_at=weekday_trading_datetime,
            filled_at=filled_at,
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )

    # Missing filled_at time
    with pytest.raises(ValueError, match="Partially filled orders must have a filled_at time"):
        Order(
            symbol="AAPL",
            quantity_requested=Decimal("10"),
            quantity_filled=Decimal("5"),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            status=OrderStatus.PARTIAL_FILL,
            avg_fill_price=Money(amount=Decimal("150.00")),
            created_at=weekday_trading_datetime,
            filled_at=None,  # Missing filled_at time
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )


def test_pending_order_validation(weekday_trading_datetime):
    """Pending orders must have quantity_filled = 0 and no fill data."""
    filled_at = weekday_trading_datetime + datetime.timedelta(minutes=5)

    # Valid pending order
    valid_pending = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("0"),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal("100")),
        avg_fill_price=None,
        created_at=weekday_trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    assert valid_pending is not None
    assert valid_pending.status == OrderStatus.PENDING

    # Invalid pending order with non-zero quantity_filled
    with pytest.raises(ValueError, match="Pending orders must have quantity_filled = 0"):
        Order(
            symbol="AAPL",
            quantity_requested=Decimal("10"),
            quantity_filled=Decimal("5"),  # Should be 0 for pending
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            status=OrderStatus.PENDING,
            current_price=Money(amount=Decimal("100")),
            avg_fill_price=None,
            created_at=weekday_trading_datetime,
            filled_at=None,
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )

    # Invalid pending order with fill price
    with pytest.raises(ValueError, match="Pending orders cannot have a fill price"):
        Order(
            symbol="AAPL",
            quantity_requested=Decimal("10"),
            quantity_filled=Decimal("0"),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            status=OrderStatus.PENDING,
            current_price=Money(amount=Decimal("100")),
            avg_fill_price=Money(amount=Decimal("150.00")),  # Should be None for pending
            created_at=weekday_trading_datetime,
            filled_at=None,
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )

    # Invalid pending order with filled_at
    with pytest.raises(ValueError, match="Pending orders cannot have a filled_at time"):
        Order(
            symbol="AAPL",
            quantity_requested=Decimal("10"),
            quantity_filled=Decimal("0"),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            status=OrderStatus.PENDING,
            current_price=Money(amount=Decimal("100")),
            avg_fill_price=None,
            created_at=weekday_trading_datetime,
            filled_at=filled_at,  # Should be None for pending
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )


def test_cancelled_and_rejected_orders(weekday_trading_datetime):
    """Cancelled and rejected orders have special validation rules."""
    filled_at = weekday_trading_datetime + datetime.timedelta(minutes=5)

    # Cancelled order with no fills
    cancelled_no_fill = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("0"),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.CANCELED,
        avg_fill_price=None,
        created_at=weekday_trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    assert cancelled_no_fill is not None
    assert cancelled_no_fill.status == OrderStatus.CANCELED

    # Rejected order
    rejected_order = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("0"),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.REJECTED,
        avg_fill_price=None,
        created_at=weekday_trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    assert rejected_order is not None
    assert rejected_order.status == OrderStatus.REJECTED

    # Partially filled then cancelled
    partial_cancelled = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("5"),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.CANCELED,
        avg_fill_price=Money(amount=Decimal("150.00")),
        created_at=weekday_trading_datetime,
        filled_at=filled_at,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    assert partial_cancelled is not None
    assert partial_cancelled.status == OrderStatus.CANCELED
    assert partial_cancelled.quantity_filled < partial_cancelled.quantity_requested


def test_string_representation(weekday_trading_datetime):
    """Order string representation should contain key information."""
    filled_at = weekday_trading_datetime + datetime.timedelta(minutes=5)

    order = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=OrderStatus.FILLED,
        avg_fill_price=Money(amount=Decimal("150.00")),
        created_at=weekday_trading_datetime,
        filled_at=filled_at,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    str_repr = str(order)
    assert "AAPL" in str_repr
    assert "buy" in str_repr
    assert "FILLED" in str_repr
    assert "10" in str_repr


def test_single_order_generator():
    """OrderGenerator should produce valid Order instances."""
    from ...test_utils.order_generator import OrderGenerator, OrderCriteria

    # Create a simple order
    criteria = OrderCriteria(
        count=1,
        symbol="AAPL",
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
    )

    generator = OrderGenerator(criteria)
    orders = generator.generate_orders()

    # Verify we got an order back
    assert len(orders) == 1

    order = orders[0]
    assert order.symbol == "AAPL"
    assert order.side == OrderSide.BUY
    assert order.status == OrderStatus.FILLED
    assert order.quantity_filled == order.quantity_requested
    assert order.avg_fill_price is not None


def test_multiple_order_generation():
    """OrderGenerator should generate multiple orders when requested."""
    from ...test_utils.order_generator import OrderGenerator, OrderCriteria

    # Create multiple orders
    criteria = OrderCriteria(
        count=5,
        symbol="TSLA",
        side=OrderSide.SELL,
        status=OrderStatus.FILLED,
    )

    generator = OrderGenerator(criteria)
    orders = generator.generate_orders()

    # Verify we got the right number of orders
    assert len(orders) == 5

    # Check that all orders match criteria
    for order in orders:
        assert order.symbol == "TSLA"
        assert order.side == OrderSide.SELL
        assert order.status == OrderStatus.FILLED
        assert order.quantity_filled == order.quantity_requested
        assert order.avg_fill_price is not None


def test_order_generation_with_custom_criteria():
    """OrderGenerator should respect custom criteria."""
    from ...test_utils.order_generator import OrderGenerator, OrderCriteria

    # Create order with custom criteria
    criteria = OrderCriteria(
        count=1,
        symbol="MSFT",
        side=OrderSide.BUY,
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal("100")),
        quantity_min=Decimal("90"),
        quantity_max=Decimal("100"),
    )

    generator = OrderGenerator(criteria)
    orders = generator.generate_orders()

    order = orders[0]
    assert order.symbol == "MSFT"
    assert order.side == OrderSide.BUY
    assert order.status == OrderStatus.PENDING
    assert order.current_price is not None
    assert order.quantity_filled == Decimal("0")  # Pending order
    assert order.avg_fill_price is None  # Pending order
