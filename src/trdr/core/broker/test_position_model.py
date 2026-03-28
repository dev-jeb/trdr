import pytest
import datetime
import json
from decimal import Decimal
from typing import List

from ..shared.models import Money, TradingDateTime
from .models import Position, Order, OrderSide, OrderType, OrderStatus, PositionSide


def test_position_initialization(create_order):
    """Test creating a position with a list of orders works correctly"""
    order = create_order(symbol="AAPL")
    position = Position(symbol=order.symbol, orders=[order])
    assert position.orders == [order]

    empty_position = Position(symbol="AAPL", orders=[])
    assert empty_position.symbol == "AAPL"
    assert empty_position.orders == []


def test_get_orders_created_after_dt():
    """Test get_orders_created_after_dt filters correctly"""
    # Create orders with different creation times
    now = datetime.datetime.now(datetime.UTC)
    # Ensure we're on a weekday for filled_at
    while now.weekday() >= 5:  # 5=Saturday, 6=Sunday
        now = now - datetime.timedelta(days=1)

    yesterday = now - datetime.timedelta(days=1)
    # Ensure yesterday is a weekday
    while yesterday.weekday() >= 5:
        yesterday = yesterday - datetime.timedelta(days=1)

    day_before = now - datetime.timedelta(days=2)
    # Ensure day_before is a weekday
    while day_before.weekday() >= 5:
        day_before = day_before - datetime.timedelta(days=1)

    order1 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order2 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(yesterday),
        filled_at=TradingDateTime.from_utc(yesterday),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order3 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(day_before),
        filled_at=TradingDateTime.from_utc(day_before),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order1, order2, order3])

    # Filter for orders after yesterday
    cutoff_dt = TradingDateTime.from_utc(yesterday - datetime.timedelta(hours=1))
    filtered_orders = position.get_orders_created_after_dt(cutoff_dt)

    assert len(filtered_orders) == 2
    # First two orders should be included (now and yesterday)
    assert filtered_orders[0].created_at.timestamp >= cutoff_dt.timestamp
    assert filtered_orders[1].created_at.timestamp >= cutoff_dt.timestamp


def test_size_all_buy_orders():
    """Test size property calculates correctly for all buy orders"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order1 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order2 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("5"),
        quantity_filled=Decimal("5"),
        avg_fill_price=Money(amount=Decimal("120")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order1, order2])

    assert position.size == Decimal("15")  # 10 + 5
    assert position.side == PositionSide.LONG


def test_size_all_sell_orders():
    """Test size property calculates correctly for all sell orders"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order1 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.SELL,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order2 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("5"),
        quantity_filled=Decimal("5"),
        avg_fill_price=Money(amount=Decimal("120")),
        type=OrderType.MARKET,
        side=OrderSide.SELL,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order1, order2])

    assert position.size == Decimal("15")  # 10 + 5
    assert position.side == PositionSide.SHORT


def test_size_mixed_buy_sell_orders():
    """Test size property calculates correctly for mixed buy/sell orders"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order1 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order2 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("3"),
        quantity_filled=Decimal("3"),
        avg_fill_price=Money(amount=Decimal("120")),
        type=OrderType.MARKET,
        side=OrderSide.SELL,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order3 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("2"),
        quantity_filled=Decimal("2"),
        avg_fill_price=Money(amount=Decimal("90")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    order4 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("2"),
        quantity_filled=Decimal("1"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.PARTIAL_FILL,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order1, order2, order3, order4])

    assert position.size == Decimal("10")  # 10 - 3 + 2 + 1


def test_get_market_value():
    """Test get_market_value calculates correctly"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order1 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order2 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("5"),
        quantity_filled=Decimal("5"),
        avg_fill_price=Money(amount=Decimal("120")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order1, order2])

    # 10 * $100 + 5 * $120 = $1,000 + $600 = $1,600
    expected_value = Money(amount=Decimal("1600.00"))
    assert position.get_market_value == expected_value


def test_average_cost_single_buy_order():
    """Test average_cost calculates correctly for a single buy order"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order])

    assert position.average_cost == Money(amount=Decimal("100"))


def test_average_cost_multiple_buy_orders():
    """Test average_cost calculates correctly for multiple buy orders with different prices"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order1 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order2 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("5"),
        quantity_filled=Decimal("5"),
        avg_fill_price=Money(amount=Decimal("120")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order1, order2])

    # (10 * 100 + 5 * 120) / 15 = (1000 + 600) / 15 = 1600 / 15 = 106.67
    expected_cost = Money(amount=Decimal("106.67"))
    assert position.average_cost == expected_cost


def test_average_cost_mixed_buy_sell_orders():
    """Test average_cost calculates correctly for mixed buy/sell orders"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order1 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order2 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("3"),
        quantity_filled=Decimal("3"),
        avg_fill_price=Money(amount=Decimal("120")),
        type=OrderType.MARKET,
        side=OrderSide.SELL,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order3 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("2"),
        quantity_filled=Decimal("2"),
        avg_fill_price=Money(amount=Decimal("90")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order1, order2, order3])

    # Total cost: (10 * 100) - (3 * 120) + (2 * 90) = 1000 - 360 + 180 = 820
    # Total size: 10 - 3 + 2 = 9
    # Average cost: 820 / 9 = 91.11
    expected_cost = Money(amount=Decimal("91.11"))
    assert position.average_cost == expected_cost


def test_to_json():
    """Test to_json returns valid JSON"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order])

    json_str = position.to_json()
    # Verify it's valid JSON by parsing it
    json_obj = json.loads(json_str)

    assert json_obj["symbol"] == "AAPL"
    assert len(json_obj["orders"]) == 1


def test_str_representation():
    """Test __str__ returns the expected string representation"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order])

    str_repr = str(position)
    assert "Position(symbol=AAPL, size=10, average_cost=USD 100.00)" == str_repr


def test_position_with_zero_orders():
    """Test position with zero orders returns sensible values"""
    position = Position(symbol="AAPL", orders=[])

    assert position.size == Decimal("0")
    assert position.get_market_value == Money(amount=Decimal("0"))


def test_position_with_only_pending_orders():
    """Test position with only pending orders has zero size"""
    now = datetime.datetime.now(datetime.UTC)

    order1 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("0"),
        avg_fill_price=None,
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal("100")),
        created_at=TradingDateTime.from_utc(now),
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order2 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("5"),
        quantity_filled=Decimal("0"),
        avg_fill_price=None,
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal("100")),
        created_at=TradingDateTime.from_utc(now),
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order1, order2])

    assert position.size == Decimal("0")
    assert position.get_market_value == Money(amount=Decimal("0"))


def test_position_with_rejected_cancelled_orders():
    """Test position with rejected/cancelled orders excludes those from calculations"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order1 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order2 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("5"),
        quantity_filled=Decimal("0"),
        avg_fill_price=None,
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.CANCELED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order3 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("3"),
        quantity_filled=Decimal("0"),
        avg_fill_price=None,
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.REJECTED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order1, order2, order3])

    # Only the filled order should contribute to the position size
    assert position.size == Decimal("10")


def test_position_with_mixed_filled_and_partial_fill_orders():
    """Test position with mixed filled and partial fill orders"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order1 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order2 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("5"),
        quantity_filled=Decimal("3"),
        avg_fill_price=Money(amount=Decimal("120")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.PARTIAL_FILL,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order1, order2])

    # Both filled and partially filled should be included
    assert position.size == Decimal("13")  # 10 + 3


def test_market_value_with_negative_position_size():
    """Test handling of market value with negative position size"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.SELL,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order])

    # Negative position should have positive market value
    expected_value = Money(amount=Decimal("1000.00"))
    assert position.get_market_value == expected_value
    assert position.side == PositionSide.SHORT


def test_changes_to_orders_reflected_in_position():
    """Test that changes to orders in the position are reflected in position properties"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order])
    assert position.size == Decimal("10")

    # Modify the order
    position.orders[0].quantity_filled = Decimal("8")

    # The position size should reflect the change
    assert position.size == Decimal("8")


def test_adding_orders_to_position():
    """Test adding new orders to the position"""
    # Ensure we're on a weekday for filled_at
    now = datetime.datetime.now(datetime.UTC)
    while now.weekday() >= 5:
        now = now - datetime.timedelta(days=1)

    order1 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("10"),
        quantity_filled=Decimal("10"),
        avg_fill_price=Money(amount=Decimal("100")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    position = Position(symbol="AAPL", orders=[order1])
    assert position.size == Decimal("10")

    # Add a new order
    order2 = Order(
        symbol="AAPL",
        quantity_requested=Decimal("5"),
        quantity_filled=Decimal("5"),
        avg_fill_price=Money(amount=Decimal("120")),
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        created_at=TradingDateTime.from_utc(now),
        filled_at=TradingDateTime.from_utc(now),
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    position.orders.append(order2)

    # The position size should include the new order
    assert position.size == Decimal("15")
    assert len(position.orders) == 2
