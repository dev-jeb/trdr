import asyncio
from decimal import Decimal

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from .trading_engine import TradingEngine
from ..broker.exceptions import InsufficientFundsException
from ...dsl.dsl_ast import NoSizingRuleMatched


class _AlwaysEnterAST:
    """Stub strategy AST that always wants to enter every security."""

    async def evaluate_exit(self, context):
        return False

    async def evaluate_entry(self, context):
        return True

    async def evaluate_sizing(self, context):
        # Large enough that the engine computes a non-zero share count.
        return Decimal(100000)


def _engine_with_inmemory_tracer(trading_context):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    engine = TradingEngine(
        strategy_file_name="test-strat",
        trading_context=trading_context,
        tracer=tracer,
        _from_create=True,
    )
    engine.strategy_ast = _AlwaysEnterAST()
    return engine, exporter


def test_order_rejection_does_not_abort_run(mock_trading_context):
    """A rejected order on one security must not blind us to the rest of the universe."""

    async def _reject(order):
        raise InsufficientFundsException("Insufficient cash to place order")

    mock_trading_context.broker.place_order = _reject
    engine, exporter = _engine_with_inmemory_tracer(mock_trading_context)

    # Must complete without raising despite every order being rejected.
    asyncio.run(engine.execute())

    spans = exporter.get_finished_spans()
    security_spans = [s for s in spans if s.name == "Strategy.process_security"]
    # The fake-data universe has two tradable symbols (AAPL, MSFT); both processed.
    assert len(security_spans) == 2

    rejected_events = [e for s in security_spans for e in s.events if e.name == "order_rejected"]
    assert len(rejected_events) == 2
    assert all(e.attributes["reason"] == "InsufficientFundsException" for e in rejected_events)
    assert all(e.attributes["side"] == "BUY" for e in rejected_events)

    execute_span = next(s for s in spans if s.name == "TradingEngine.execute")
    assert execute_span.attributes["orders.rejected"] == 2
    assert execute_span.attributes["signals.entry"] == 0


def test_no_sizing_rule_match_does_not_abort_run(mock_trading_context):
    """If sizing finds no matching rule on one security, skip just that entry — not the run."""

    class _EnterButUnsizableAST(_AlwaysEnterAST):
        async def evaluate_sizing(self, context):
            raise NoSizingRuleMatched("No sizing rule matched the context.")

    engine, exporter = _engine_with_inmemory_tracer(mock_trading_context)
    engine.strategy_ast = _EnterButUnsizableAST()

    # Must complete without raising despite every entry being unsizable.
    asyncio.run(engine.execute())

    spans = exporter.get_finished_spans()
    security_spans = [s for s in spans if s.name == "Strategy.process_security"]
    assert len(security_spans) == 2  # both symbols reached; run not aborted

    skip_events = [e for s in security_spans for e in s.events if e.name == "entry_skipped_no_sizing_rule"]
    assert len(skip_events) == 2

    execute_span = next(s for s in spans if s.name == "TradingEngine.execute")
    assert execute_span.attributes["signals.entry"] == 0
    assert execute_span.attributes["securities.skipped"] == 2


def test_exits_run_before_entries_and_sold_symbols_are_not_rebought():
    """Rotation: with one slot, AAA (first in the list) can only enter after BBB (held) exits."""
    from types import SimpleNamespace
    from ..shared.models import Money

    class FakeContext:
        def __init__(self):
            self.universe = ["AAA", "BBB"]
            self.holdings = {"BBB": Decimal(10)}
            self.orders = []
            self.broker = SimpleNamespace(cancel_all_orders=self._noop, place_order=self._place)
            self.symbol_stack = []

        async def _noop(self, **kwargs):
            pass

        async def _place(self, order):
            self.orders.append((order.side.name, order.symbol))
            if order.side.name == "SELL":
                self.holdings.pop(order.symbol)
            else:
                self.holdings[order.symbol] = order.quantity_requested

        def rewind(self):
            self.symbol_stack = list(self.universe)

        async def next_symbol(self):
            if not self.symbol_stack:
                return False
            self.current_symbol = self.symbol_stack.pop(0)
            size = self.holdings.get(self.current_symbol)
            self.current_position = SimpleNamespace(get_size_for_strategy=lambda name, s=size: s) if size else None
            self.current_security = SimpleNamespace(
                current_bar=SimpleNamespace(close=Money(amount=Decimal(100)))
            )
            return True

    class OneSlotAST:
        def __init__(self, context):
            self.context = context

        async def evaluate_exit(self, context):
            return True

        async def evaluate_entry(self, context):
            return True

        async def evaluate_sizing(self, context):
            if self.context.holdings:
                raise NoSizingRuleMatched("slot taken")
            return Decimal(1000)

    context = FakeContext()
    engine, _ = _engine_with_inmemory_tracer(context)
    engine.strategy_ast = OneSlotAST(context)

    counts = asyncio.run(engine.execute())

    assert context.orders == [("SELL", "BBB"), ("BUY", "AAA")]  # BBB not re-bought this run
    assert counts["exit"] == 1 and counts["entry"] == 1


class _SizedEntryContext:
    """One symbol at `price`; records every order the engine places."""

    def __init__(self, price, held=None):
        from types import SimpleNamespace
        from ..shared.models import Money

        self.universe = ["AAA"]
        self.held = held
        self.orders = []
        self.broker = SimpleNamespace(cancel_all_orders=self._noop, place_order=self._place)
        self._price = Money(amount=Decimal(price))
        self.symbol_stack = []

    async def _noop(self, **kwargs):
        pass

    async def _place(self, order):
        self.orders.append(order)

    def rewind(self):
        self.symbol_stack = list(self.universe)

    async def next_symbol(self):
        from types import SimpleNamespace

        if not self.symbol_stack:
            return False
        self.current_symbol = self.symbol_stack.pop(0)
        held = self.held
        self.current_position = SimpleNamespace(get_size_for_strategy=lambda name: held) if held else None
        self.current_security = SimpleNamespace(current_bar=SimpleNamespace(close=self._price))
        return True


def _run_entry(price, dollars, fractional, held=None, exit_signal=False):
    class _AST:
        async def evaluate_exit(self, context):
            return exit_signal

        async def evaluate_entry(self, context):
            return True

        async def evaluate_sizing(self, context):
            return Decimal(dollars)

    context = _SizedEntryContext(price, held=held)
    engine = TradingEngine("test-strat", context, _from_create=True, fractional_shares=fractional)
    engine.strategy_ast = _AST()
    summary = asyncio.run(engine.execute())
    return context.orders, summary


def test_whole_shares_by_default():
    orders, _ = _run_entry(price="300", dollars="1000", fractional=False)
    assert [o.quantity_requested for o in orders] == [Decimal(3)]


def test_fractional_shares_buy_the_exact_amount_rounded_down():
    orders, _ = _run_entry(price="300", dollars="1000", fractional=True)
    assert [o.quantity_requested for o in orders] == [Decimal("3.333333")]
    assert orders[0].quantity_requested * Decimal(300) <= Decimal(1000)


def test_fractional_shares_buy_less_than_one_share():
    """The point of fractional shares: a $140 position in a $465 stock."""
    orders, _ = _run_entry(price="465", dollars="140", fractional=True)
    assert [o.quantity_requested for o in orders] == [Decimal("0.301075")]


def test_less_than_one_whole_share_is_skipped_not_sent():
    """Whole-share mode used to send a 0-share order, which the broker rejects with an error."""
    orders, summary = _run_entry(price="465", dollars="140", fractional=False)
    assert orders == []
    assert summary["skipped"] == 1


def test_fractional_order_below_the_broker_minimum_is_skipped():
    orders, summary = _run_entry(price="465", dollars="0.99", fractional=True)
    assert orders == []
    assert summary["skipped"] == 1


def test_market_orders_are_day_orders():
    """Alpaca only accepts fractional orders as day orders."""
    orders, _ = _run_entry(price="300", dollars="1000", fractional=True)
    assert orders[0].time_in_force == "day"


def test_fractional_position_is_sold_in_full():
    orders, _ = _run_entry(price="300", dollars="1000", fractional=True, held=Decimal("0.301075"), exit_signal=True)
    assert [(o.side.name, o.quantity_requested, o.time_in_force) for o in orders] == [("SELL", Decimal("0.301075"), "day")]
