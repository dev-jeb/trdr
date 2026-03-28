import pytest
import asyncio
from decimal import Decimal
from datetime import timedelta

from ..models import Order, Position, OrderStatus, OrderSide, OrderType, PositionSide
from ...shared.models import Money, TradingDateTime
from ...broker.pdt.exceptions import PDTRuleViolationException


def test_create_method(mock_broker_with_nun_strategy):
    """Test that the broker can be created using the factory method."""
    broker = mock_broker_with_nun_strategy
    assert broker is not None
    assert hasattr(broker, "_positions")
    assert hasattr(broker, "_cash")
    assert hasattr(broker, "_equity")
    assert hasattr(broker, "_day_trade_count")
    assert hasattr(broker, "_updated_dt")
    assert hasattr(broker, "_is_stale_flag")


def test_init_throws_error():
    """Test that direct initialization throws an error."""
    from ..mock_broker.mock_broker import MockBroker

    with pytest.raises(TypeError, match="Use MockBroker.create()"):
        MockBroker()


def test_initial_state(mock_broker_with_nun_strategy):
    """Test the initial state of the broker after creation."""
    broker = mock_broker_with_nun_strategy

    # Check cash
    assert broker._cash.amount == Decimal("100000")

    # Check positions
    assert isinstance(broker._positions, dict)
    assert len(broker._positions) == 3  # Default is 3 positions
    for symbol, position in broker._positions.items():
        assert isinstance(position, Position)
        assert position.symbol == symbol

    # Check snapshots
    assert broker._snapshot_of_cash == broker._cash
    assert broker._snapshot_of_positions == broker._positions


def test_get_position(mock_broker_with_nun_strategy):
    """Test retrieving a position by symbol."""
    broker = mock_broker_with_nun_strategy

    # Get a symbol that exists
    symbol = list(broker._positions.keys())[0]
    position = asyncio.run(broker.get_position(symbol))

    assert position is not None
    assert position.symbol == symbol

    # Get a position that doesn't exist
    position = asyncio.run(broker.get_position("NONEXISTENT"))
    assert position is None


def test_get_positions(mock_broker_with_nun_strategy):
    """Test retrieving all positions."""
    broker = mock_broker_with_nun_strategy

    positions = asyncio.run(broker.get_positions())

    assert positions is not None
    assert len(positions) == 3  # Default is 3 positions
    assert positions == broker._positions


def test_place_order(mock_broker_with_nun_strategy):
    """Test placing an order."""
    broker = mock_broker_with_nun_strategy

    trading_datetime = TradingDateTime.now()
    while trading_datetime.is_weekend:
        trading_datetime = TradingDateTime.from_utc(trading_datetime.timestamp - timedelta(days=1))
    # Create a new order
    order = Order(
        symbol="TEST",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity_requested=Decimal(100),
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    assert broker._pending_orders == []
    assert broker._is_stale_flag is False
    asyncio.run(broker.place_order(order))
    assert broker._pending_orders == [order]
    assert broker._is_stale_flag is True
    position = asyncio.run(broker.get_position("TEST"))
    assert broker._is_stale_flag is False
    assert position is not None
    assert position.symbol == "TEST"
    assert position.size == order.quantity_requested
    assert position.get_market_value == Money(amount=order.quantity_requested * order.avg_fill_price.amount)
    assert broker._cash.amount == Decimal("100000") - position.get_market_value.amount


def test_adding_to_existing_position(mock_broker_with_wiggle_strategy):
    """Test adding an order to an existing position."""
    broker = mock_broker_with_wiggle_strategy

    trading_datetime = TradingDateTime.now()
    while trading_datetime.is_weekend:
        trading_datetime = TradingDateTime.from_utc(trading_datetime.timestamp - timedelta(days=1))

    # Create a new order
    order = Order(
        symbol="TEST",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity_requested=Decimal(100),
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    asyncio.run(broker.place_order(order))
    position = asyncio.run(broker.get_position("TEST"))
    assert broker._is_stale_flag is False
    assert position is not None
    assert position.symbol == "TEST"
    assert position.size == order.quantity_requested

    trading_datetime += timedelta(minutes=1)

    # Add another order to the same position
    next_order = Order(
        symbol="TEST",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity_requested=Decimal(100),
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    asyncio.run(broker.place_order(next_order))
    position = asyncio.run(broker.get_position("TEST"))
    assert broker._is_stale_flag is False
    assert position is not None
    assert position.symbol == "TEST"
    assert position.size == order.quantity_requested + next_order.quantity_requested
    assert position.side == PositionSide.LONG
    avg_cost = (
        order.quantity_requested * order.avg_fill_price.amount
        + next_order.quantity_requested * next_order.avg_fill_price.amount
    ) / (order.quantity_requested + next_order.quantity_requested)
    assert position.average_cost == Money(amount=avg_cost)
    assert broker._cash.amount == Decimal("100000") - position.get_market_value.amount

    # close entire position
    close_order = Order(
        symbol="TEST",
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        quantity_requested=position.size,
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    asyncio.run(broker.place_order(close_order))
    position = asyncio.run(broker.get_position("TEST"))
    assert position is None
    assert broker._cash.amount == Decimal("100000")


def test_refresh_equity(mock_broker_with_nun_strategy):
    """Test that equity calculation is correct."""
    broker = mock_broker_with_nun_strategy
    trading_datetime = TradingDateTime.now()
    while trading_datetime.is_weekend:
        trading_datetime = TradingDateTime.from_utc(trading_datetime.timestamp - timedelta(days=1))

    # Get initial equity
    initial_equity = asyncio.run(broker.get_equity())
    positions = asyncio.run(broker.get_positions())
    current_positions_market_value = sum(position.get_market_value.amount for position in positions.values())
    current_equity = broker._cash + Money(amount=current_positions_market_value)
    assert initial_equity == current_equity

    # Place a buy order
    order = Order(
        symbol="TEST",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity_requested=Decimal(100),
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    asyncio.run(broker.place_order(order))
    assert broker._pending_orders == [order]
    assert broker._is_stale_flag is True

    # Refresh and verify equity = cash + position market values
    updated_equity = asyncio.run(broker.get_equity())
    assert broker._is_stale_flag is False
    positions = asyncio.run(broker.get_positions())
    current_positions_market_value = sum(position.get_market_value.amount for position in positions.values())
    current_equity = broker._cash + Money(amount=current_positions_market_value)
    assert updated_equity == current_equity


def test_cancel_all_orders(mock_broker_with_nun_strategy):
    """Test that cancel_all_orders clears pending orders."""
    broker = mock_broker_with_nun_strategy
    trading_datetime = TradingDateTime.now()
    while trading_datetime.is_weekend:
        trading_datetime = TradingDateTime.from_utc(trading_datetime.timestamp - timedelta(days=1))

    # Add a couple test orders
    order1 = Order(
        symbol="TEST1",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity_requested=Decimal(100),
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    order2 = Order(
        symbol="TEST2",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity_requested=Decimal(100),
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    asyncio.run(broker.place_order(order1))
    broker._is_stale_flag = False
    asyncio.run(broker.place_order(order2))
    assert len(broker._pending_orders) == 2

    # Cancel all orders for the strategy
    asyncio.run(broker.cancel_all_orders("test-strategy"))
    assert len(broker._pending_orders) == 0
    assert broker._is_stale_flag is True


def test_account_exposure(mock_broker_with_nun_strategy):
    """Test that account exposure is calculated correctly."""
    broker = mock_broker_with_nun_strategy
    trading_datetime = TradingDateTime.now()
    while trading_datetime.is_weekend:
        trading_datetime = TradingDateTime.from_utc(trading_datetime.timestamp - timedelta(days=1))

    # Get initial exposure
    initial_equity = asyncio.run(broker.get_equity())
    initial_exposure = asyncio.run(broker.get_account_exposure())

    # Place a buy order
    order = Order(
        symbol="TEST",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity_requested=Decimal(100),
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    asyncio.run(broker.place_order(order))

    # Check exposure increased
    new_exposure = asyncio.run(broker.get_account_exposure())
    assert new_exposure > initial_exposure

    # Verify exposure calculation
    positions = asyncio.run(broker.get_positions())
    total_position_value = sum(abs(p.get_market_value.amount) for p in positions.values())
    current_equity = asyncio.run(broker.get_equity())
    expected_exposure = total_position_value / current_equity.amount
    assert abs(new_exposure - expected_exposure) < Decimal("0.0001")  # Allow for small floating point differences


def test_pdt_cash_validation(mock_broker_with_nun_strategy):
    """Test that cash validation is correct for PDT rules."""
    broker = mock_broker_with_nun_strategy
    broker._snapshot_of_cash = Money(amount=Decimal(24999))
    broker._cash = Money(amount=Decimal(24999))
    broker._day_trade_count = 3
    order = Order(
        symbol="TEST",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity_requested=Decimal(10),
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=TradingDateTime.now(),
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    with pytest.raises(PDTRuleViolationException):
        asyncio.run(broker.place_order(order))
    broker._snapshot_of_cash = Money(amount=Decimal(26000))
    broker._cash = Money(amount=Decimal(26000))
    asyncio.run(broker.place_order(order))
    assert broker._is_stale_flag is True
    assert broker._pending_orders == [order]
    cash = asyncio.run(broker.get_available_cash())
    assert cash.amount == Decimal("25000")


def test_pdt_cash_validation_for_shorts(mock_broker_with_nun_strategy):
    """Test that cash validation works correctly when shorting with PDT rules."""
    broker = mock_broker_with_nun_strategy
    broker._snapshot_of_cash = Money(amount=Decimal(24999))
    broker._cash = Money(amount=Decimal(24999))
    broker._day_trade_count = 3

    # Try to open a short position with insufficient cash
    short_order = Order(
        symbol="TEST",
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        quantity_requested=Decimal(10),
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=TradingDateTime.now(),
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    with pytest.raises(PDTRuleViolationException):
        asyncio.run(broker.place_order(short_order))

    # Now with sufficient cash
    broker._snapshot_of_cash = Money(amount=Decimal(26000))
    broker._cash = Money(amount=Decimal(26000))

    asyncio.run(broker.place_order(short_order))
    assert broker._is_stale_flag is True
    assert broker._pending_orders == [short_order]

    # Verify position and cash
    position = asyncio.run(broker.get_position("TEST"))
    assert position.side == PositionSide.SHORT
    assert position.size == Decimal(10)

    cash = asyncio.run(broker.get_available_cash())
    assert cash.amount == Decimal("25000")


def test_position_exposure(mock_broker_with_nun_strategy):
    """Test that position exposure is calculated correctly."""
    broker = mock_broker_with_nun_strategy
    trading_datetime = TradingDateTime.now()
    while trading_datetime.is_weekend:
        trading_datetime = TradingDateTime.from_utc(trading_datetime.timestamp - timedelta(days=1))

    # Place a buy order
    order = Order(
        symbol="TEST",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity_requested=Decimal(100),
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=trading_datetime,
        filled_at=None,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )
    asyncio.run(broker.place_order(order))

    position_exposure = asyncio.run(broker.get_position_exposure("TEST"))

    assert position_exposure > 0
    assert position_exposure < 1  # Exposure should be less than 100%
    assert isinstance(position_exposure, Decimal)
