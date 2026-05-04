"""Syrabit.ai — OpenTelemetry distributed tracing.

History:
  * Task #610 — original implementation, GCP Cloud Trace primary
    sink with OTLP fallback.
  * Task #333 — observability rewire. GCP Cloud Trace is RETIRED
    (GCP hosting is decommissioned alongside the four-cloud
    rebalance — see ``docs/infra/cloud-allocation-plan.md`` and
    ``docs/infra/observability.md``). Spans now fan out to two
    parallel sinks via two ``BatchSpanProcessor`` instances:

      * **Azure Application Insights** — unified APM/trace sink
        across DO + AWS + Azure. Wired via ``AzureMonitorTraceExporter``
        when ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set.
      * **Axiom** — parallel log/trace sink with long-term retention.
        Wired via the standard ``OTLPSpanExporter`` (HTTP/protobuf)
        pointed at Axiom's OTLP ingest URL when ``AXIOM_DATASET`` +
        ``AXIOM_API_TOKEN`` are both set.

    Either sink may be missing without breaking the other. With
    neither configured, the SDK falls back to a console exporter
    when ``OTEL_EXPORTER=console`` is set, or stays disabled.

The public API (``init_tracing``, ``chat_span``, ``record_chat_attrs``,
``record_first_token``, ``emit_phase_span``, ``get_current_trace_id``,
``is_tracing_enabled``) is unchanged so all call sites in
``routes/ai_chat.py`` keep working.

Sampling, propagators (W3C tracecontext + baggage), FastAPI auto-
instrumentation, and httpx auto-instrumentation are all preserved.

Module is failure-tolerant: a missing dependency, mis-configured
exporter, or ingest 5xx never raises — ``init_tracing`` returns
``False`` and the rest of the app keeps running.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)

__all__ = [
    "init_tracing",
    "is_tracing_enabled",
    "chat_span",
    "emit_phase_span",
    "record_chat_attrs",
    "record_first_token",
    "get_current_trace_id",
]

_INITIALIZED = False
_ENABLED = False
_TRACER: Any = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def is_tracing_enabled() -> bool:
    return _ENABLED


# ─── Exporter factories ─────────────────────────────────────────────────────
# Each returns either a configured exporter instance or ``None`` when its
# env contract is unmet. The caller wraps each in its own
# ``BatchSpanProcessor`` so a single sink outage does not block the other.


def _build_app_insights_exporter():
    """Azure Application Insights exporter — primary unified APM sink."""
    conn = (os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING") or "").strip()
    if not conn:
        return None
    try:
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
        return AzureMonitorTraceExporter(connection_string=conn)
    except Exception as exc:  # pragma: no cover — exporter is optional
        logger.warning("[tracing] App Insights exporter unavailable: %s", exc)
        return None


def _build_axiom_exporter():
    """Axiom exporter — parallel log/trace sink with long-term retention.

    Axiom speaks OTLP/HTTP at ``https://api.axiom.co/v1/traces`` with
    a bearer token + ``X-Axiom-Dataset`` header — the standard OTLP
    HTTP exporter handles both.
    """
    dataset = (os.environ.get("AXIOM_DATASET") or "").strip()
    token = (os.environ.get("AXIOM_API_TOKEN") or "").strip()
    if not dataset or not token:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        return OTLPSpanExporter(
            endpoint="https://api.axiom.co/v1/traces",
            headers={
                "Authorization":   f"Bearer {token}",
                "X-Axiom-Dataset": dataset,
            },
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("[tracing] Axiom exporter unavailable: %s", exc)
        return None


def init_tracing(app: Any) -> bool:
    """Idempotent initialization. Returns True on successful wire-up.

    Reads:
      TRACING_ENABLED                          — master gate ("1"/"true")
      TRACE_SAMPLE_RATIO                       — float 0.0–1.0 (default 0.1)
      OTEL_SERVICE_NAME                        — defaults "syrabit-backend-do"
      APPLICATIONINSIGHTS_CONNECTION_STRING    — wires App Insights exporter
      AXIOM_DATASET + AXIOM_API_TOKEN          — wires Axiom exporter
      OTEL_EXPORTER=console                    — debug-only console exporter
    """
    global _INITIALIZED, _ENABLED, _TRACER
    if _INITIALIZED:
        return _ENABLED
    _INITIALIZED = True

    if not _env_bool("TRACING_ENABLED", False):
        logger.info("[tracing] disabled (TRACING_ENABLED not set)")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
        from opentelemetry.propagate import set_global_textmap
        from opentelemetry.propagators.composite import CompositePropagator
        from opentelemetry.trace.propagation.tracecontext import (
            TraceContextTextMapPropagator,
        )
        from opentelemetry.baggage.propagation import W3CBaggagePropagator
    except Exception as exc:
        logger.warning(
            "[tracing] OpenTelemetry SDK not installed — tracing disabled (%s)",
            exc,
        )
        return False

    ratio = max(0.0, min(1.0, _env_float("TRACE_SAMPLE_RATIO", 0.1)))
    service_name = os.environ.get("OTEL_SERVICE_NAME", "syrabit-backend-do")

    # Resource attributes pinned to "where is this running?" so App
    # Insights queries can filter by `cloud.provider` and on-call can
    # answer "is the slowness on DO, AWS, or Azure?" without relying
    # on naming conventions.
    resource = Resource.create({
        "service.name":           service_name,
        "service.namespace":      "syrabit",
        "service.version":        os.environ.get("OTEL_SERVICE_VERSION", "2.0.0"),
        "service.instance.id":    os.environ.get("HOSTNAME", "unknown"),
        "deployment.environment": os.environ.get("DEPLOYMENT_ENV", "production"),
        "cloud.provider":         "digitalocean",
        "cloud.platform":         "digitalocean_app_platform",
        "cloud.region":           os.environ.get("DO_REGION", "blr1"),
    })
    sampler = TraceIdRatioBased(ratio)
    provider = TracerProvider(resource=resource, sampler=sampler)

    processors_added = 0
    sinks: list[str] = []

    ai_exporter = _build_app_insights_exporter()
    if ai_exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(ai_exporter))
        processors_added += 1
        sinks.append("app_insights")

    axiom_exporter = _build_axiom_exporter()
    if axiom_exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(axiom_exporter))
        processors_added += 1
        sinks.append("axiom")

    if processors_added == 0:
        # Allow ``OTEL_EXPORTER=console`` for debugging in dev where
        # neither cloud sink is wired. Otherwise warn-and-disable so
        # spans aren't silently dropped on prod misconfig.
        if (os.environ.get("OTEL_EXPORTER", "") or "").strip().lower() == "console":
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            sinks.append("console")
        else:
            logger.warning(
                "[tracing] no exporters configured — set "
                "APPLICATIONINSIGHTS_CONNECTION_STRING and/or "
                "AXIOM_DATASET+AXIOM_API_TOKEN. Tracing disabled."
            )
            return False

    trace.set_tracer_provider(provider)

    set_global_textmap(CompositePropagator([
        TraceContextTextMapPropagator(),
        W3CBaggagePropagator(),
    ]))

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="/health,/api/health,/api/livez,/api/readyz,/api/metrics,/api/admin/health,/favicon.ico",
        )
    except Exception as exc:
        logger.warning("[tracing] FastAPI auto-instrumentation failed: %s", exc)

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception as exc:
        logger.debug("[tracing] httpx instrumentation skipped: %s", exc)

    _TRACER = trace.get_tracer("syrabit.chat")
    _ENABLED = True
    logger.info(
        "[tracing] initialized service=%s sampler=ratio(%.2f) sinks=%s",
        service_name, ratio, ",".join(sinks),
    )
    return True


@contextmanager
def chat_span(name: str, **attrs: Any) -> Iterator[Any]:
    """Start a custom span that nests under the current request span.

    No-op (yields None) when tracing is disabled, so call sites can
    always wrap interesting work without checking flags first.
    """
    if not _ENABLED or _TRACER is None:
        yield None
        return
    span = _TRACER.start_span(name)
    try:
        for k, v in attrs.items():
            try:
                span.set_attribute(k, v)
            except Exception:
                pass
        yield span
    except Exception as exc:
        try:
            span.record_exception(exc)
            from opentelemetry.trace import Status, StatusCode
            span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
        except Exception:
            pass
        raise
    finally:
        try:
            span.end()
        except Exception:
            pass


def record_chat_attrs(**attrs: Any) -> None:
    """Attach arbitrary key/value attributes to the *current* span
    (the one auto-created by FastAPIInstrumentor for the HTTP request)."""
    if not _ENABLED:
        return
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        if span is None:
            return
        for k, v in attrs.items():
            try:
                if v is not None:
                    span.set_attribute(k, v)
            except Exception:
                pass
    except Exception:
        pass


def record_first_token(elapsed_ms: float, *, source: str = "llm") -> None:
    """Record chat first-token latency on the current request span."""
    if not _ENABLED:
        return
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        if span is None:
            return
        try:
            # Canonical keys (match dashboard/alert contract documented
            # in docs/PERFORMANCE_MONITORING.md).
            span.set_attribute("syrabit.chat.first_token_ms", float(elapsed_ms))
            span.set_attribute("syrabit.chat.first_token_source", source)
            # Legacy keys preserved for backwards compatibility with any
            # ad-hoc queries that may already reference them.
            span.set_attribute("chat.first_token_ms", float(elapsed_ms))
            span.set_attribute("chat.first_token_source", source)
            span.add_event("chat.first_token", {"elapsed_ms": float(elapsed_ms), "source": source})
        except Exception:
            pass
    except Exception:
        pass


def emit_phase_span(name: str, start_ts: float, end_ts: float, **attrs: Any) -> None:
    """Emit a child span retroactively for a chat-flow phase using the
    captured wall-clock start/end timestamps (``time.time()``).

    Used by ``routes/ai_chat.py`` to materialize ``chat.retrieval``,
    ``chat.llm_call`` and ``chat.post_processing`` as proper nested
    spans without restructuring the streaming generator. The start/end
    pair is converted to nanoseconds (OTel's native unit) and passed
    via ``start_time`` / ``end_time``. No-op when tracing is disabled
    or when ``end_ts < start_ts``.
    """
    if not _ENABLED or _TRACER is None:
        return
    try:
        if end_ts < start_ts:
            return
        start_ns = int(start_ts * 1_000_000_000)
        end_ns = int(end_ts * 1_000_000_000)
        span = _TRACER.start_span(name, start_time=start_ns)
        try:
            span.set_attribute("phase.duration_ms", round((end_ts - start_ts) * 1000.0, 3))
            for k, v in attrs.items():
                try:
                    if v is not None:
                        span.set_attribute(k, v)
                except Exception:
                    pass
        finally:
            try:
                span.end(end_time=end_ns)
            except Exception:
                pass
    except Exception:
        pass


def get_current_trace_id() -> str:
    """Hex trace-id of the active span, or "" if tracing inactive."""
    if not _ENABLED:
        return ""
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        if span is None:
            return ""
        ctx = span.get_span_context()
        if not ctx or not ctx.is_valid:
            return ""
        return format(ctx.trace_id, "032x")
    except Exception:
        return ""
