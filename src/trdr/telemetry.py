"""
Telemetry setup for TRDR.

Wires OpenTelemetry tracing to any OTLP-compatible managed backend (Honeycomb,
Grafana Cloud Tempo, New Relic, Axiom, ...) using the *standard* OTEL
environment variables, so the vendor is pure configuration — switching backends
never requires a code change.

Configuration (environment variables):

    OTEL_EXPORTER_OTLP_ENDPOINT   Base URL of the backend. Tracing is only
                                  enabled when this (or the traces-specific
                                  variant) is set; otherwise a no-op tracer is
                                  returned so local runs and tests stay clean.
    OTEL_EXPORTER_OTLP_HEADERS    Auth headers, e.g. "x-honeycomb-team=KEY".
    OTEL_SERVICE_NAME             Service name shown in the backend (default "trdr").

Example — Honeycomb:

    OTEL_SERVICE_NAME=trdr
    OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io
    OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=YOUR_INGEST_KEY

The OTLP **HTTP/protobuf** exporter is used deliberately: it survives the
Lambda freeze/thaw lifecycle far more reliably than gRPC's long-lived
connections.

Lambda note: spans are batched, so you MUST call ``flush_tracing()`` before the
handler returns — otherwise the runtime freezes before the batch is exported and
the trace is lost. See ``examples/lambda/handler.py``.
"""

import os
from importlib.metadata import PackageNotFoundError, version
from typing import Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

_provider: Optional[TracerProvider] = None


def _trdr_version() -> str:
    """Installed trdr version, so spans are segmentable by release (before/after a deploy)."""
    try:
        return version("trdr")
    except PackageNotFoundError:
        return "unknown"


def _otlp_endpoint_configured() -> bool:
    return bool(
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    )


def configure_tracing(
    service_name: Optional[str] = None,
    console: bool = False,
) -> trace.Tracer:
    """
    Configure and install a global tracer provider, returning a tracer to pass
    into TRDR components (broker, bar provider, engine, ...).

    When no OTLP endpoint is configured the provider has no exporter attached,
    which is effectively a no-op — safe to call unconditionally in any
    environment.

    Args:
        service_name: Overrides OTEL_SERVICE_NAME / the "trdr" default.
        console: Also print spans to stdout (handy for local debugging).

    Returns:
        A tracer named after the service.
    """
    global _provider

    service_name = service_name or os.getenv("OTEL_SERVICE_NAME", "trdr")
    # service.version is stamped automatically; deployment.environment (and any
    # other deploy metadata) can be supplied via the OTEL_RESOURCE_ATTRIBUTES env
    # var, which Resource.create merges in. Explicit attributes here win on conflict.
    resource = Resource.create({"service.name": service_name, "service.version": _trdr_version()})
    provider = TracerProvider(resource=resource)

    if _otlp_endpoint_configured():
        # OTLPSpanExporter() reads OTEL_EXPORTER_OTLP_ENDPOINT / _HEADERS itself.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

    if console:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _provider = provider
    return trace.get_tracer(service_name)


def flush_tracing(timeout_millis: int = 5000) -> None:
    """
    Force-export any buffered spans.

    MUST be called before a Lambda handler returns, otherwise the batch
    processor never gets a chance to flush before the runtime freezes.
    """
    if _provider is not None:
        _provider.force_flush(timeout_millis)


def shutdown_tracing() -> None:
    """Flush and tear down the provider (for long-lived/local processes)."""
    if _provider is not None:
        _provider.shutdown()
