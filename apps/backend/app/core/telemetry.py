"""
OpenTelemetry Initialization for Syrabit Backend.

Provides distributed tracing with spans tagged by:
  - lang (en/as)
  - provider (vertex/sarvam)
  - model name
  - user tier
  - RAG chunk count / top score

Exports traces via OTLP (gRPC) to any compatible collector
(GCP Cloud Trace, Jaeger, Grafana Tempo, etc.)

Environment variables (standard OTel SDK config):
  OTEL_EXPORTER_OTLP_ENDPOINT  — Collector endpoint (e.g. https://otel:4317)
  OTEL_SERVICE_NAME            — Service name (default: syrabit-backend)
  OTEL_TRACES_SAMPLER          — Sampler type (default: parentbased_traceidratio)
  OTEL_TRACES_SAMPLER_ARG      — Sample rate (default: 0.1 = 10%)
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)

# Lazy-loaded tracer to avoid import errors when OTel packages aren't installed
_tracer = None
_initialized = False


def init_telemetry(app) -> None:
    """
    Initialize OpenTelemetry tracing for the FastAPI application.

    Auto-instruments:
      - FastAPI (all HTTP requests)
      - httpx (all outgoing HTTP — catches LLM API calls)
      - pymongo (all DB operations)

    Safe to call even if OTel packages aren't installed (graceful no-op).
    """
    global _initialized

    if _initialized:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    except ImportError:
        logger.info(
            "OpenTelemetry packages not installed — tracing disabled. "
            "Install with: pip install opentelemetry-api opentelemetry-sdk "
            "opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-httpx "
            "opentelemetry-exporter-otlp"
        )
        _initialized = True
        return

    try:
        # Build resource with service metadata
        resource = Resource.create(
            {
                SERVICE_NAME: "syrabit-backend",
                "deployment.environment": settings.APP_ENV,
                "service.version": "3.0.0",
            }
        )

        # Create tracer provider with OTLP exporter
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter()  # Reads OTEL_EXPORTER_OTLP_ENDPOINT from env
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        # Auto-instrument FastAPI
        FastAPIInstrumentor.instrument_app(app)

        # Auto-instrument httpx (catches all LLM + embedding API calls)
        HTTPXClientInstrumentor().instrument()

        # Try to instrument pymongo if available
        try:
            from opentelemetry.instrumentation.pymongo import PymongoInstrumentor

            PymongoInstrumentor().instrument()
        except ImportError:
            pass  # Optional — pymongo instrumentation not installed

        _initialized = True
        logger.info("OpenTelemetry initialized successfully")

    except Exception as e:
        logger.warning(f"OpenTelemetry initialization failed (non-fatal): {e}")
        _initialized = True


def get_tracer():
    """
    Get the application tracer instance.

    Returns a no-op tracer if OTel isn't initialized.
    Usage:
        tracer = get_tracer()
        with tracer.start_as_current_span("my.operation") as span:
            span.set_attribute("key", "value")
    """
    global _tracer

    if _tracer is not None:
        return _tracer

    try:
        from opentelemetry import trace

        _tracer = trace.get_tracer("syrabit.backend", "3.0.0")
    except ImportError:
        # Return a no-op tracer
        _tracer = _NoOpTracer()

    return _tracer


class _NoOpSpan:
    """No-op span for when OTel is not available."""

    def set_attribute(self, key: str, value) -> None:
        pass

    def set_status(self, status) -> None:
        pass

    def record_exception(self, exception) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _NoOpTracer:
    """No-op tracer for when OTel is not available."""

    def start_as_current_span(self, name: str, **kwargs):
        return _NoOpSpan()

    def start_span(self, name: str, **kwargs):
        return _NoOpSpan()
