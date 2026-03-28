import pytest
import asyncio
from decimal import Decimal
from datetime import timedelta

from ..models import OrderSide, Order, OrderType, OrderStatus, PositionSide
from ...shared.models import Money, TradingDateTime
from ..pdt.exceptions import PDTRuleViolationException, PDTStrategyException


def test_position_opened_today_tracking(mock_broker_with_nun_strategy):
    broker = mock_broker_with_nun_strategy

    count = 0
    current_day = TradingDateTime.start_of_current_day()
    for _, position in broker._positions.items():
        orders = position.get_orders_created_after_dt(current_day)
        if len(orders) > 0:
            count += 1

    assert asyncio.run(broker.get_count_of_positions_opened_today()) == count


def test_position_opened_today_tracking_with_triggers_refresh(mock_broker_with_nun_strategy):
    """Test the position_opened_today tracking mechanism with triggers refresh."""
    broker = mock_broker_with_nun_strategy
    broker._is_stale_flag = True
    asyncio.run(broker.get_count_of_positions_opened_today())

    assert broker._is_stale_flag is False


def test_nun_strategy_buy_orders_day_trade_limits(mock_broker_with_nun_strategy, monkeypatch):
    """Test NunStrategy enforcement of day trade limits for BUY orders."""
    broker = mock_broker_with_nun_strategy
    broker._cash = Money(amount=Decimal(24999))

    async def mock_count():
        return 0

    monkeypatch.setattr(broker, "get_count_of_positions_opened_today", mock_count)

    created_at = TradingDateTime.start_of_current_day()
    while created_at.is_weekend:
        created_at = TradingDateTime.from_utc(created_at.timestamp - timedelta(days=1))

    symbol = list(broker._positions.keys())[0]
    existing_position = broker._positions.get(symbol)
    order_side = OrderSide.BUY if existing_position.side == PositionSide.LONG else OrderSide.SELL

    for day_trade_count in range(4):
        broker._day_trade_count = day_trade_count

        if day_trade_count < 3:
            order = Order(
                symbol=symbol,
                side=order_side,
                type=OrderType.MARKET,
                quantity_requested=Decimal(10),
                quantity_filled=Decimal(0),
                status=OrderStatus.PENDING,
                current_price=Money(amount=Decimal(100)),
                created_at=created_at,
                client_order_id="test-strategy:abc12345",
                strategy_name="test-strategy",
            )
            asyncio.run(broker._validate_pre_order(order))
        else:
            # Should not be able to buy
            with pytest.raises(PDTRuleViolationException, match="PDT restrictions prevent opening a new position"):
                asyncio.run(broker._validate_pre_order(order))


def test_nun_strategy_sell_orders_day_trade_limits(mock_broker_with_nun_strategy, monkeypatch):
    """Test NunStrategy enforcement of day trade limits for SELL orders."""
    broker = mock_broker_with_nun_strategy
    broker._cash = Money(amount=Decimal(24999))

    async def mock_count():
        return 0

    monkeypatch.setattr(broker, "get_count_of_positions_opened_today", mock_count)

    created_at = TradingDateTime.start_of_current_day()
    while created_at.is_weekend:
        created_at = TradingDateTime.from_utc(created_at.timestamp - timedelta(days=1))

    symbol = list(broker._positions.keys())[0]
    existing_position = broker._positions.get(symbol)
    order_side = OrderSide.SELL if existing_position.side == PositionSide.LONG else OrderSide.BUY
    # Test sell orders with different day trade counts
    for day_trade_count in range(4):
        broker._day_trade_count = day_trade_count
        broker._cash = Money(amount=Decimal(24999))

        # order to close existing position
        sell_order = Order(
            symbol=symbol,
            side=order_side,
            type=OrderType.MARKET,
            quantity_requested=Decimal(10),
            quantity_filled=Decimal(0),
            status=OrderStatus.PENDING,
            current_price=Money(amount=Decimal(100)),
            created_at=created_at,
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )

        if day_trade_count < 3:
            # Should be able to sell with less than 3 day trades
            asyncio.run(broker._validate_pre_order(sell_order))
        else:
            # Should not be able to sell with 3 day trades
            with pytest.raises(PDTStrategyException):
                asyncio.run(broker._validate_pre_order(sell_order))


def test_wiggle_strategy_buy_orders_day_trade_limits(mock_broker_with_wiggle_strategy, monkeypatch):
    broker = mock_broker_with_wiggle_strategy
    broker._cash = Money(amount=Decimal(24999))

    async def mock_count():

        return 2

    monkeypatch.setattr(broker, "get_count_of_positions_opened_today", mock_count)

    created_at = TradingDateTime.start_of_current_day()
    while created_at.is_weekend:
        created_at = TradingDateTime.from_utc(created_at.timestamp - timedelta(days=1))

    symbol = list(broker._positions.keys())[0]
    existing_position = broker._positions.get(symbol)
    order_side = OrderSide.BUY if existing_position.side == PositionSide.LONG else OrderSide.SELL

    order = Order(
        symbol=symbol,
        side=order_side,
        type=OrderType.MARKET,
        quantity_requested=Decimal(10),
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=created_at,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    broker._day_trade_count = 0
    asyncio.run(broker._validate_pre_order(order))

    broker._day_trade_count = 1
    asyncio.run(broker._validate_pre_order(order))

    broker._day_trade_count = 2
    asyncio.run(broker._validate_pre_order(order))

    with pytest.raises(
        PDTRuleViolationException, match="PDT restrictions prevent opening a new position: exceeds wiggle room"
    ):
        broker._day_trade_count = 3
        asyncio.run(broker._validate_pre_order(order))


def test_wiggle_strategy_sell_orders_day_trade_limits(mock_broker_with_wiggle_strategy, monkeypatch):
    broker = mock_broker_with_wiggle_strategy
    broker._cash = Money(amount=Decimal(24999))

    async def mock_count():
        return 0

    monkeypatch.setattr(broker, "get_count_of_positions_opened_today", mock_count)

    created_at = TradingDateTime.start_of_current_day()
    while created_at.is_weekend:
        created_at = TradingDateTime.from_utc(created_at.timestamp - timedelta(days=1))

    symbol = list(broker._positions.keys())[0]
    existing_position = broker._positions.get(symbol)
    order_side = OrderSide.SELL if existing_position.side == PositionSide.LONG else OrderSide.BUY

    for day_trade_count in range(4):
        broker._day_trade_count = day_trade_count

        # order to close existing position
        sell_order = Order(
            symbol=symbol,
            side=order_side,
            type=OrderType.MARKET,
            quantity_requested=Decimal(10),
            quantity_filled=Decimal(0),
            status=OrderStatus.PENDING,
            current_price=Money(amount=Decimal(100)),
            created_at=created_at,
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )

        if day_trade_count < 3:
            # Should be able to sell with less than 3 day trades
            asyncio.run(broker._validate_pre_order(sell_order))
        else:
            # Should not be able to sell with 3 day trades
            with pytest.raises(PDTStrategyException):
                asyncio.run(broker._validate_pre_order(sell_order))


def test_yolo_strategy_buy_orders_day_trade_limits(mock_broker_with_yolo_strategy, monkeypatch):
    broker = mock_broker_with_yolo_strategy
    broker._cash = Money(amount=Decimal(24999))

    async def mock_count():
        return 0

    monkeypatch.setattr(broker, "get_count_of_positions_opened_today", mock_count)

    created_at = TradingDateTime.start_of_current_day()
    while created_at.is_weekend:
        created_at = TradingDateTime.from_utc(created_at.timestamp - timedelta(days=1))

    symbol = list(broker._positions.keys())[0]
    existing_position = broker._positions.get(symbol)
    order_side = OrderSide.BUY if existing_position.side == PositionSide.LONG else OrderSide.SELL

    order = Order(
        symbol=symbol,
        side=order_side,
        type=OrderType.MARKET,
        quantity_requested=Decimal(10),
        quantity_filled=Decimal(0),
        status=OrderStatus.PENDING,
        current_price=Money(amount=Decimal(100)),
        created_at=created_at,
        client_order_id="test-strategy:abc12345",
        strategy_name="test-strategy",
    )

    broker._day_trade_count = 0
    asyncio.run(broker._validate_pre_order(order))

    broker._day_trade_count = 1
    asyncio.run(broker._validate_pre_order(order))

    broker._day_trade_count = 4
    asyncio.run(broker._validate_pre_order(order))


def test_yolo_strategy_sell_orders_day_trade_limits(mock_broker_with_yolo_strategy, monkeypatch):
    broker = mock_broker_with_yolo_strategy
    broker._cash = Money(amount=Decimal(24999))

    async def mock_count():
        return 0

    monkeypatch.setattr(broker, "get_count_of_positions_opened_today", mock_count)

    created_at = TradingDateTime.start_of_current_day()
    while created_at.is_weekend:
        created_at = TradingDateTime.from_utc(created_at.timestamp - timedelta(days=1))

    symbol = list(broker._positions.keys())[0]
    existing_position = broker._positions.get(symbol)
    order_side = OrderSide.SELL if existing_position.side == PositionSide.LONG else OrderSide.BUY

    for day_trade_count in range(4):
        broker._day_trade_count = day_trade_count

        # order to close existing position
        sell_order = Order(
            symbol=symbol,
            side=order_side,
            type=OrderType.MARKET,
            quantity_requested=Decimal(10),
            quantity_filled=Decimal(0),
            status=OrderStatus.PENDING,
            current_price=Money(amount=Decimal(100)),
            created_at=created_at,
            client_order_id="test-strategy:abc12345",
            strategy_name="test-strategy",
        )

        if day_trade_count < 3:
            # Should be able to sell with less than 3 day trades
            asyncio.run(broker._validate_pre_order(sell_order))
        else:
            # Should not be able to sell with 3 day trades
            with pytest.raises(PDTStrategyException):
                asyncio.run(broker._validate_pre_order(sell_order))
