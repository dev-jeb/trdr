from typing import Type, TypeVar, Dict, Any
from decimal import Decimal
import uuid
from opentelemetry import trace
from datetime import datetime

from ...dsl.dsl_loader import StrategyDSLLoader
from ..broker.models import OrderSide, Order, OrderStatus, OrderType
from ..trading_context.trading_context import TradingContext
from ..trading_context.exceptions import MissingContextValue
from ..shared.models import TradingDateTime

T = TypeVar("T", bound="TradingEngine")


class TradingEngine:
    """
    Core component for loading, evaluating, and executing trading strategies.

    The TradingEngine class is responsible for:
    1. Loading trading strategies defined in the DSL (.trdr files)
    2. Evaluating entry and exit conditions against the trading context
    3. Executing trades through the broker when conditions are met
    4. Determining position sizing based on strategy rules

    It serves as the "brain" of the trading system, using market data, account data and execution capabilities
    (via the trading context) to execute trading strategies defined in the strategy DSL.

    Attributes:
        strategy_file_name: Name of the .trdr file containing the strategy
        trading_context: Trading context for the strategy
        _tracer: OpenTelemetry tracer for instrumentation
        strategy_ast: Abstract syntax tree representing the parsed strategy
    """

    def __init__(
        self,
        strategy_file_name: str,
        trading_context: TradingContext,
        strategies_dir: str = None,
        tracer: trace.Tracer = trace.NoOpTracer(),
        _from_create: bool = False,
    ):
        if not _from_create:
            raise TypeError("Use TradingEngine.create() instead to create a new trading engine")
        self.strategy_file_name = strategy_file_name
        self.trading_context = trading_context
        self.strategies_dir = strategies_dir
        self._tracer = tracer
        self.strategy_ast = None

    @classmethod
    async def create(
        cls: Type[T],
        strategy_file_name: str,
        trading_context: TradingContext,
        strategies_dir: str = None,
        tracer: trace.Tracer = trace.NoOpTracer(),
    ) -> T:
        """
        Factory method to create and initialize a trading engine.

        This async factory method loads the strategy DSL file, parses it into
        an abstract syntax tree, and prepares the trading engine for execution.

        Args:
            strategy_file_name: Name of the .trdr file containing the strategy definition
            trading_context: Trading context for the strategy
            tracer: OpenTelemetry tracer for instrumentation

        Returns:
            An initialized TradingEngine instance ready for execution

        Raises:
            FileNotFoundError: If the strategy file cannot be found
            ParserError: If there are syntax errors in the strategy file
            Other exceptions depending on the specific DSL implementation
        """
        with tracer.start_as_current_span("TradingEngine.create") as span:
            span.set_attribute("trading_engine.file_name", strategy_file_name)

            self = cls(strategy_file_name, trading_context, strategies_dir, tracer, _from_create=True)

            try:
                await self._load_strategy()
                span.set_status(trace.StatusCode.OK)
                return self
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR)
                raise

    async def _load_strategy(self) -> None:
        with self._tracer.start_as_current_span("TradingEngine._load_strategy") as span:
            try:
                span.set_attribute("trading_engine.file_name", self.strategy_file_name)
                span.add_event("loading_strategy_file")
                loader = StrategyDSLLoader(self.strategies_dir)
                self.strategy_ast = loader.load(self.strategy_file_name)
                span.set_attribute("trading_engine.ast_type", type(self.strategy_ast).__name__)
                span.add_event("strategy_loaded")
                span.set_status(trace.StatusCode.OK)
            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR)
                raise

    def _generate_client_order_id(self) -> str:
        """Generate a client_order_id encoding the strategy name."""
        return f"{self.strategy_file_name}:{uuid.uuid4().hex[:8]}"

    async def execute(self) -> None:
        """
        Execute the trading strategy across all available securities.

        Each strategy only manages its own positions, identified via client_order_id
        tagging. A strategy will only exit shares it bought, and can enter a position
        on a symbol even if another strategy already holds shares in it.
        """
        with self._tracer.start_as_current_span("TradingEngine.execute") as span:
            try:
                span.add_event("canceling_pending_orders_for_strategy")
                await self.trading_context.broker.cancel_all_orders(strategy_name=self.strategy_file_name)

                processed_count = 0
                skipped_count = 0
                exit_signals = 0
                entry_signals = 0

                while await self.trading_context.next_symbol():
                    with self._tracer.start_as_current_span("Strategy.process_security") as security_span:
                        security_span.set_attribute(
                            "trading_context.current_symbol", self.trading_context.current_symbol
                        )
                        security_span.set_attribute("strategy_name", self.strategy_file_name)

                        # Determine this strategy's share count in the current position
                        position = self.trading_context.current_position
                        strategy_size = (
                            position.get_size_for_strategy(self.strategy_file_name)
                            if position
                            else Decimal(0)
                        )
                        security_span.set_attribute("strategy_size", float(strategy_size))

                        if strategy_size > 0:
                            # This strategy owns shares — evaluate exit
                            security_span.add_event("evaluating_exit_conditions")
                            try:
                                should_exit = await self.strategy_ast.evaluate_exit(self.trading_context)
                                security_span.set_attribute("exit_signal", should_exit)

                                if should_exit:
                                    order = Order(
                                        symbol=self.trading_context.current_symbol,
                                        side=OrderSide.SELL,
                                        quantity_requested=strategy_size,
                                        status=OrderStatus.PENDING,
                                        type=OrderType.MARKET,
                                        created_at=TradingDateTime.now(),
                                        current_price=self.trading_context.current_security.current_bar.close,
                                        client_order_id=self._generate_client_order_id(),
                                        strategy_name=self.strategy_file_name,
                                    )

                                    security_span.add_event("placing_sell_order")
                                    await self.trading_context.broker.place_order(order)
                                    exit_signals += 1

                            except MissingContextValue:
                                security_span.add_event("missing_context_value_for_exit")
                                skipped_count += 1
                                continue

                        else:
                            # This strategy has no shares — evaluate entry
                            security_span.add_event("evaluating_entry_conditions")
                            try:
                                should_enter = await self.strategy_ast.evaluate_entry(self.trading_context)
                                security_span.set_attribute("entry_signal", should_enter)

                                if should_enter:
                                    security_span.add_event("evaluating_sizing")
                                    dollar_amount = await self.strategy_ast.evaluate_sizing(self.trading_context)
                                    security_span.set_attribute("dollar_amount_requested", float(dollar_amount))

                                    number_of_shares = int(
                                        dollar_amount // self.trading_context.current_security.current_bar.close.amount
                                    )
                                    security_span.set_attribute("number_of_shares_requested", number_of_shares)

                                    order = Order(
                                        symbol=self.trading_context.current_symbol,
                                        side=OrderSide.BUY,
                                        quantity_requested=number_of_shares,
                                        status=OrderStatus.PENDING,
                                        type=OrderType.MARKET,
                                        created_at=TradingDateTime.now(),
                                        current_price=self.trading_context.current_security.current_bar.close,
                                        client_order_id=self._generate_client_order_id(),
                                        strategy_name=self.strategy_file_name,
                                    )

                                    security_span.add_event("placing_buy_order")
                                    await self.trading_context.broker.place_order(order)
                                    entry_signals += 1

                            except MissingContextValue:
                                security_span.add_event("missing_context_value_for_entry")
                                skipped_count += 1
                                continue

                        processed_count += 1
                        security_span.set_status(trace.StatusCode.OK)

                span.set_attribute("securities.processed", processed_count)
                span.set_attribute("securities.skipped", skipped_count)
                span.set_attribute("signals.exit", exit_signals)
                span.set_attribute("signals.entry", entry_signals)
                span.set_status(trace.StatusCode.OK)

            except Exception as e:
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR)
                raise
