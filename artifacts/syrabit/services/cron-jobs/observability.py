"""OpenTelemetry wiring for the Azure Container Apps Jobs cron runner.

Phase 5 — Observability rewire (Task #333).

The ACA Jobs runtime auto-emits stdout / stderr to the Log Analytics
workspace via the diagnostic settings declared in
``infra/azure/observability.tf``, so logs are already covered. This
module adds the matching OTel trace exporter so each cron invocation
shows up as a single root span in Application Insights — that span is
the unit of work the alerter at ``infra/azure/observability.tf``'s
``ai_ingest_stalled`` metric alert measures.

Called by ``run.py`` *before* the dispatch table is consulted so
import-time spans (config-error paths, missing handler symbols)
are also captured.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

log = logging.getLogger("cron-jobs.observability")


def configure_otel(job_name: str) -> Any:
    """Configure OTel + return the root span for this job invocation.

    Resource attributes pin every span to ``cloud.provider=azure`` and
    ``cloud.platform=azure_container_apps_jobs`` so the App Insights
    KQL filters in the runbook (``docs/infra/aca-cutover.md``) can
    slice cron traces vs DO API traces vs AWS Lambda traces without
    relying on naming conventions.

    Returns a started span context manager — caller is responsible
    for using it as ``with span: ...`` so the span ends regardless of
    exception path. Returns ``None`` if the SDK is missing (bench
    rigs / local smoke runs without the deps installed).
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("opentelemetry-sdk not installed — tracing disabled for %s", job_name)
        return None

    current = trace.get_tracer_provider()
    if not isinstance(current, TracerProvider):
        resource = Resource.create({
            "service.name":           f"cron-{job_name}",
            "service.namespace":      "syrabit",
            "service.instance.id":    os.environ.get("CONTAINER_APP_REPLICA_NAME", "unknown"),
            "deployment.environment": os.environ.get("DEPLOY_ENV", "production"),
            "cloud.provider":         "azure",
            "cloud.platform":         "azure_container_apps_jobs",
            "cloud.region":           os.environ.get("AZURE_REGION", "centralindia"),
            "faas.name":              f"aca-job-{job_name}",
            "faas.invocation_id":     os.environ.get("CONTAINER_APP_JOB_EXECUTION_NAME", ""),
        })
        provider = TracerProvider(resource=resource)

        # Application Insights — unified sink.
        ai_conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
        if ai_conn:
            try:
                from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
                provider.add_span_processor(
                    BatchSpanProcessor(AzureMonitorTraceExporter(connection_string=ai_conn))
                )
                log.info("OTel: App Insights exporter wired for cron %s", job_name)
            except Exception:  # pragma: no cover
                log.exception("OTel: AI exporter wiring failed")

        # Axiom — parallel log sink. Cron jobs emit relatively few
        # spans (one root + a handful of children) so the standard
        # OTLP/HTTP exporter is fine; no batching tuning needed.
        axiom_dataset = os.environ.get("AXIOM_DATASET")
        axiom_token = os.environ.get("AXIOM_API_TOKEN")
        if axiom_dataset and axiom_token:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
                    endpoint="https://api.axiom.co/v1/traces",
                    headers={
                        "Authorization":   f"Bearer {axiom_token}",
                        "X-Axiom-Dataset": axiom_dataset,
                    },
                )))
                log.info("OTel: Axiom exporter wired for cron %s", job_name)
            except Exception:  # pragma: no cover
                log.exception("OTel: Axiom exporter wiring failed")

        trace.set_tracer_provider(provider)

    tracer = trace.get_tracer("cron-jobs.run")
    span = tracer.start_as_current_span(
        f"cron.{job_name}",
        attributes={
            "cron.job_name":  job_name,
            "cron.kind":      os.environ.get("JOB_KIND", "loop"),
            "cron.started_at": int(time.time()),
        },
    )
    return span
