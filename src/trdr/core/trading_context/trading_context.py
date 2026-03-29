from typing import Type, TypeVar
from decimal import Decimal
from opentelemetry import trace

from ..broker.base_broker import BaseBroker
from ..broker.models import Position
from ..security_provider.base_security_provider import BaseSecurityProvider
from ..security_provider.models import Security
from ..shared.models import ContextIdentifier, Timeframe
from .exceptions import MissingContextValue

T = TypeVar("T", bound="TradingContext")


class TradingContext:
    """
    Core trading context that manages the interaction between security provider and broker.

    This class maintains the state of the current trading symbol, position, and security.
    It provides methods to iterate through symbols and retrieve market data values needed
    for trading strategy execution.

    The TradingContext follows the factory pattern with async initialization and must be
    created using the `create()` class method rather than direct instantiation.
    """

    def __init__(
        self,
        security_provider: BaseSecurityProvider,
        broker: BaseBroker,
        tracer: trace.Tracer = trace.NoOpTracer(),
        _from_create: bool = False,
    ):
        """
        Initialize a TradingContext instance.

        Note: This constructor should not be called directly.
        Use the `create()` class method to instantiate a TradingContext.

        Args:
            security_provider: Provider that supplies securities and market data
            broker: Broker that manages orders and positions
            tracer: OpenTelemetry tracer for instrumentation
            _from_create: Internal flag to ensure proper initialization flow

        Raises:
            TypeError: If not instantiated through the create() factory method
        """
        if not _from_create:
            raise TypeError("Use TradingContext.create() instead to create a new trading context")
        self.security_provider = security_provider
        self.broker = broker
        self.current_symbol: str | None = None
        self.current_position: Position | None = None
        self.current_security: Security | None = None
        self.symbol_stack: list[str] | None = None
        self._tracer = tracer

    @classmethod
    async def create(
        cls: Type[T],
        security_provider: BaseSecurityProvider,
        broker: BaseBroker,
        tracer: trace.Tracer = trace.NoOpTracer(),
    ) -> T:
        """
        Factory method to create and initialize a TradingContext instance.

        This async factory method ensures proper initialization of the context
        including loading the symbol list from the security provider.

        Args:
            security_provider: Provider that supplies securities and market data
            broker: Broker that manages orders and positions
            tracer: OpenTelemetry tracer for instrumentation

        Returns:
            A fully initialized TradingContext instance

        Raises:
            Exception: If initialization fails for any reason
        """
        self = cls.__new__(cls)
        TradingContext.__init__(self, security_provider, broker, tracer, _from_create=True)
        with self._tracer.start_as_current_span("TradingContext.create") as span:
            try:
                await self._initialize()
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR)
                raise
            else:
                span.set_status(trace.StatusCode.OK)
                return self

    async def _initialize(self) -> None:
        """
        Internal initialization method to load the list of symbols.

        This method is called by the create() factory method during context initialization.
        """
        self.symbol_stack = await self.security_provider.get_symbols()

    async def next_symbol(self) -> bool:
        """
        Advance to the next symbol in the stack and load its associated data.

        Updates the current_symbol, current_position, and current_security properties
        with data for the next symbol in the stack.

        Returns:
            bool: True if successfully moved to the next symbol, False if no more symbols

        Raises:
            ValueError: If there's a symbol mismatch between security and position and current symbol
        """
        with self._tracer.start_as_current_span("TradingContext.next_symbol") as span:
            span.set_attribute("length_of_symbol_stack", len(self.symbol_stack))
            try:
                self.current_symbol = self.symbol_stack.pop(0)
            except IndexError:
                span.set_status(trace.StatusCode.OK)
                span.add_event("No more symbols to process")
                self.current_symbol = None
                self.current_position = None
                self.current_security = None
                return False
            else:
                self.current_position = await self.broker.get_position(self.current_symbol)
                self.current_security = await self.security_provider.get_security(self.current_symbol)

                if not self.current_security.symbol == self.current_symbol:
                    span.set_status(trace.StatusCode.ERROR)
                    error = ValueError("Current security symbol does not match current symbol")
                    span.record_exception(error)
                    raise error

                if self.current_position is not None:
                    if self.current_position.symbol != self.current_symbol:
                        span.set_status(trace.StatusCode.ERROR)
                        error = ValueError("Current position symbol does not match current symbol")
                        span.record_exception(error)
                        raise error

                span.set_status(trace.StatusCode.OK)
                return True

    async def get_value_for_identifier(self, identifier: ContextIdentifier) -> Decimal:
        """
        Retrieve a value for the current symbol based on the provided context identifier.

        This method acts as a unified interface to access two distinct types of data:
        1. Security data: Market data and calculations for the current symbol (prices,
           moving averages, volumes) - provided by the security provider
        2. Broker data: Account and position data related to the trader's account
           (positions, cash, exposure) - provided by the broker

        The separation between security and broker is intentional:
        - Security provider handles market data and calculations from that data
        - Broker provides state and context from the trader's account

        Args:
            identifier: The specific context identifier to retrieve the value for

        Returns:
            Decimal: The requested value for the current symbol

        Raises:
            ValueError: If the identifier is invalid or current symbol is not set
            MissingContextValue: If the requested value is not available
        """
        with self._tracer.start_as_current_span("TradingContext.get_value_for_identifier") as span:
            if not self.current_symbol:
                error = ValueError("Current symbol is not set when trying to get value for identifier")
                span.record_exception(error)
                raise error
            if not self.current_security:
                error = ValueError("Current security is not set when trying to get value for identifier")
                span.record_exception(error)
                raise error

            match identifier:

                case ContextIdentifier.MA5:
                    moving_average = self.current_security.compute_moving_average(Timeframe.d5)
                    if moving_average is None:
                        error = MissingContextValue(f"Moving average for {self.current_symbol} is not available")
                        span.add_event(str(error))
                        raise error
                    return moving_average.amount

                case ContextIdentifier.MA20:
                    moving_average = self.current_security.compute_moving_average(Timeframe.d20)
                    if moving_average is None:
                        error = MissingContextValue(f"Moving average for {self.current_symbol} is not available")
                        raise error
                    return moving_average.amount

                case ContextIdentifier.MA50:
                    moving_average = self.current_security.compute_moving_average(Timeframe.d50)
                    if moving_average is None:
                        error = MissingContextValue(f"Moving average for {self.current_symbol} is not available")
                        span.add_event(str(error))
                        raise error
                    return moving_average.amount

                case ContextIdentifier.MA100:
                    moving_average = self.current_security.compute_moving_average(Timeframe.d100)
                    if moving_average is None:
                        error = MissingContextValue(f"Moving average for {self.current_symbol} is not available")
                        span.add_event(str(error))
                        raise error
                    return moving_average.amount

                case ContextIdentifier.MA200:
                    moving_average = self.current_security.compute_moving_average(Timeframe.d200)
                    if moving_average is None:
                        error = MissingContextValue(f"Moving average for {self.current_symbol} is not available")
                        span.add_event(str(error))
                        raise error
                    return moving_average.amount

                case ContextIdentifier.AV5:
                    average_volume = self.current_security.compute_average_volume(Timeframe.d5)
                    if average_volume is None:
                        error = MissingContextValue(f"Average volume for {self.current_symbol} is not available")
                        span.add_event(str(error))
                        raise error
                    return Decimal(average_volume)

                case ContextIdentifier.AV20:
                    average_volume = self.current_security.compute_average_volume(Timeframe.d20)
                    if average_volume is None:
                        error = MissingContextValue(f"Average volume for {self.current_symbol} is not available")
                        span.add_event(str(error))
                        raise error
                    return Decimal(average_volume)

                case ContextIdentifier.AV50:
                    average_volume = self.current_security.compute_average_volume(Timeframe.d50)
                    if average_volume is None:
                        error = MissingContextValue(f"Average volume for {self.current_symbol} is not available")
                        span.add_event(str(error))
                        raise error
                    return Decimal(average_volume)

                case ContextIdentifier.AV100:
                    average_volume = self.current_security.compute_average_volume(Timeframe.d100)
                    if average_volume is None:
                        error = MissingContextValue(f"Average volume for {self.current_symbol} is not available")
                        span.add_event(str(error))
                        raise error
                    return Decimal(average_volume)

                case ContextIdentifier.AV200:
                    average_volume = self.current_security.compute_average_volume(Timeframe.d200)
                    if average_volume is None:
                        error = MissingContextValue(f"Average volume for {self.current_symbol} is not available")
                        span.add_event(str(error))
                        raise error
                    return Decimal(average_volume)

                case ContextIdentifier.CURRENT_VOLUME:
                    current_volume = self.current_security.current_bar.volume
                    if current_volume is None:
                        error = MissingContextValue(f"Current volume for {self.current_symbol} is not available")
                        span.add_event(str(error))
                        raise error
                    return Decimal(current_volume)

                case ContextIdentifier.CURRENT_PRICE:
                    current_price = self.current_security.current_bar.close
                    if current_price is None:
                        error = MissingContextValue(f"Current price for {self.current_symbol} is not available")
                        span.add_event(str(error))
                        raise error
                    return current_price.amount

                case ContextIdentifier.ACCOUNT_EXPOSURE:
                    account_exposure = await self.broker.get_account_exposure()
                    if account_exposure is None:
                        error = MissingContextValue("Account exposure is not available")
                        span.add_event(str(error))
                        raise error
                    return account_exposure

                case ContextIdentifier.NUMBER_OF_OPEN_POSITIONS:
                    open_positions = await self.broker.get_positions()
                    if open_positions is None:
                        error = MissingContextValue("Number of open positions is not available")
                        span.add_event(str(error))
                        raise error
                    return len(open_positions.keys())

                case ContextIdentifier.AVAILABLE_CASH:
                    available_cash = await self.broker.get_available_cash()
                    if available_cash is None:
                        error = MissingContextValue("Available cash is not available")
                        span.add_event(str(error))
                        raise error
                    return available_cash.amount

                case ContextIdentifier.AVERAGE_COST:
                    if self.current_position is None:
                        error = MissingContextValue("Average cost is not available as no position is open")
                        span.add_event(str(error))
                        raise error
                    average_cost = self.current_position.average_cost
                    if average_cost is None:
                        error = MissingContextValue("Average cost is not available")
                        span.add_event(str(error))
                        raise error
                    return average_cost.amount

                # RSI
                case ContextIdentifier.RSI7 | ContextIdentifier.RSI14 | ContextIdentifier.RSI21:
                    period = {"RSI7": 7, "RSI14": 14, "RSI21": 21}[identifier.value]
                    value = self.current_security.compute_rsi(period)
                    if value is None:
                        raise MissingContextValue(f"RSI{period} for {self.current_symbol} is not available")
                    return value

                # EMA
                case ContextIdentifier.EMA5 | ContextIdentifier.EMA12 | ContextIdentifier.EMA20 | ContextIdentifier.EMA26 | ContextIdentifier.EMA50:
                    period = {"EMA5": 5, "EMA12": 12, "EMA20": 20, "EMA26": 26, "EMA50": 50}[identifier.value]
                    value = self.current_security.compute_ema(period)
                    if value is None:
                        raise MissingContextValue(f"EMA{period} for {self.current_symbol} is not available")
                    return value.amount

                # MACD
                case ContextIdentifier.MACD_LINE | ContextIdentifier.MACD_SIGNAL | ContextIdentifier.MACD_HISTOGRAM:
                    result = self.current_security.compute_macd()
                    if result is None:
                        raise MissingContextValue(f"MACD for {self.current_symbol} is not available")
                    macd_line, signal, histogram = result
                    if identifier == ContextIdentifier.MACD_LINE:
                        return macd_line
                    elif identifier == ContextIdentifier.MACD_SIGNAL:
                        return signal
                    else:
                        return histogram

                # ATR
                case ContextIdentifier.ATR14:
                    value = self.current_security.compute_atr(14)
                    if value is None:
                        raise MissingContextValue(f"ATR14 for {self.current_symbol} is not available")
                    return value

                # Bollinger Bands
                case ContextIdentifier.BBAND_UPPER:
                    value = self.current_security.compute_bollinger_band(upper=True)
                    if value is None:
                        raise MissingContextValue(f"BBAND_UPPER for {self.current_symbol} is not available")
                    return value.amount

                case ContextIdentifier.BBAND_LOWER:
                    value = self.current_security.compute_bollinger_band(upper=False)
                    if value is None:
                        raise MissingContextValue(f"BBAND_LOWER for {self.current_symbol} is not available")
                    return value.amount

                # Price action
                case ContextIdentifier.DAILY_HIGH:
                    return self.current_security.current_bar.high.amount

                case ContextIdentifier.DAILY_LOW:
                    return self.current_security.current_bar.low.amount

                case ContextIdentifier.PERCENT_CHANGE:
                    value = self.current_security.compute_percent_change()
                    if value is None:
                        raise MissingContextValue(f"PERCENT_CHANGE for {self.current_symbol} is not available")
                    return value

                case _:
                    span.set_status(trace.StatusCode.ERROR)
                    error = ValueError(f"Invalid context identifier: {identifier}")
                    span.record_exception(error)
                    raise error
