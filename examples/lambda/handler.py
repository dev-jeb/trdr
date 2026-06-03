"""
AWS Lambda entrypoint for running a TRDR strategy on a schedule.

Deploy notes
------------
Set these environment variables on the Lambda (vendor-agnostic OTLP config):

    OTEL_SERVICE_NAME=trdr
    OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io
    OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=YOUR_INGEST_KEY

The tracer is configured once at import time (module scope) so it is reused
across warm invocations. The handler ALWAYS calls flush_tracing() in a finally
block — without it, the Lambda freezes before the batch span processor exports
and you lose the trace for that run (the exact "black box" we're avoiding).
"""

import asyncio

from trdr.telemetry import configure_tracing, flush_tracing
from trdr.core.bar_provider.yf_bar_provider.yf_bar_provider import YFBarProvider
from trdr.core.security_provider.security_provider import SecurityProvider
from trdr.core.broker.mock_broker.mock_broker import MockBroker
from trdr.core.trading_engine.trading_engine import TradingEngine
from trdr.core.trading_context.trading_context import TradingContext
from trdr.core.broker.pdt.nun_strategy import NunStrategy

# Configured once per container, reused across warm invocations.
tracer = configure_tracing()

UNIVERSE = ["TSLA", "AAPL", "MSFT", "NVDA", "AMD"]
STRATEGY = "first-strat"


async def _run() -> None:
    pdt_strategy = NunStrategy.create(tracer)
    async with await MockBroker.create(pdt_strategy, tracer) as broker:
        bar_provider = await YFBarProvider.create(UNIVERSE, tracer)
        security_provider = await SecurityProvider.create(bar_provider, tracer)
        context = await TradingContext.create(security_provider, broker, tracer)
        engine = await TradingEngine.create(STRATEGY, context, tracer=tracer)
        await engine.execute()


def handler(event, context):
    """Lambda entrypoint (e.g. triggered by an EventBridge cron)."""
    with tracer.start_as_current_span("lambda.invocation") as span:
        span.set_attribute("strategy", STRATEGY)
        span.set_attribute("universe_size", len(UNIVERSE))
        try:
            asyncio.run(_run())
            return {"status": "ok"}
        except Exception as e:
            span.record_exception(e)
            raise
        finally:
            # Critical: export buffered spans before the runtime freezes.
            flush_tracing()
