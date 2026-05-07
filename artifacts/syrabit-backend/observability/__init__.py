"""Observability package — Task #558.

Splits errors-only sink (Sentry Developer free tier) from the tracing
pipeline (OTEL → GCP Cloud Trace, see ``../tracing.py``). Sentry
Performance / tracing is fully removed; this package owns the
``before_send`` noise filter and the init-from-env helpers.
"""
from .sentry_setup import init_sentry, get_sentry_health, before_send_filter

__all__ = ["init_sentry", "get_sentry_health", "before_send_filter"]
