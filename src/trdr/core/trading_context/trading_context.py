import calendar
import copy
from datetime import date, timedelta
from typing import Any, Type, TypeVar
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
        market_symbol: str | None = None,
        universe: list[str] | None = None,
        sessions: list[date] | None = None,
        sleeve_weight: float | None = None,
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
            market_symbol: Benchmark symbol backing the MARKET_* identifiers (see create())
            universe: Explicit tradable symbols (see create())
            sessions: Exchange session dates for the calendar identifiers (see create())
            sleeve_weight: This strategy's share of the account (see create())

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
        self.market_symbol = market_symbol
        self.market_security: Security | None = None
        self.universe = list(universe) if universe is not None else None
        self.sessions = sorted(set(sessions)) if sessions else None
        self.universe_symbols: list[str] = []
        # Set by TradingEngine: scopes AVERAGE_COST to this strategy's own shares in a position
        # other strategies may also hold.
        self.strategy_name: str | None = None
        if sleeve_weight is not None and not 0 < sleeve_weight <= 1:
            raise ValueError(f"sleeve_weight must be in (0, 1], got {sleeve_weight}")
        self.sleeve_weight = sleeve_weight
        self._security_cache: dict[str, Security | None] = {}
        self._rank_cache: dict[int, dict[str, Decimal]] = {}
        self._tracer = tracer

    @classmethod
    async def create(
        cls: Type[T],
        security_provider: BaseSecurityProvider,
        broker: BaseBroker,
        tracer: trace.Tracer = trace.NoOpTracer(),
        market_symbol: str | None = None,
        universe: list[str] | None = None,
        sessions: list[date] | None = None,
        sleeve_weight: float | None = None,
    ) -> T:
        """
        Factory method to create and initialize a TradingContext instance.

        This async factory method ensures proper initialization of the context
        including loading the symbol list from the security provider.

        Args:
            security_provider: Provider that supplies securities and market data
            broker: Broker that manages orders and positions
            tracer: OpenTelemetry tracer for instrumentation
            market_symbol: Optional benchmark (e.g. "SPY") backing the MARKET_* identifiers.
                The security provider must have data for it. It is used only as a market
                reference and, unless it is listed in `universe`, is not traded.
            universe: The symbols to trade (and to rank with RANK), in this order. Symbols the
                provider has no data for are left out. Default: every provider symbol except
                market_symbol.
            sessions: Exchange session dates covering at least the current month, so
                TRADING_DAY_OF_MONTH / TRADING_DAYS_LEFT_IN_MONTH account for holidays. Without
                them those identifiers count weekdays.
            sleeve_weight: Run the strategy in a sleeve holding this share of the account (e.g.
                0.6), so several strategies can share one account. ACCOUNT_EQUITY becomes
                weight x account equity, AVAILABLE_CASH that budget minus the strategy's own
                holdings (never more than the account's cash), and NUMBER_OF_OPEN_POSITIONS /
                ACCOUNT_EXPOSURE count only the strategy's own positions. Holdings are marked at
                the current bar's close. Default None: the account identifiers cover the whole
                account, as before.

        Returns:
            A fully initialized TradingContext instance

        Raises:
            Exception: If initialization fails for any reason
        """
        self = cls.__new__(cls)
        TradingContext.__init__(
            self,
            security_provider,
            broker,
            tracer,
            _from_create=True,
            market_symbol=market_symbol,
            universe=universe,
            sessions=sessions,
            sleeve_weight=sleeve_weight,
        )
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
        provider_symbols = await self.security_provider.get_symbols()
        if self.universe is not None:
            available = set(provider_symbols)
            symbols = [s for s in self.universe if s in available]
        else:
            symbols = [s for s in provider_symbols if s != self.market_symbol]
        if self.market_symbol:
            self.market_security = await self._get_security(self.market_symbol)
        self.universe_symbols = symbols
        self.symbol_stack = list(symbols)

    def rewind(self) -> None:
        """Restart iteration over the universe (the engine makes an exit pass, then an entry pass)."""
        self.symbol_stack = list(self.universe_symbols)
        self.current_symbol = None
        self.current_position = None
        self.current_security = None

    async def _get_security(self, symbol: str) -> Security | None:
        """A symbol's Security, built once per context (i.e. per run) and reused."""
        if symbol not in self._security_cache:
            self._security_cache[symbol] = await self.security_provider.get_security(symbol)
        return self._security_cache[symbol]

    async def rank(self, expression: Any) -> Decimal:
        """Rank of the current symbol's value of `expression` across the universe (1 = largest).

        Each symbol's value is computed once per run. Symbols whose value is unavailable are left
        out of the ranking; if the current symbol's is, raises MissingContextValue. Ties share the
        better rank.
        """
        key = id(expression)
        if key not in self._rank_cache:
            values: dict[str, Decimal] = {}
            for symbol in self.universe_symbols:
                security = await self._get_security(symbol)
                if security is None:
                    continue
                view = copy.copy(self)
                view.current_symbol = symbol
                view.current_security = security
                view.current_position = await self.broker.get_position(symbol)
                try:
                    values[symbol] = Decimal(await expression.evaluate(view))
                except MissingContextValue:
                    continue
            self._rank_cache[key] = values
        values = self._rank_cache[key]
        if self.current_symbol not in values:
            raise MissingContextValue(f"RANK({expression.describe()}) is unavailable for {self.current_symbol}")
        mine = values[self.current_symbol]
        return Decimal(1 + sum(1 for value in values.values() if value > mine))

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
            while True:
                try:
                    self.current_symbol = self.symbol_stack.pop(0)
                except IndexError:
                    span.set_status(trace.StatusCode.OK)
                    span.add_event("No more symbols to process")
                    self.current_symbol = None
                    self.current_position = None
                    self.current_security = None
                    return False
                self.current_security = await self._get_security(self.current_symbol)
                if self.current_security is not None:
                    break
                # No usable bars for this symbol right now (e.g. a gap in the data feed). Skip just
                # this symbol; raising here used to abort every remaining symbol in the run.
                span.add_event("skipped_symbol_without_data", {"symbol": self.current_symbol})

            self.current_position = await self.broker.get_position(self.current_symbol)

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

            span.set_attribute(
                "identifier",
                identifier.value if isinstance(identifier, ContextIdentifier) else str(identifier),
            )
            span.set_attribute("symbol", self.current_symbol)
            value = await self._resolve_identifier_value(identifier, span)
            try:
                span.set_attribute("value", float(value))
            except (TypeError, ValueError):
                pass
            return value

    async def _resolve_identifier_value(self, identifier: ContextIdentifier, span) -> Decimal:
        """Resolve the raw value for ``identifier``.

        ``span`` is passed only so not-available cases can record an event; the
        identifier/symbol/value attribute tagging lives in the public wrapper.
        """
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

                case ContextIdentifier.MARKET_PRICE | ContextIdentifier.MARKET_MA50 | ContextIdentifier.MARKET_MA200:
                    return self._resolve_market_value(identifier, span)

                case (
                    ContextIdentifier.RETURN_5D
                    | ContextIdentifier.RETURN_21D
                    | ContextIdentifier.RETURN_63D
                    | ContextIdentifier.RETURN_126D
                    | ContextIdentifier.RETURN_252D
                ):
                    sessions = int(identifier.value.removeprefix("RETURN_").removesuffix("D"))
                    value = self.current_security.compute_return(sessions)
                    if value is None:
                        raise MissingContextValue(f"{identifier.value} for {self.current_symbol} is not available")
                    return value

                case ContextIdentifier.VOLATILITY_21D | ContextIdentifier.VOLATILITY_63D:
                    sessions = int(identifier.value.removeprefix("VOLATILITY_").removesuffix("D"))
                    value = self.current_security.compute_volatility(sessions)
                    if value is None:
                        raise MissingContextValue(f"{identifier.value} for {self.current_symbol} is not available")
                    return value

                case ContextIdentifier.UNIVERSE_SIZE:
                    return Decimal(len(self.universe_symbols))

                case (
                    ContextIdentifier.DAY_OF_WEEK
                    | ContextIdentifier.DAY_OF_MONTH
                    | ContextIdentifier.MONTH
                    | ContextIdentifier.TRADING_DAY_OF_MONTH
                    | ContextIdentifier.TRADING_DAYS_LEFT_IN_MONTH
                ):
                    return self._resolve_calendar_value(identifier, span)

                case ContextIdentifier.ACCOUNT_EQUITY if self.sleeve_weight is not None:
                    return (await self.sleeve_values())["equity"]

                case ContextIdentifier.ACCOUNT_EXPOSURE if self.sleeve_weight is not None:
                    return (await self.sleeve_values())["exposure"]

                case ContextIdentifier.NUMBER_OF_OPEN_POSITIONS if self.sleeve_weight is not None:
                    return (await self.sleeve_values())["positions"]

                case ContextIdentifier.AVAILABLE_CASH if self.sleeve_weight is not None:
                    return (await self.sleeve_values())["cash"]

                case ContextIdentifier.ACCOUNT_EQUITY:
                    equity = await self.broker.get_equity()
                    if equity is None:
                        raise MissingContextValue("Account equity is not available")
                    return equity.amount

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
                    average_cost = (
                        self.current_position.get_average_cost_for_strategy(self.strategy_name)
                        if self.strategy_name
                        else self.current_position.average_cost
                    )
                    if average_cost is not None and average_cost.amount == 0:
                        average_cost = None  # this strategy holds none of the position
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

    def _resolve_market_value(self, identifier: ContextIdentifier, span) -> Decimal:
        """Resolve a MARKET_* identifier from the benchmark security."""
        if not self.market_symbol:
            # A configuration error, not missing data: fail loudly instead of skipping every entry.
            error = ValueError(
                f"{identifier.value} needs a benchmark: pass market_symbol to TradingContext.create() "
                "and include that symbol in the bar provider"
            )
            span.record_exception(error)
            raise error
        if self.market_security is None:
            error = MissingContextValue(f"No data for market symbol {self.market_symbol}")
            span.add_event(str(error))
            raise error
        span.set_attribute("market_symbol", self.market_symbol)
        if identifier == ContextIdentifier.MARKET_PRICE:
            return self.market_security.current_bar.close.amount
        period = Timeframe.d50 if identifier == ContextIdentifier.MARKET_MA50 else Timeframe.d200
        moving_average = self.market_security.compute_moving_average(period)
        if moving_average is None:
            error = MissingContextValue(f"{identifier.value} for {self.market_symbol} is not available")
            span.add_event(str(error))
            raise error
        return moving_average.amount

    def _resolve_calendar_value(self, identifier: ContextIdentifier, span) -> Decimal:
        """Calendar values for the decision session: the current bar's date. In a daily backtest
        and in completed-sessions live trading that is the session whose close the decision uses;
        orders fill at the next session's open."""
        today = self.current_security.current_bar.trading_datetime.trading_date
        if identifier == ContextIdentifier.DAY_OF_WEEK:
            return Decimal(today.isoweekday())
        if identifier == ContextIdentifier.DAY_OF_MONTH:
            return Decimal(today.day)
        if identifier == ContextIdentifier.MONTH:
            return Decimal(today.month)
        month_sessions = self._month_sessions(today, span)
        if identifier == ContextIdentifier.TRADING_DAY_OF_MONTH:
            return Decimal(1 + sum(1 for d in month_sessions if d < today))
        return Decimal(sum(1 for d in month_sessions if d > today))

    def _month_sessions(self, day: date, span) -> list[date]:
        if self.sessions is not None:
            sessions = [d for d in self.sessions if d.year == day.year and d.month == day.month]
            if not sessions:
                raise MissingContextValue(f"The trading calendar does not cover {day:%Y-%m}")
            return sessions
        span.add_event("calendar_approximated_with_weekdays")
        first = day.replace(day=1)
        length = calendar.monthrange(day.year, day.month)[1]
        return [first + timedelta(days=i) for i in range(length) if (first + timedelta(days=i)).weekday() < 5]

    async def sleeve_values(self) -> dict[str, Decimal]:
        """This strategy's sleeve: its budget (`equity` = sleeve_weight x account equity), the value
        and count of its own holdings, `cash` left in the budget (capped by the account's real
        cash) and `exposure` (holdings / budget). Recomputed on each call, since orders change it."""
        if self.sleeve_weight is None:
            raise ValueError("sleeve_values() needs a sleeve_weight")
        if not self.strategy_name:
            raise ValueError("A sleeve needs the strategy name (set by TradingEngine)")
        positions = await self.broker.get_positions() or {}
        held_value = Decimal(0)
        count = 0
        for symbol, position in positions.items():
            quantity = position.get_size_for_strategy(self.strategy_name)
            if quantity <= 0:
                continue
            count += 1
            security = await self._get_security(symbol)
            if security is not None:
                price = security.current_bar.close.amount
            else:  # no data for it this run: fall back to cost
                price = position.get_average_cost_for_strategy(self.strategy_name).amount
            held_value += quantity * price
        budget = (await self.broker.get_equity()).amount * Decimal(str(self.sleeve_weight))
        account_cash = (await self.broker.get_available_cash()).amount
        cash = max(Decimal(0), min(budget - held_value, account_cash))
        return {
            "equity": budget,
            "held_value": held_value,
            "cash": cash,
            "positions": Decimal(count),
            "exposure": held_value / budget if budget else Decimal(0),
        }
