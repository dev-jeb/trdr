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
