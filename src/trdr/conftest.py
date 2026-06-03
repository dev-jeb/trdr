import asyncio
import pytest
import yfinance as yf
import yfinance.shared  # noqa: F401  ensures yf.shared is bound for the monkeypatch below
import random
import datetime

from .core.security_provider.security_provider import SecurityProvider
from .core.bar_provider.yf_bar_provider.yf_bar_provider import YFBarProvider
from .test_utils.fake_yf_download import fake_yf_download
from .test_utils.security_generator import SecurityGenerator, SecurityCriteria
from .test_utils.position_generator import PositionGenerator, PositionCriteria
from .test_utils.order_generator import OrderGenerator, OrderCriteria
from .core.broker.mock_broker.mock_broker import MockBroker
from .core.broker.pdt.nun_strategy import NunStrategy
from .core.broker.pdt.wiggle_strategy import WiggleStrategy
from .core.broker.pdt.yolo_strategy import YoloStrategy
from .core.trading_context.trading_context import TradingContext
from .core.shared.models import TradingDateTime


@pytest.fixture(scope="function")
def weekday_trading_datetime():
    trading_now = TradingDateTime.now()
    while trading_now.is_weekend:
        trading_now = trading_now + datetime.timedelta(days=1)
    return trading_now


@pytest.fixture(scope="function")
def weekend_trading_datetime():
    trading_now = TradingDateTime.now()
    while not trading_now.is_weekend:
        trading_now = trading_now + datetime.timedelta(days=1)
    return trading_now


@pytest.fixture(scope="function")
def yf_bar_provider_with_fake_data(monkeypatch):
    monkeypatch.setattr(yf, "download", fake_yf_download)
    monkeypatch.setattr(yf.shared, "_ERRORS", {"ABCDEFG": "YFTzMissingError()", "AMZN": "JSONDecodeError()"})
    return asyncio.run(YFBarProvider.create(["AAPL", "MSFT", "ABCDEFG", "AMZN"]))


@pytest.fixture(scope="function")
def security_provider_with_fake_data(yf_bar_provider_with_fake_data):
    return asyncio.run(SecurityProvider.create(yf_bar_provider_with_fake_data))


@pytest.fixture(scope="function")
def random_security(symbol="AAPL", bar_count=200):
    """Create a random security with the given symbol and bar count.

    Args:
        symbol: The ticker symbol to use
        count: The number of bars to generate

    Returns:
        A Security instance with randomly generated price and volume data
    """
    generator = SecurityGenerator(SecurityCriteria(bar_count=bar_count))
    security = generator.find_suitable_security()
    # Override the symbol if requested
    if symbol != security.symbol:
        security.symbol = symbol
    return security


@pytest.fixture(scope="function")
def get_random_security():
    """Legacy fixture name for backward compatibility."""
    generator = SecurityGenerator(SecurityCriteria(bar_count=200))
    return generator.find_suitable_security()


@pytest.fixture(scope="module")
def security_generator():
    """Return a configured SecurityGenerator instance."""
    return SecurityGenerator(SecurityCriteria(bar_count=200))


@pytest.fixture(scope="function")
def short_dummy_position():
    """Create a test position with default values."""
    position = PositionGenerator(criteria=PositionCriteria(bar_count=1, net_position_bias=0)).generate_positions()[0]
    return position


@pytest.fixture(scope="function")
def long_dummy_position():
    """Create a test position with default values."""
    position = PositionGenerator(criteria=PositionCriteria(bar_count=1, net_position_bias=1)).generate_positions()[0]
    return position


@pytest.fixture(scope="function")
def dummy_positions():
    """Create a dictionary of test positions."""
    num_positions = random.randint(1, 9)
    positions = PositionGenerator(
        criteria=PositionCriteria(bar_count=num_positions, net_position_bias=0.5)
    ).generate_positions()
    return positions


@pytest.fixture(scope="function")
def mock_broker_with_nun_strategy():
    nun_strategy = NunStrategy.create()
    broker = asyncio.run(MockBroker.create(pdt_strategy=nun_strategy))
    yield broker
    asyncio.run(broker._session.close())


@pytest.fixture(scope="function")
def mock_broker_with_wiggle_strategy():
    pdt_strategy = WiggleStrategy.create()
    pdt_strategy.wiggle_room = 2
    broker = asyncio.run(MockBroker.create(pdt_strategy=pdt_strategy))
    yield broker
    asyncio.run(broker._session.close())


@pytest.fixture(scope="function")
def mock_broker_with_yolo_strategy():
    pdt_strategy = YoloStrategy.create()
    broker = asyncio.run(MockBroker.create(pdt_strategy=pdt_strategy))
    yield broker
    asyncio.run(broker._session.close())


@pytest.fixture(scope="function")
def mock_trading_context(security_provider_with_fake_data, mock_broker_with_nun_strategy):
    return asyncio.run(TradingContext.create(security_provider_with_fake_data, mock_broker_with_nun_strategy))


@pytest.fixture(scope="function")
def create_order():

    def _create_order(symbol: str, side=None):
        return OrderGenerator(criteria=OrderCriteria(count=1, symbol=symbol, side=side)).generate_orders()[0]

    return _create_order


@pytest.fixture(scope="function")
def prepared_trading_context(mock_trading_context: TradingContext):
    """Prepare trading context with a valid symbol and security."""
    asyncio.run(mock_trading_context.next_symbol())
    return mock_trading_context
