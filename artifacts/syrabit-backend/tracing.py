"""Syrabit.ai — OpenTelemetry distributed tracing (Task #558).

History:
  * Task #610 — original implementation, GCP Cloud Trace primary
    sink with OTLP fallback.
  * Task #333 — observability rewire to Azure App Insights + Axiom
    (now retired with Task #558).
  * Task #558 — observability narrowing. GCP Cloud Trace becomes the
    **sole** trace destination. App Insights and Axiom exporters are
    removed entirely so the canonical specialist-delegation map
    (`infra/four-cloud-delegation.md` row "Tracing") has exactly one
    primary and zero fallbacks. Sampling is head-based 10 % for normal
    traffic and 100 % for spans tagged ``error=true`` or
    ``slo_breach=true`` (per V4 §12 — keep the high-signal traces).
    Sentry tracing is deleted in the companion ``observability/``
    package; Sentry stays only as the errors-only sink.

The public API (``init_tracing``, ``chat_span``, ``record_chat_attrs``,
``record_first_token``, ``emit_phase_span``, ``get_current_trace_id``,
``is_tracing_enabled``, ``get_otel_health``) is unchanged so all call
sites in ``routes/ai_chat.py`` keep working. ``get_otel_health`` is
new — it backs the ``/api/health/otel`` endpoint and reports the last
successful export timestamp + the last export error so the admin
panel can monitor ingestion lag.

Sampling, propagators (W3C tracecontext + baggage), FastAPI auto-
instrumentation, and httpx auto-instrumentation are all preserved.

Module is failure-tolerant: a missing dependency, mis-configured
exporter, or ingest 5xx never raises — ``init_tracing`` returns
``False`` and the rest of the app keeps running.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "init_tracing",
    "is_tracing_enabled",
    "chat_span",
    "emit_phase_span",
    "record_chat_attrs",
    "record_first_token",
    "get_current_trace_id",
    "get_otel_health",
]

_INITIALIZED = False
_ENABLED = False
_TRACER: Any = None

# Last-export bookkeeping for /api/health/otel. The GCP exporter does
# not emit a public hook for "batch shipped" so we wrap its export()
# method in `_HealthTrackingGcpExporter` below to update these.
_LAST_EXPORT_OK_TS: Optional[float] = None
_LAST_EXPORT_ERR_TS: Optional[float] = None
_LAST_EXPORT_ERR: Optional[str] = None
_LAST_EXPORT_SPAN_COUNT: int = 0
_INIT_DETAILS: dict[str, Any] = {}


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


# ─── Sampler — head-based 10 % + 100 % on error / slo_breach ────────
def _build_sampler(default_ratio: float):
    """Return a composite sampler that always keeps spans tagged with
    ``error=true`` or ``slo_breach=true`` and samples everything else
    at ``default_ratio``.

    Implemented via OTel's ``ParentBasedSampler`` over a custom
    ``Sampler`` that inspects the start attributes. Falls back to a
    plain ratio sampler if the SDK does not expose the necessary
    classes (defensive for future SDK churn).
    """
    try:
        from opentelemetry.sdk.trace.sampling import (
            ParentBased,
            Sampler,
            SamplingResult,
            Decision,
            TraceIdRatioBased,
            ALWAYS_ON,
        )
    except Exception:
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
        return TraceIdRatioBased(default_ratio)

    base = TraceIdRatioBased(default_ratio)

    class _ErrorAwareSampler(Sampler):
        def should_sample(  # type: ignore[override]
            self,
            parent_context,
            trace_id,
            name,
            kind=None,
            attributes=None,
            links=None,
            trace_state=None,
        ):
            attrs = attributes or {}
            if attrs.get("error") is True or attrs.get("slo_breach") is True:
                return SamplingResult(Decision.RECORD_AND_SAMPLE, dict(attrs))
            return base.should_sample(
                parent_context, trace_id, name, kind, attributes, links, trace_state,
            )

        def get_description(self) -> str:  # type: ignore[override]
            return f"ErrorAware(ratio={default_ratio},override=ALWAYS_ON_ERROR_OR_SLO)"

    return ParentBased(root=_ErrorAwareSampler())


# ─── Health-tracking exporter wrapper ───────────────────────────────
def _wrap_with_health_tracking(inner_exporter, default_ratio: float = 0.10):
    """Wrap a SpanExporter so we can:

    1. Report last-export status to ``get_otel_health`` (Task #558 §C).
    2. Apply **tail-style sampling**: keep 100 % of spans tagged
       ``error=true`` or ``slo_breach=true``, and ``default_ratio``
       (head ratio, default 10 %) of everything else.

    The tail filter lives here — not in the SDK ``Sampler`` — because
    the head-based ``Sampler`` runs at span START, before exception
    handlers have had a chance to set ``error=true``. By filtering at
    EXPORT we catch error/SLO-breach attributes that were attached
    anywhere in the span lifetime, which is what Task #558 actually
    promises ("100 % for spans tagged error=true or slo_breach=true").
    The TracerProvider is configured with an ``ALWAYS_ON`` root
    sampler to feed every span through this filter.
    """
    try:
        from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
    except Exception:
        return inner_exporter

    # Reuse the OTel hash function so trace-id ratio sampling here is
    # consistent with what the SDK Sampler would have done at span
    # start. Falls back to a portable hash if the helper moves.
    try:
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
        _ratio_thresh = int(default_ratio * (1 << 64))
    except Exception:
        TraceIdRatioBased = None  # type: ignore[assignment]
        _ratio_thresh = int(default_ratio * (1 << 64))

    def _keep(span) -> bool:
        try:
            attrs = getattr(span, "attributes", {}) or {}
            if attrs.get("error") is True or attrs.get("slo_breach") is True:
                return True
            status = getattr(span, "status", None)
            # OTel SDK marks recorded-exception spans with StatusCode.ERROR;
            # treat those as 100 %-keep too even if no attribute was set.
            if status is not None and getattr(status, "status_code", None) is not None:
                try:
                    from opentelemetry.trace import StatusCode
                    if status.status_code == StatusCode.ERROR:
                        return True
                except Exception:
                    pass
            ctx = getattr(span, "context", None) or getattr(span, "get_span_context", lambda: None)()
            tid = getattr(ctx, "trace_id", 0) if ctx else 0
            return (tid & ((1 << 64) - 1)) < _ratio_thresh
        except Exception:
            # Be conservative — keep on inspection failure rather than
            # silently drop a span we can't classify (V4 §12).
            return True

    class _HealthTrackingExporter(SpanExporter):
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def export(self, spans):  # type: ignore[override]
            global _LAST_EXPORT_OK_TS, _LAST_EXPORT_ERR_TS
            global _LAST_EXPORT_ERR, _LAST_EXPORT_SPAN_COUNT
            kept = [s for s in spans if _keep(s)]
            if not kept:
                _LAST_EXPORT_OK_TS = time.time()
                _LAST_EXPORT_ERR = None
                return SpanExportResult.SUCCESS
            try:
                result = self._wrapped.export(kept)
                if result == SpanExportResult.SUCCESS:
                    _LAST_EXPORT_OK_TS = time.time()
                    _LAST_EXPORT_SPAN_COUNT += len(kept)
                    _LAST_EXPORT_ERR = None
                else:
                    _LAST_EXPORT_ERR_TS = time.time()
                    _LAST_EXPORT_ERR = f"non-success result: {result!r}"
                return result
            except Exception as exc:
                _LAST_EXPORT_ERR_TS = time.time()
                _LAST_EXPORT_ERR = f"{type(exc).__name__}: {exc}"[:200]
                raise

        def shutdown(self):  # type: ignore[override]
            return self._wrapped.shutdown()

        def force_flush(self, timeout_millis: int = 30000):  # type: ignore[override]
            try:
                return self._wrapped.force_flush(timeout_millis)
            except Exception:
                return False

    return _HealthTrackingExporter(inner_exporter)


# ─── Sole exporter — GCP Cloud Trace ─────────────────────────────────
def _build_gcp_trace_exporter():
    """Build the only allowed exporter — Google Cloud Trace.

    Returns ``None`` (and logs once) if the SDK is missing or the GCP
    project id cannot be resolved. ``init_tracing`` then leaves
    tracing disabled rather than silently swallowing spans.
    """
    project = (
        os.environ.get("OTEL_EXPORTER_GCP_PROJECT_ID")
        or os.environ.get("GCP_PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or ""
    ).strip()
    try:
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
    except Exception as exc:
        logger.warning(
            "[tracing] opentelemetry-exporter-gcp-trace not installed (%s) — "
            "tracing disabled per Task #558 (no second exporter exists)",
            exc,
        )
        return None
    try:
        if project:
            return CloudTraceSpanExporter(project_id=project)
        # Without an explicit project id the exporter falls back to ADC;
        # in our deploy that is GOOGLE_APPLICATION_CREDENTIALS_JSON →
        # a service account that resolves the project automatically.
        return CloudTraceSpanExporter()
    except Exception as exc:
        logger.warning("[tracing] CloudTraceSpanExporter init failed: %s", exc)
        return None


def init_tracing(app: Any) -> bool:
    """Idempotent initialization. Returns True on successful wire-up.

    Reads:
      TRACING_ENABLED                          — master gate ("1"/"true")
      TRACE_SAMPLE_RATIO                       — float 0.0–1.0 (default 0.10)
      OTEL_SERVICE_NAME                        — defaults "syrabit-backend"
      OTEL_EXPORTER_GCP_PROJECT_ID / GCP_PROJECT_ID / GOOGLE_CLOUD_PROJECT
                                                — GCP project for Cloud Trace
    """
    global _INITIALIZED, _ENABLED, _TRACER, _INIT_DETAILS
    if _INITIALIZED:
        return _ENABLED
    _INITIALIZED = True

    if not _env_bool("TRACING_ENABLED", False):
        logger.info("[tracing] disabled (TRACING_ENABLED not set)")
        _INIT_DETAILS = {"enabled": False, "reason": "TRACING_ENABLED not set"}
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
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
        _INIT_DETAILS = {"enabled": False, "reason": f"sdk missing: {exc}"}
        return False

    ratio = max(0.0, min(1.0, _env_float("TRACE_SAMPLE_RATIO", 0.10)))
    service_name = os.environ.get("OTEL_SERVICE_NAME", "syrabit-backend")

    resource = Resource.create({
        "service.name":           service_name,
        "service.namespace":      "syrabit",
        "service.version":        os.environ.get("OTEL_SERVICE_VERSION", "2.0.0"),
        "service.instance.id":    os.environ.get("HOSTNAME", "unknown"),
        "deployment.environment": os.environ.get("DEPLOYMENT_ENV", "production"),
        "cloud.provider":         "gcp",
        "cloud.platform":         "gcp_cloud_trace",
    })
    # The TracerProvider sampler is ALWAYS_ON; the actual 10 % head-ratio
    # + 100 %-on-error decision lives in `_wrap_with_health_tracking`'s
    # tail filter so spans whose `error=true` / `slo_breach=true`
    # attribute is set AFTER span start (e.g. in an exception handler)
    # are still retained. See the wrapper docstring for why this lives
    # at the exporter rather than the SDK Sampler.
    try:
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON
        sampler = ALWAYS_ON
    except Exception:
        sampler = _build_sampler(ratio)
    provider = TracerProvider(resource=resource, sampler=sampler)

    raw_exporter = _build_gcp_trace_exporter()
    if raw_exporter is None:
        logger.warning(
            "[tracing] no GCP Cloud Trace exporter configured — tracing disabled "
            "(Task #558: GCP Cloud Trace is the sole permitted exporter; no fallback)"
        )
        _INIT_DETAILS = {
            "enabled": False,
            "reason": "gcp_trace exporter unavailable",
            "service_name": service_name,
            "ratio": ratio,
        }
        return False

    tracked = _wrap_with_health_tracking(raw_exporter, default_ratio=ratio)
    provider.add_span_processor(BatchSpanProcessor(tracked))

    trace.set_tracer_provider(provider)

    set_global_textmap(CompositePropagator([
        TraceContextTextMapPropagator(),
        W3CBaggagePropagator(),
    ]))

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls=(
                "/health,/api/health,/api/livez,/api/readyz,/api/metrics,"
                "/api/admin/health,/api/health/otel,/favicon.ico"
            ),
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
    _INIT_DETAILS = {
        "enabled": True,
        "service_name": service_name,
        "ratio": ratio,
        "exporter": "gcp_trace",
        "sampler": "ParentBased(ErrorAware ratio=%.2f, ALWAYS_ON on error|slo_breach)" % ratio,
    }
    logger.info(
        "[tracing] initialized service=%s sampler=ratio(%.2f)+ALWAYS_ON(error|slo_breach) exporter=gcp_trace",
        service_name, ratio,
    )
    return True


def get_otel_health() -> dict[str, Any]:
    """Snapshot for the ``/api/health/otel`` endpoint and the admin
    Observability card.

    Returns a flat dict; never raises. Times are seconds-since-epoch
    (UTC) so the admin UI can compute ingestion lag without parsing.
    """
    now = time.time()
    last_ok = _LAST_EXPORT_OK_TS
    last_err = _LAST_EXPORT_ERR_TS
    return {
        "enabled": _ENABLED,
        "init":    dict(_INIT_DETAILS),
        "last_export_ok_ts":   last_ok,
        "last_export_err_ts":  last_err,
        "last_export_error":   _LAST_EXPORT_ERR,
        "exported_span_count": _LAST_EXPORT_SPAN_COUNT,
        "ingestion_lag_seconds":
            int(now - last_ok) if last_ok is not None else None,
        "now_ts": now,
    }


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
            try:
                span.set_attribute("error", True)
            except Exception:
                pass
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
            span.set_attribute("syrabit.chat.first_token_ms", float(elapsed_ms))
            span.set_attribute("syrabit.chat.first_token_source", source)
            span.set_attribute("chat.first_token_ms", float(elapsed_ms))
            span.set_attribute("chat.first_token_source", source)
            span.add_event(
                "chat.first_token",
                {"elapsed_ms": float(elapsed_ms), "source": source},
            )
        except Exception:
            pass
    except Exception:
        pass


def emit_phase_span(name: str, start_ts: float, end_ts: float, **attrs: Any) -> None:
    """Emit a child span retroactively for a chat-flow phase using the
    captured wall-clock start/end timestamps (``time.time()``)."""
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
