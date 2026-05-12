"""Task #969 — shared Slack-alerter config helper for cron silence
alerters.

Three sibling cron silence-alerter modules — ``admin_logs_cf_pull_silence_alerts``,
``admin_cf_waf_drift_cron_alerts``, and ``admin_edge_proxy_deploy_cron_alerts`` —
each fan out their "cron has gone silent / recovered" pages to a
dedicated Slack incoming webhook gated on a per-alerter env var.
Their public health endpoints surface a small ``slackConfigured`` /
``slackWebhookEnv`` pair so the AdminHealth dashboard can render a
"Slack ✓ / ✗" badge next to the cron pill (Task #964).

Before this module existed, each alerter defined its own private
``_slack_webhook_url`` helper plus ``_CRON_SLACK_WEBHOOK_ENV``
constant, and Task #964 had to copy the same ``slackConfigured`` /
``slackWebhookEnv`` block into each. Worse, ``admin_health.py``
needed to surface the same pair on the edge-proxy-deploy cron pill
(which lives in ``admin_health`` itself, not in the alerter module),
and a module-level import of those private symbols would have re-
introduced the circular import the alerter→admin_health dependency
already creates — so it had to do a per-request late import of
``_slack_webhook_url`` and ``_CRON_SLACK_WEBHOOK_ENV`` from the
alerter module.

This module collapses both: every alerter and every health endpoint
calls into :func:`slack_config_for` (and, for posting, into
:func:`slack_webhook_url_for`) keyed on the env-var name. The env-var
names themselves are also re-exported here so admin_health.py can
import them directly without reaching into a sibling module that
already imports back into it.

The webhook URL itself is intentionally never returned by
:func:`slack_config_for` — only the boolean configured-ness and the
env-var name — so admin-readable JSON surfaces never leak the URL.
"""
from __future__ import annotations

import os
from typing import TypedDict


# ─── Per-alerter env-var names (single source of truth) ───────────────────
#
# Each cron silence-alerter posts to a dedicated incoming webhook so
# operators can split per-channel later without code changes. Today
# they're typically all pointed at the same ops channel.
EDGE_PROXY_DEPLOY_SLACK_WEBHOOK_ENV = "EDGE_PROXY_DEPLOY_SLACK_WEBHOOK"
CF_WAF_DRIFT_SLACK_WEBHOOK_ENV = "CF_WAF_DRIFT_SLACK_WEBHOOK"
UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK_ENV = "UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK"
# Task #972 — the Trustpilot AggregateRating JSON-LD verifier alerter
# (``routes.admin_trustpilot_jsonld_status``) posts to its own dedicated
# webhook so the JSON-LD ops channel is independent of the cron silence
# channels above. It was added by Task #757 with a private helper and
# was kept out of the original Task #969 consolidation to preserve
# scope; migrating it here finishes the consolidation so the env-var
# name lives in exactly one place across the whole codebase.
SLACK_TRUSTPILOT_WEBHOOK_ENV = "SLACK_TRUSTPILOT_WEBHOOK_URL"
# Task #258 — CF Gateway slug smoke-test alerter
# (``routes.admin_credits``) fires when any of the PROVIDER_PRIORITY
# feature-key provider slugs returns non-200 or a connection error
# during the /admin/credits/smoke-test probe. The feature count is no
# longer hard-coded here (Task #368) — see admin_credits._provider_
# priority_count() for the dynamic source of truth.
SMOKE_TEST_SLACK_WEBHOOK_ENV = "SMOKE_TEST_SLACK_WEBHOOK"
# Task #509 — the D1 mirror lag watchdog (``routes.admin_d1_mirror_lag_alerts``)
# pages on-call when the Cloudflare D1 mirror's ``lag_seconds`` climbs past
# the configured threshold. Until #509 the fan-out only hit email + in-app
# notifications; this env var slots the alerter into the same shared cron
# Slack channel its sibling silence-alerters already post to so on-call
# doesn't have to keep an inbox tab open.
D1_MIRROR_LAG_SLACK_WEBHOOK_ENV = "D1_MIRROR_LAG_SLACK_WEBHOOK"
# Task #95 — patch-contract broken-flag notifier
# (``scripts/ci/notify_patch_contract_broken.py``) posts to this webhook
# when the PATCH-contract watcher sets the flag for >= 5 minutes, and
# again when the flag is cleared (resolved).  Uses the generic
# ``SLACK_WEBHOOK_URL`` so it shares the same ops channel as other
# non-topic-specific alerts without requiring a dedicated binding.
PATCH_CONTRACT_SLACK_WEBHOOK_ENV = "SLACK_WEBHOOK_URL"


class SlackAlerterConfig(TypedDict):
    """Public shape surfaced on cron health endpoints."""
    slackConfigured: bool
    slackWebhookEnv: str


def slack_webhook_url_for(env_name: str) -> str:
    """Return the trimmed Slack webhook URL configured under ``env_name``,
    or ``""`` when unset / blank.

    Whitespace-only values are treated as not configured: an accidental
    ``"  "`` from a broken secret-manager render would otherwise make
    ``bool(...)`` return ``True`` and the dashboard claim Slack was
    wired even though every POST would 400.
    """
    return (os.environ.get(env_name) or "").strip()


def slack_config_for(env_name: str) -> SlackAlerterConfig:
    """Compute the ``slackConfigured`` / ``slackWebhookEnv`` pair for the
    given env-var name. Used by every cron silence-alerter health
    endpoint so the AdminHealth dashboard can render a "Slack ✓ / ✗"
    badge next to the pill with a uniform shape.

    The boolean is true iff :func:`slack_webhook_url_for` returns a
    non-empty value. The URL itself is deliberately not included so
    admin-readable JSON surfaces never leak it.
    """
    return {
        "slackConfigured": bool(slack_webhook_url_for(env_name)),
        "slackWebhookEnv": env_name,
    }
