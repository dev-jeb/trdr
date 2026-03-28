from abc import ABC, abstractmethod
import aiohttp
from typing import Optional, Type, TypeVar, Dict
from opentelemetry import trace
from opentelemetry.trace import NoOpTracer
from decimal import Decimal
from datetime import timedelta

from .models import Order, Position
from ..bar_provider.models import Bar
from ..shared.models import Money, TradingDateTime
from .pdt.base_pdt_strategy import BasePDTStrategy
from .pdt.models import PDTContext
from .pdt.exceptions import PDTRuleViolationException

T = TypeVar("T", bound="BaseBroker")


class BaseBroker(ABC):
    """
    Abstract base class for executing trades and managing account information.

    The broker is responsible for:
    1. Executing buy and sell orders
    2. Tracking positions, cash, and equity values
    3. Enforcing Pattern Day Trading (PDT) rules
    4. Managing authentication and communication with trading APIs

    All broker implementations should provide proper state management, with
    automatic refreshing of stale data and complete OpenTelemetry instrumentation.
    The broker maintains several internal state variables that track account values,
    including equity, cash, and positions.

    Attributes:
        _session: HTTP client session for API communication
        _pdt_strategy: Strategy for enforcing Pattern Day Trading rules
        _tracer: OpenTelemetry tracer for instrumentation
        _positions: Dictionary of current positions (symbol → Position)
        _cash: Available cash in the account
        _equity: Total account value (cash + positions)
        _day_trade_count: Number of day trades in the rolling 5-day window
        _updated_dt: Timestamp of the last data refresh
        _is_stale_flag: Flag indicating if data needs refreshing
    """

    def __init__(self, pdt_strategy: BasePDTStrategy, tracer: trace.Tracer):
        """Initialize the broker with pattern day trading strategy and tracer support.

        Args:
            pdt_strategy: Strategy for enforcing Pattern Day Trading rules
            tracer: OpenTelemetry tracer for instrumentation
        """
        self._session = aiohttp.ClientSession()
        self._pdt_strategy = pdt_strategy
        self._tracer = tracer
        self._positions = None
        self._cash = None
        self._equity = None
        self._day_trade_count = None
        self._updated_dt = TradingDateTime.now()
        self._is_stale_flag = True

    @classmethod
    async def create(
        cls: Type[T],
        pdt_strategy: BasePDTStrategy,
        tracer: trace.Tracer | None = NoOpTracer(),
    ) -> T:
        """
        Factory method to create and initialize a broker instance.

        This async factory method handles proper initialization of resources
        including HTTP sessions, initial account data fetching, and PDT rule
        strategy setup. It uses the template method pattern to allow concrete
        implementations to define their specific initialization logic.

        Args:
            pdt_strategy: Strategy for Pattern Day Trading rule enforcement
            tracer: OpenTelemetry tracer for instrumentation

        Returns:
            An initialized instance of the concrete broker

        Raises:
            Various exceptions depending on implementation, typically related to
            authentication or connectivity issues
        """
        self = cls.__new__(cls)
        BaseBroker.__init__(self, pdt_strategy=pdt_strategy, tracer=tracer)
        with self._tracer.start_as_current_span("BaseBroker.create") as span:
            try:
                await self._initialize()
                await self._stale_handler()
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR)
                raise
            else:
                span.set_status(trace.StatusCode.OK)

            return self

    @abstractmethod
    def _initialize(self):
        pass

    @abstractmethod
    async def _refresh_positions(self):
        """
        Refresh the positions from the broker.
        """
        pass

    @abstractmethod
    async def _refresh_cash(self):
        """
        Refresh the cash from the broker.
        """
        pass

    @abstractmethod
    async def _refresh_equity(self):
        """
        Refresh the equity from the broker.
        """
        pass

    @abstractmethod
    async def _refresh_day_trade_count(self):
        """
        Refresh the day trade count from the broker.
        """
        pass

    @abstractmethod
    async def _place_order(self, order: Order) -> None:
        """
        Implement the low-level order placement logic specific to the subclass (e.g., interaction with the API).
        """
        pass

    @abstractmethod
    async def _cancel_all_orders(self, strategy_name: str) -> None:
        """
        Implement the low-level order cancellation logic specific to the subclass.
        Only cancels orders belonging to the specified strategy.
        """
        pass

    async def _refresh(self):
        with self._tracer.start_as_current_span("BaseBroker._refresh") as span:
            await self._refresh_positions()
            await self._refresh_cash()
            await self._refresh_equity()
            await self._refresh_day_trade_count()
            span.set_status(trace.StatusCode.OK)

    async def get_available_cash(self) -> Money:
        with self._tracer.start_as_current_span("BaseBroker.get_cash") as span:
            await self._stale_handler()
            span.set_attribute("cash", str(self._cash))
            span.set_status(trace.StatusCode.OK)
            return self._cash

    async def get_position(self, symbol: str) -> Optional[Position]:
        with self._tracer.start_as_current_span("BaseBroker.get_position") as span:
            await self._stale_handler()
            position = self._positions.get(symbol, None)
            span.set_attribute("position", str(position))
            span.set_status(trace.StatusCode.OK)
            return position

    async def get_positions(self) -> Optional[Dict[str, Position]]:
        with self._tracer.start_as_current_span("BaseBroker.get_positions") as span:
            await self._stale_handler()
            span.set_attribute("positions_count", len(self._positions))
            span.set_status(trace.StatusCode.OK)
            return self._positions

    async def get_equity(self) -> Money:
        with self._tracer.start_as_current_span("BaseBroker.get_equity") as span:
            await self._stale_handler()
            span.set_attribute("equity", str(self._equity))
            span.set_status(trace.StatusCode.OK)
            return self._equity

    async def get_account_exposure(self) -> Decimal:
        with self._tracer.start_as_current_span("BaseBroker.get_account_exposure") as span:
            await self._stale_handler()
            if self._equity.amount == 0:
                span.set_status(trace.StatusCode.OK)
                span.set_attribute("account_exposure", "0")
                return Decimal(0)
            exposure = (
                sum([position.get_market_value.amount for position in self._positions.values()]) / self._equity.amount
            )
            span.set_attribute("account_exposure", str(exposure))
            span.set_status(trace.StatusCode.OK)
            return exposure

    async def get_position_exposure(self, symbol: str) -> Decimal:
        with self._tracer.start_as_current_span("BaseBroker.get_position_exposure") as span:
            await self._stale_handler()
            position = self._positions.get(symbol, None)
            if position is None:
                span.set_status(trace.StatusCode.OK)
                return Decimal(0)
            if self._equity.amount == 0:
                span.set_status(trace.StatusCode.OK)
                return Decimal(0)
            exposure = position.size * position.average_cost.amount / self._equity.amount
            span.set_attribute("exposure", str(exposure))
            span.set_status(trace.StatusCode.OK)
            return exposure

    async def get_count_of_positions_opened_today(self) -> int:
        with self._tracer.start_as_current_span("BaseBroker.get_count_of_positions_opened_today") as span:
            await self._stale_handler()
            start_of_day = TradingDateTime.start_of_current_day()
            count = 0
            for _, position in self._positions.items():
                orders = position.get_orders_created_after_dt(start_of_day)
                if len(orders) > 0:
                    count += 1
            span.set_status(trace.StatusCode.OK)
            span.set_attribute("positions_opened_today_count", str(count))
            return count

    async def place_order(self, order: Order) -> None:
        """
        This is the concrete place_order method in BaseBroker.
        It performs a state refresh check, runs PDT logic, delegates to the implementation-specific _execute_order,
        and then marks the state as stale.
        """
        with self._tracer.start_as_current_span("BaseBroker.place_order") as span:
            await self._stale_handler()

            await self._validate_pre_order(order)
            await self._place_order(order)
            self._is_stale_flag = True
            span.set_status(trace.StatusCode.OK)
            span.add_event(
                "Order opened successfully: symbol={}, side={}, quantity={}".format(
                    order.symbol, order.side, order.quantity_filled
                )
            )

    async def cancel_all_orders(self, strategy_name: str) -> None:
        with self._tracer.start_as_current_span("BaseBroker.cancel_all_orders") as span:
            span.set_attribute("strategy_name", strategy_name)
            await self._cancel_all_orders(strategy_name)
            self._is_stale_flag = True
            span.set_status(trace.StatusCode.OK)

    async def _validate_pre_order(self, order: Order) -> None:
        """
        Validate a proposed order against PDT rules and other constraints.

        This method creates a PDTContext with all relevant information and
        passes it to the PDT strategy for evaluation.

        Args:
            symbol: The ticker symbol for the order
            side: Buy or sell
            dollar_amount: The dollar amount of the order

        Raises:
            PDTRuleViolationException: If the order would violate PDT rules
            Exception: For other validation failures
        """
        # Create PDT context with all relevant information

        """
        we dont need to check pdt rules if cash is over 25k. However, if this trade puts us below 25k, we need to check pdt rules.
        """
        with self._tracer.start_as_current_span("BaseBroker._validate_pre_order") as span:
            if (order.quantity_requested * order.current_price.amount) > self._cash.amount:
                raise ValueError("Insufficient cash to place order")
            if not (self._cash.amount - order.quantity_requested * order.current_price.amount) < 25000:
                span.add_event("cash is over 25k, skipping pdt rules")
                span.set_status(trace.StatusCode.OK)
                return
            span.add_event(
                "checking pdt rules. cash after order: {}".format(
                    self._cash.amount - order.quantity_requested * order.current_price.amount
                )
            )
            count_of_positions_opened_today = await self.get_count_of_positions_opened_today()
            context = PDTContext(
                order=order,
                position=self._positions.get(order.symbol, None),
                rolling_day_trade_count=self._day_trade_count,
                count_of_positions_opened_today=count_of_positions_opened_today,
            )

            # Let the strategy evaluate the context
            decision = self._pdt_strategy.evaluate_order(context)

            # Process the decision
            if not decision.allowed:
                reason = decision.reason or "PDT restrictions prevent this order"
                raise PDTRuleViolationException(reason)

            # Additional common verifications can be incorporated here

    def _clear_current_state(self) -> None:
        with self._tracer.start_as_current_span("BaseBroker._clear_current_state") as span:
            span.add_event("clearing current state")
            self._cash = None
            self._positions = None
            self._equity = None
            self._day_trade_count = None
            self._updated_dt = None
            span.set_status(trace.StatusCode.OK)

    def _is_state_in_good_order(self) -> None:
        with self._tracer.start_as_current_span("BaseBroker._is_state_in_good_order") as span:
            span.add_event("checking if state is in good order")
            if self._cash is None:
                span.record_exception(ValueError("Cash is not initialized"))
                span.set_status(trace.StatusCode.ERROR)
                raise ValueError("Cash is not initialized")
            if self._positions is None:
                span.record_exception(ValueError("Positions are not initialized"))
                span.set_status(trace.StatusCode.ERROR)
                raise ValueError("Positions are not initialized")
            if not isinstance(self._positions, dict):
                span.record_exception(
                    ValueError("BaseBroker.positions is not a dictionary of the form {symbol: Position}")
                )
                span.set_status(trace.StatusCode.ERROR)
                raise ValueError("BaseBroker.positions is not a dictionary of the form {symbol: Position}")
            for position in self._positions.values():
                if not isinstance(position, Position):
                    span.record_exception(ValueError("BaseBroker.positions contains non-Position objects"))
                    span.set_status(trace.StatusCode.ERROR)
                    raise ValueError("BaseBroker.positions contains non-Position objects")
            if self._equity is None:
                span.record_exception(
                    ValueError("Equity is not initialized. The subclass _refresh() method must set this.")
                )
                span.set_status(trace.StatusCode.ERROR)
                raise ValueError("Equity is not initialized. The subclass _refresh() method must set this.")
            if self._day_trade_count is None:
                span.record_exception(
                    ValueError("Day trade count is not initialized. The subclass _refresh() method must set this.")
                )
                span.set_status(trace.StatusCode.ERROR)
                raise ValueError("Day trade count is not initialized. The subclass _refresh() method must set this.")
            span.set_status(trace.StatusCode.OK)

    async def _stale_handler(self) -> bool:
        with self._tracer.start_as_current_span("BaseBroker._stale_handler") as span:
            # Use existing staleness logic (or refactor as needed)
            if self._updated_dt.timestamp < TradingDateTime.now().timestamp - timedelta(minutes=10):
                span.add_event("stale_state_detected due to timestamp difference")
                self._is_stale_flag = True
                span.add_event("_is_stale_flag set to True")

            if self._is_stale_flag:
                self._clear_current_state()
                await self._refresh()
                self._updated_dt = TradingDateTime.now()
                self._is_state_in_good_order()

            self._is_stale_flag = False
            span.add_event("_is_stale_flag set to False")
            span.set_status(trace.StatusCode.OK)

    async def __aenter__(self):
        """Enter the async context manager.

        Example:
            async with await Broker().create() as broker:
                cash = await broker.get_available_cash()

        Returns:
            self: The broker instance
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the async context manager and cleanup resources.

        Args:
            exc_type: Exception type if an error occurred
            exc_val: Exception value if an error occurred
            exc_tb: Exception traceback if an error occurred
        """
        """Clean up resources by closing the aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None
