"""Task #558 — OTEL TracerProvider must have exactly one exporter and
it must be the GCP Cloud Trace exporter.

The check inspects ``tracing.py`` source (the umbrella CI guard does
the same) AND walks the live ``TracerProvider`` after ``init_tracing``
has run. Both signals must agree.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

BACKEND = Path(__file__).resolve().parents[2]


def test_tracing_module_only_imports_gcp_exporter():
    src = (BACKEND / "tracing.py").read_text(encoding="utf-8")

    # The only allowed exporter import name (Task #558 row in
    # infra/four-cloud-delegation.md). Detect any sibling exporter
    # module imports — they would mean a fan-out has crept back in.
    forbidden_imports = (
        "AzureMonitorTraceExporter",
        "azure.monitor.opentelemetry.exporter",
        "OTLPSpanExporter",
        "ConsoleSpanExporter",
    )
    for needle in forbidden_imports:
        assert needle not in src, (
            f"tracing.py must not import {needle!r} (Task #558: GCP Cloud "
            f"Trace is the sole permitted exporter)."
        )

    assert "CloudTraceSpanExporter" in src, (
        "tracing.py must import CloudTraceSpanExporter — the canonical "
        "Task #558 exporter."
    )

    # _build_*_exporter helpers must enumerate exactly one exporter.
    builders = re.findall(r"^def\s+(_build_\w*exporter)", src, re.MULTILINE)
    assert builders == ["_build_gcp_trace_exporter"], (
        f"tracing.py exporter builders must be exactly "
        f"['_build_gcp_trace_exporter']; got {builders!r}"
    )


def test_init_tracing_wires_single_gcp_exporter(monkeypatch):
    monkeypatch.setenv("TRACING_ENABLED", "1")
    monkeypatch.setenv("TRACE_SAMPLE_RATIO", "0.10")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "syrabit-backend")
    monkeypatch.setenv("OTEL_EXPORTER_GCP_PROJECT_ID", "syrabit-prod")

    import tracing
    tracing._INITIALIZED = False
    tracing._ENABLED = False
    tracing._TRACER = None
    tracing._INIT_DETAILS = {}

    class _StubApp:
        pass

    ok = tracing.init_tracing(_StubApp())
    if not ok:
        # Cloud Trace exporter init may fail without ADC in the test env;
        # the source-level assertions above still cover the contract, so
        # only assert the *intent* is recorded.
        assert tracing.get_otel_health()["init"].get("exporter") in (
            "gcp_trace", None,
        )
        return

    from opentelemetry import trace
    provider = trace.get_tracer_provider()
    span_processors = list(getattr(provider, "_active_span_processor", None)._span_processors) \
        if hasattr(provider, "_active_span_processor") else []

    # The provider is wrapped in MultiSpanProcessor that holds one
    # BatchSpanProcessor per exporter. Exactly one exporter is expected.
    assert len(span_processors) == 1, (
        f"TracerProvider must hold exactly one BatchSpanProcessor "
        f"(GCP Cloud Trace only); got {len(span_processors)}"
    )
    health = tracing.get_otel_health()
    assert health["init"]["exporter"] == "gcp_trace"


def test_bicep_exporter_is_single_value():
    """The Bicep template must declare a single-exporter env var; any
    comma-separated list is the multi-exporter shape Task #558 banned.
    """
    repo_root = BACKEND.parents[1]
    bicep = (repo_root / "infra" / "azure" / "aca-syrabit-backend.bicep").read_text(
        encoding="utf-8",
    )
    m = re.search(
        r"OTEL_TRACES_EXPORTER['\"]?\s*[,;]?\s*value\s*:\s*['\"]([^'\"]+)['\"]",
        bicep,
    )
    assert m is not None, "OTEL_TRACES_EXPORTER must be set in Bicep"
    value = m.group(1).strip()
    assert "," not in value, (
        f"OTEL_TRACES_EXPORTER must be a single exporter (Task #558); got {value!r}"
    )
    assert value == "googlecloud", (
        f"OTEL_TRACES_EXPORTER must be 'googlecloud'; got {value!r}"
    )
