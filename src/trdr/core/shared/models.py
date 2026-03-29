from decimal import Decimal
from datetime import date, datetime, time, timezone, timedelta
from enum import Enum
from typing import Optional
from pydantic import BaseModel

from .exceptions import TradingDateException


class Money(BaseModel):
    """Value object representing monetary amounts in trading context.

    Attributes:
        amount (Decimal): The monetary amount
        currency (str): The currency code, defaults to USD

    Methods:
        __add__: Adds two Money objects of the same currency
    """

    amount: Decimal
    currency: str | None = "USD"

    def __add__(self, other: "Money") -> "Money":
        """Add two Money objects.

        Args:
            other: Another Money object to add

        Returns:
            A new Money object with the sum

        Raises:
            ValueError: If currencies don't match
        """
        if self.currency != other.currency:
            raise ValueError(f"Cannot add different currencies: {self.currency} and {other.currency}")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError(f"Cannot subtract different currencies: {self.currency} and {other.currency}")
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __str__(self) -> str:
        return f"{self.currency} {self.amount:.2f}"

    def __eq__(self, other):
        if not isinstance(other, Money):
            return False
        return round(self.amount, 2) == round(other.amount, 2) and self.currency == other.currency


class TradingDateTime(BaseModel):
    """Value object representing a point in market time."""

    trading_date: date
    timestamp: datetime

    @property
    def is_weekend(self) -> bool:
        return self.trading_date.weekday() in [5, 6]

    @classmethod
    def start_of_current_day(cls) -> "TradingDateTime":
        return cls(trading_date=date.today(), timestamp=datetime.combine(date.today(), time.min, tzinfo=timezone.utc))

    @classmethod
    def end_of_current_day(cls) -> "TradingDateTime":
        return cls(trading_date=date.today(), timestamp=datetime.combine(date.today(), time.max, tzinfo=timezone.utc))

    @classmethod
    def from_utc(cls, timestamp: datetime) -> "TradingDateTime":
        if timestamp.tzinfo != timezone.utc:
            raise TradingDateException("Timestamp must be UTC")
        return cls(trading_date=timestamp.date(), timestamp=timestamp)

    @classmethod
    def now(cls) -> "TradingDateTime":
        now = datetime.now(tz=timezone.utc)
        return cls(trading_date=now.date(), timestamp=now)

    def __str__(self) -> str:
        return f"[{self.trading_date} {self.timestamp.strftime('%H:%M:%S')} UTC]"

    def __add__(self, delta: timedelta) -> "TradingDateTime":
        if not isinstance(delta, timedelta):
            raise NotImplementedError("Cannot add non-timedelta to TradingDateTime")
        new_timestamp = self.timestamp + delta
        return TradingDateTime(trading_date=new_timestamp.date(), timestamp=new_timestamp)

    def __radd__(self, delta: timedelta) -> "TradingDateTime":
        return self.__add__(delta)


class Timeframe(Enum):
    """
    Represents standard time periods used for technical analysis and market data operations.

    Each timeframe value is stored as the number of seconds in that period:
    - m15: 15 minutes (900 seconds)
    - d1: 1 day (86,400 seconds)
    - d5: 5 days
    - etc.

    These timeframes are used to:
    1. Calculate moving averages (MA5, MA20, etc.) that are referenced in the DSL
    2. Request historical data from market data providers
    3. Determine bar intervals for price and volume data

    Timeframes map directly to ContextIdentifier values through the to_timeframe() method.
    """

    m15 = 900  # 15 minutes
    d1 = 86400  # 1 day
    d5 = 432000  # 5 days
    d20 = 1728000  # 20 days
    d50 = 4320000  # 50 days
    d100 = 8640000  # 100 days
    d200 = 17280000  # 200 days

    def to_days(self) -> int:
        """
        Convert the timeframe to number of days.

        Returns:
            int: Number of days in this timeframe (rounds down for intraday timeframes)
        """
        return self.value // 86400

    def to_yf_interval(self) -> str:
        """
        Convert timeframe to Yahoo Finance API interval format.

        This method is used when making requests to Yahoo Finance or similar data providers
        to ensure the correct interval string is used.

        Returns:
            str: Yahoo Finance compatible interval string (e.g., "1d", "15m")
        """
        return {
            "m15": "15m",
            "d1": "1d",
            "d5": "5d",
            "d20": "20d",
            "d50": "50d",
            "d100": "100d",
            "d200": "200d",
        }[self.name]

    def is_intraday(self) -> bool:
        """
        Check if this timeframe represents an intraday period (less than one day).

        Returns:
            bool: True if the timeframe is less than one day (86400 seconds)
        """
        return self.value < 86400

    def __index__(self) -> int:
        """
        Allow using Timeframe in contexts where an integer is expected.

        This enables using Timeframe enums in slicing and other numeric contexts.

        Returns:
            int: The number of days in this timeframe
        """
        return self.to_days()

    def __str__(self) -> str:
        """
        Return a human-readable string representation of the timeframe.

        Returns:
            str: Human-readable description (e.g., "15 minutes", "5 days")

        Raises:
            ValueError: If there's no string mapping for this timeframe
        """
        name_map = {
            "m15": "15 minutes",
            "d1": "1 day",
            "d5": "5 days",
            "d20": "20 days",
            "d50": "50 days",
            "d100": "100 days",
            "d200": "200 days",
        }
        str_representation = name_map.get(self.name, None)
        if not str_representation:
            raise ValueError(f"Could not convert {self.name} to string")
        return str_representation


class ContextIdentifier(str, Enum):
    """
    Identifiers for different types of context data that can be accessed in trading strategies.

    These identifiers serve a dual purpose:
    1. They act as keywords in the strategy DSL (.trdr files), allowing direct reference in
       strategy conditions (e.g., `MA5 CROSSED_ABOVE MA20` or `CURRENT_PRICE > 100`)
    2. They map to specific data values that can be retrieved at runtime through the
       TradingContext.get_value_for_identifier() method

    The identifiers are organized in three categories:
    - Technical indicators (from security provider): Moving averages for price (MA*) and volume (AV*)
    - Security-specific data (from security provider): Current price and volume
    - Account data (from broker): Account exposure, open positions, cash, position cost

    When adding new identifiers, they automatically become available as keywords in the DSL.
    """

    # Security technical indicators (from security provider)
    MA5 = "MA5"  # 5-day moving average price
    MA20 = "MA20"  # 20-day moving average price
    MA50 = "MA50"  # 50-day moving average price
    MA100 = "MA100"  # 100-day moving average price
    MA200 = "MA200"  # 200-day moving average price
    AV5 = "AV5"  # 5-day average volume
    AV20 = "AV20"  # 20-day average volume
    AV50 = "AV50"  # 50-day average volume
    AV100 = "AV100"  # 100-day average volume
    AV200 = "AV200"  # 200-day average volume

    # RSI (Relative Strength Index)
    RSI7 = "RSI7"
    RSI14 = "RSI14"
    RSI21 = "RSI21"

    # EMA (Exponential Moving Average)
    EMA5 = "EMA5"
    EMA12 = "EMA12"
    EMA20 = "EMA20"
    EMA26 = "EMA26"
    EMA50 = "EMA50"

    # MACD (Moving Average Convergence Divergence)
    MACD_LINE = "MACD_LINE"
    MACD_SIGNAL = "MACD_SIGNAL"
    MACD_HISTOGRAM = "MACD_HISTOGRAM"

    # ATR (Average True Range)
    ATR14 = "ATR14"

    # Bollinger Bands (20-period, 2 std dev)
    BBAND_UPPER = "BBAND_UPPER"
    BBAND_LOWER = "BBAND_LOWER"

    # Price action
    DAILY_HIGH = "DAILY_HIGH"
    DAILY_LOW = "DAILY_LOW"
    PERCENT_CHANGE = "PERCENT_CHANGE"

    # Security-specific fields (from security provider)
    CURRENT_VOLUME = "CURRENT_VOLUME"
    CURRENT_PRICE = "CURRENT_PRICE"

    # Account data (from broker)
    ACCOUNT_EXPOSURE = "ACCOUNT_EXPOSURE"  # Ratio of invested funds to total capital
    NUMBER_OF_OPEN_POSITIONS = "NUMBER_OF_OPEN_POSITIONS"  # Count of currently held positions
    AVAILABLE_CASH = "AVAILABLE_CASH"  # Current cash available for trading
    AVERAGE_COST = "AVERAGE_COST"  # Average cost basis of current position

    def is_moving_average(self) -> bool:
        """
        Check if this identifier represents a moving average indicator.

        This method is used in the DSL to validate identifiers used in crossover expressions
        (e.g., `MA5 CROSSED_ABOVE MA20`). Only moving average identifiers can be used with
        CROSSED_ABOVE and CROSSED_BELOW operators.

        Returns:
            bool: True if the identifier is a price or volume moving average
        """
        return self in [
            ContextIdentifier.MA5,
            ContextIdentifier.MA20,
            ContextIdentifier.MA50,
            ContextIdentifier.MA100,
            ContextIdentifier.MA200,
            ContextIdentifier.AV5,
            ContextIdentifier.AV20,
            ContextIdentifier.AV50,
            ContextIdentifier.AV100,
            ContextIdentifier.AV200,
        ]

    def to_timeframe(self) -> Optional[Timeframe]:
        """
        Convert a moving average context identifier to its corresponding timeframe.

        This method is used to translate DSL moving average identifiers to actual
        Timeframe objects used by the security provider to compute values.

        Moving average identifiers have a direct mapping to timeframes:
        - MA5/AV5 → Timeframe.d5 (5 days)
        - MA20/AV20 → Timeframe.d20 (20 days)
        etc.

        Returns:
            Timeframe or None: The corresponding timeframe for this identifier,
                               or None if not a moving average identifier

        Raises:
            ValueError: If the moving average doesn't have a corresponding timeframe
        """
        if not self.is_moving_average():
            return None

        # Map moving average identifiers to timeframes
        timeframe_map = {
            ContextIdentifier.MA5: Timeframe.d5,
            ContextIdentifier.MA20: Timeframe.d20,
            ContextIdentifier.MA50: Timeframe.d50,
            ContextIdentifier.MA100: Timeframe.d100,
            ContextIdentifier.MA200: Timeframe.d200,
            ContextIdentifier.AV5: Timeframe.d5,
            ContextIdentifier.AV20: Timeframe.d20,
            ContextIdentifier.AV50: Timeframe.d50,
            ContextIdentifier.AV100: Timeframe.d100,
            ContextIdentifier.AV200: Timeframe.d200,
        }

        timeframe = timeframe_map.get(self)
        if timeframe is None:
            raise ValueError(f"No corresponding timeframe for {self}")
        return timeframe
