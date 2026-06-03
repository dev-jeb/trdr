"""
Run a strategy locally with tracing exported to an OTLP backend.

Set these before running (example uses Honeycomb; any OTLP backend works):

    export OTEL_SERVICE_NAME=trdr
    export OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io
    export OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=YOUR_INGEST_KEY

If no endpoint is set, tracing is a no-op (nothing exported). Pass console=True
to also print spans to stdout for quick local inspection.
"""

import asyncio

from trdr.telemetry import configure_tracing, shutdown_tracing
from trdr.core.bar_provider.yf_bar_provider.yf_bar_provider import YFBarProvider
from trdr.core.security_provider.security_provider import SecurityProvider
from trdr.core.broker.mock_broker.mock_broker import MockBroker
from trdr.core.trading_engine.trading_engine import TradingEngine
from trdr.core.trading_context.trading_context import TradingContext
from trdr.core.broker.pdt.nun_strategy import NunStrategy

if __name__ == "__main__":

    async def main():
        tracer = configure_tracing(console=True)
        try:
            pdt_strategy = NunStrategy.create(tracer)
            async with await MockBroker.create(pdt_strategy, tracer) as broker:
                bar_provider = await YFBarProvider.create(["TSLA"], tracer)
                security_provider = await SecurityProvider.create(bar_provider, tracer)
                context = await TradingContext.create(security_provider, broker, tracer)
                engine = await TradingEngine.create(
                    "first-strat", context, strategies_dir="../strategies", tracer=tracer
                )
                await engine.execute()
        except Exception as e:
            print(e)
        finally:
            shutdown_tracing()

    asyncio.run(main())
