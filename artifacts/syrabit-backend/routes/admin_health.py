"""Task #882 — AdminHealth pill for the edge-proxy-deploy CI workflow.

The ``edge-proxy-deploy`` GitHub Actions workflow
(``.github/workflows/edge-proxy-deploy.yml``) runs unattended on every
push to master that touches ``workers/edge-proxy/**``. Its
``smoke-preview`` job is the canonical signal that the latest worker
build still passes the burst / D1 / KV / bot-cache checks the
``smoke:preview`` script exercises (see replit.md § "Cloudflare
Workers edge-proxy"). When that job goes red the only current signal
is a red badge in the GitHub Actions UI — the AdminHealth dashboard
that on-call already keeps open is silent.

This module surfaces the latest run via the same cron-pill convention
as the Trustpilot / cf-waf-drift pills (Task #751, Task #831). The
shape returned here intentionally differs from those two: this cron
does NOT post a heartbeat to the backend (no point — the GitHub
Actions REST API is the source of truth for whether the workflow ran
and what it concluded), so we hit
``/repos/<owner>/<repo>/actions/workflows/edge-proxy-deploy.yml/runs?per_page=1``
and translate the response into the
``healthy/silent/degraded/never_observed/not_configured`` status keys
the shared ``<CronHealthPill>`` component understands. Concretely:

* ``conclusion == "failure"`` → ``silent`` (red) — the smoke job
  regressed and on-call should look right now.
* run age > 7 days → ``degraded`` (amber) — deploys this rare are
  themselves suspicious (the workflow only fires on
  ``workers/edge-proxy/**`` pushes, but a 7-day silent gap usually
  means master hasn't moved, which is the signal we want to see).
* otherwise (``success``, in-progress, queued) → ``healthy``.
* No runs returned → ``never_observed``.
* ``GITHUB_REPO`` not set → ``not_configured`` (mirrors
  ``routes.admin_ci_status`` so the same setup hint can render).

The configuration env vars are deliberately the same as
``routes.admin_ci_status`` (``GITHUB_REPO``, ``GITHUB_TOKEN``) so a
single PAT covers every admin GitHub-Actions surface; the workflow
file name is overridable via ``EDGE_PROXY_DEPLOY_WORKFLOW`` in case
someone renames it but otherwise defaults to
``edge-proxy-deploy.yml``. Failures to reach GitHub are surfaced via
``error`` so the UI can render "status temporarily unavailable"
rather than going blank — same defensive shape as
``admin_ci_status``.

Task #DIAGNOSTICS: Added /admin/diagnostics endpoint to provide system
health overview including LLM provider status, database pool health,
cache hit rates, and last D1 sync timestamp.
"""
from __future__ import annotations

import io
import json as _json
import logging
import os
import zipfile
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends

from auth_deps import get_admin_user

logger = logging.getLogger(__name__)
router = APIRouter()

_FETCH_TIMEOUT_S = 5.0
_DEFAULT_WORKFLOW = "edge-proxy-deploy.yml"
# 7 days — the task spec: "a run older than 7 days (deploys this rare
# are themselves suspicious)". Override knob exists so the threshold
# can be tuned without a redeploy if the cadence changes.
_STALE_RUN_THRESHOLD_S = int(
    os.environ.get("EDGE_PROXY_DEPLOY_STALE_THRESHOLD_S") or 7 * 86400
)


def _cfg() -> dict[str, str]:
    return {
        "repo": (os.environ.get("GITHUB_REPO") or "").strip(),
        "token": (os.environ.get("GITHUB_TOKEN") or "").strip(),
        "workflow": (
            os.environ.get("EDGE_PROXY_DEPLOY_WORKFLOW") or _DEFAULT_WORKFLOW
        ).strip(),
    }


def _workflow_url(repo: str, workflow: str) -> str:
    """Public URL of the workflow's runs page on github.com."""
    return f"https://github.com/{repo}/actions/workflows/{workflow}"


def _age_seconds(iso_ts: Optional[str]) -> Optional[int]:
    if not iso_ts:
        return None
    try:
        # GitHub returns Z-suffixed UTC timestamps.
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        return None


def _classify(
    *,
    conclusion: Optional[str],
    age_seconds: Optional[int],
) -> str:
    """Map a GitHub run to the shared CronHealthPill status keys.

    Precedence is deliberate: a ``failure`` is the most actionable
    signal so it wins regardless of age — an old red run is still red
    until someone fixes it. A successful run that is simply stale is
    only amber: the smoke gate passed, the box just hasn't been
    touched, which is mildly suspicious but not on-call-page-worthy.
    Anything else (success within window, in-progress, queued) is
    green.
    """
    if (conclusion or "").lower() == "failure":
        return "silent"
    if age_seconds is not None and age_seconds > _STALE_RUN_THRESHOLD_S:
        return "degraded"
    return "healthy"


async def get_edge_proxy_deploy_cron_health() -> dict[str, Any]:
    """Return the latest ``edge-proxy-deploy`` run shaped for the pill.

    Same return shape as :func:`admin_edge_proxy_deploy_cron` (which
    is just a thin auth-gated wrapper). Factored out so the silence
    alerter (Task #893, ``routes.admin_edge_proxy_deploy_cron_alerts``)
    can poll the same snapshot without smuggling a fake admin past
    the FastAPI dependency. Always returns a dict; never raises.
    """
    cfg = _cfg()
    workflow_url = _workflow_url(cfg["repo"] or "syrabit/syrabit", cfg["workflow"])

    if not cfg["repo"]:
        return {
            "configured": False,
            "status": "not_configured",
            "conclusion": None,
            "html_url": None,
            "updated_at": None,
            "lastRunUrl": None,
            "workflowUrl": workflow_url,
            "ageSeconds": None,
            "staleThresholdSeconds": _STALE_RUN_THRESHOLD_S,
            "error": None,
        }

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if cfg["token"]:
        headers["Authorization"] = f"Bearer {cfg['token']}"

    url = (
        f"https://api.github.com/repos/{cfg['repo']}"
        f"/actions/workflows/{cfg['workflow']}/runs?per_page=1"
    )

    base = {
        "configured": True,
        "workflowUrl": workflow_url,
        "staleThresholdSeconds": _STALE_RUN_THRESHOLD_S,
    }

    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_S, headers=headers
        ) as client:
            resp = await client.get(url)
    except Exception as exc:
        logger.warning(f"[edge-proxy-deploy-cron] fetch failed: {exc}")
        return {
            **base,
            "status": "unknown",
            "conclusion": None,
            "html_url": None,
            "updated_at": None,
            "lastRunUrl": None,
            "ageSeconds": None,
            "error": f"github unreachable: {type(exc).__name__}",
        }

    if resp.status_code == 404:
        # Workflow file not present (e.g. renamed). Treat as
        # never_observed so the gray "no run yet" pill renders rather
        # than the red "silent" pill — a 404 here is a config issue,
        # not a CI regression.
        return {
            **base,
            "status": "never_observed",
            "conclusion": None,
            "html_url": None,
            "updated_at": None,
            "lastRunUrl": None,
            "ageSeconds": None,
            "error": None,
        }

    if resp.status_code != 200:
        return {
            **base,
            "status": "unknown",
            "conclusion": None,
            "html_url": None,
            "updated_at": None,
            "lastRunUrl": None,
            "ageSeconds": None,
            "error": f"github returned {resp.status_code}",
        }

    try:
        data = resp.json() or {}
    except Exception:
        data = {}
    items = data.get("workflow_runs") or []
    if not items:
        return {
            **base,
            "status": "never_observed",
            "conclusion": None,
            "html_url": None,
            "updated_at": None,
            "lastRunUrl": None,
            "ageSeconds": None,
            "error": None,
        }

    run = items[0]
    conclusion = run.get("conclusion")
    html_url = run.get("html_url")
    updated_at = run.get("updated_at") or run.get("created_at")
    age_s = _age_seconds(updated_at)
    pill_status = _classify(conclusion=conclusion, age_seconds=age_s)

    return {
        **base,
        "status": pill_status,
        "conclusion": conclusion,
        "html_url": html_url,
        "updated_at": updated_at,
        # Alias used by the shared CronHealthPill wrapper convention
        # (matches lastRunUrl on the cf-waf-drift heartbeat shape) so
        # the pill can render the "Last run" deep-link without the
        # wrapper having to know about the GitHub-specific html_url.
        "lastRunUrl": html_url,
        "ageSeconds": age_s,
        "runStatus": run.get("status"),
        "runId": run.get("id"),
        "runNumber": run.get("run_number"),
        "headSha": (run.get("head_sha") or "")[:7] or None,
        "headBranch": run.get("head_branch"),
        "event": run.get("event"),
        "actor": (run.get("actor") or {}).get("login"),
        "error": None,
    }


@router.get("/admin/health/edge-proxy-deploy/cron")
async def admin_edge_proxy_deploy_cron(
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Auth-gated wrapper for :func:`get_edge_proxy_deploy_cron_health`.

    Always 200 — surfaces ``configured: false`` / ``status:
    not_configured`` when ``GITHUB_REPO`` isn't set so the dashboard
    can render a setup hint instead of an error. Surfaces ``status:
    never_observed`` when the workflow exists but has not produced any
    runs yet (e.g. brand-new workflow file, or repo just renamed).
    GitHub-side errors land in ``error`` with ``status: unknown``;
    this mirrors ``routes.admin_ci_status``'s defensive contract so
    the AdminHealth tile renders an "unavailable" banner instead of
    going blank.

    Task #964 — also surfaces ``slackConfigured`` / ``slackWebhookEnv``
    so the AdminHealth dashboard can render a small "Slack ✓ / ✗"
    badge next to the pill, matching the sibling cf-waf-drift and
    cf-pull cron health endpoints. The webhook URL itself is never
    returned (the boolean only) so this admin-readable JSON surface
    does not leak it.

    Task #969 — both the env-var name and the boolean+name pair come
    from ``routes.slack_alerter_config`` so this no longer late-imports
    private ``_``-prefixed symbols from the alerter module just to
    dodge a circular import.
    """
    from routes.slack_alerter_config import (
        EDGE_PROXY_DEPLOY_SLACK_WEBHOOK_ENV,
        slack_config_for,
    )
    payload = await get_edge_proxy_deploy_cron_health()
    payload.update(slack_config_for(EDGE_PROXY_DEPLOY_SLACK_WEBHOOK_ENV))
    return payload


# ─── Task #902 — alert-state lock-doc snapshot ─────────────────────────────
#
# The cron pill above answers "is the workflow currently red?". The
# silence alerter (Task #893, ``routes.admin_edge_proxy_deploy_cron_alerts``)
# answers "have we paged on-call about that yet?" by persisting its
# dedup state to a Mongo ``job_locks`` doc keyed by
# ``edge_proxy_deploy_cron_alert_state``. That state was previously
# only visible by querying Mongo directly, so admins seeing a red
# pill couldn't tell whether on-call had already been paged (and the
# alerter is in its 24h debounce window) or whether the page is
# still pending. The endpoint below surfaces the lock doc next to
# the pill so the dashboard can render that distinction inline.
#
# The same helper is reused by the cf-waf-drift and Trustpilot
# alert-state endpoints (defined alongside their own pill routes —
# see ``routes.admin_cf_waf_drift_cron_alerts`` and
# ``routes.admin_trustpilot_cron_alerts``) so all three admin
# pills surface the same shape.


def _snake_to_camel(s: str) -> str:
    """``"last_alert_at"`` → ``"lastAlertAt"``. Used to project the
    alerter's snake_case lock-doc fields into the camelCase that the
    rest of the AdminHealth JSON surface uses (matches the existing
    ``lastHeartbeatTs`` / ``lastRunUrl`` convention so the React
    pill props don't have to mix conventions)."""
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


async def _build_alert_state_response(
    lock_id: str,
    realert_interval_s: int,
    broken_state_label: str = "broken",
) -> dict[str, Any]:
    """Read an alerter's ``job_locks`` doc and shape it for the dashboard.

    Always returns 200-ready JSON. When Mongo is unavailable or the
    lock doc doesn't exist yet (the alerter hasn't fired even once),
    we surface ``present: False`` so the UI renders "no alert state on
    file" rather than erroring out.

    The response always includes the static
    ``realertIntervalSeconds`` (the alerter's debounce cadence) plus
    derived ``lastAlertAgeSeconds`` / ``inDebounce`` /
    ``debounceRemainingSeconds`` fields so the frontend doesn't have
    to re-implement timestamp parsing or the debounce-window check.
    Every other lock-doc field is passed through verbatim with its
    snake_case key projected to camelCase (``last_state`` →
    ``lastState``, ``last_html_url`` → ``lastHtmlUrl``, etc.).

    ``broken_state_label`` differs across alerters: the edge-proxy
    alerter writes ``last_state="broken"`` on the broken side, while
    the cf-waf-drift and Trustpilot alerters write
    ``last_state="silent"`` (mirroring the pill colour-mapping).
    Either label is treated as "currently alerting" for the purposes
    of the ``inDebounce`` flag.
    """
    base: dict[str, Any] = {
        "present": False,
        "realertIntervalSeconds": int(realert_interval_s),
        "lastState": None,
        "lastAlertAt": None,
        "lastAlertAgeSeconds": None,
        "inDebounce": False,
        "debounceRemainingSeconds": None,
    }
    try:
        from deps import db, is_mongo_available  # type: ignore
        if not await is_mongo_available():
            return base
        doc = await db.job_locks.find_one({"_id": lock_id})
    except Exception as exc:
        logger.debug(
            f"[admin-health] alert-state read failed for {lock_id}: {exc}"
        )
        return base
    if not doc:
        return base
    base["present"] = True
    for key, val in doc.items():
        if key == "_id":
            continue
        base[_snake_to_camel(key)] = val
    last_alert_at = doc.get("last_alert_at")
    if last_alert_at:
        try:
            s = str(last_alert_at)
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = int(
                (datetime.now(timezone.utc) - dt).total_seconds()
            )
            base["lastAlertAgeSeconds"] = max(0, age)
            if (
                doc.get("last_state") == broken_state_label
                and age < realert_interval_s
            ):
                base["inDebounce"] = True
                base["debounceRemainingSeconds"] = max(
                    0, realert_interval_s - age,
                )
        except Exception as exc:
            logger.debug(
                f"[admin-health] alert-state ts parse failed "
                f"for {lock_id}: {exc}"
            )
    return base


@router.get("/admin/health/edge-proxy-deploy/cron/alert-state")
async def admin_edge_proxy_deploy_cron_alert_state(
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Lock-doc snapshot for the edge-proxy-deploy silence alerter.

    Surfaces what the alerter (Task #893) has on file — last paged
    state, when it last paged, against which run, and how long the
    24h debounce window has left to run — so on-call can distinguish
    "I'm seeing red because nobody has been paged yet" from "I'm
    seeing red because we already paged Nh ago and are in debounce"
    without having to query Mongo directly. Always 200; returns
    ``present: False`` when the alerter hasn't fired even once or
    when Mongo is unavailable.
    """
    from routes.admin_edge_proxy_deploy_cron_alerts import (
        _CRON_REALERT_INTERVAL_S,
        _LOCK_ID,
    )
    return await _build_alert_state_response(
        _LOCK_ID, _CRON_REALERT_INTERVAL_S, broken_state_label="broken",
    )


# ─── Task #918 — paged-on-call audit log ──────────────────────────────────
#
# The Task #902 ``/alert-state`` endpoint above only carries the *most
# recent* page from the alerter lock doc. Admins seeing red can tell
# whether the on-call has been paged "Xh ago" but cannot tell whether
# the workflow has been flapping (paged-recovered-paged-recovered) or
# has been broken steadily for a week. Task #918 adds a tiny audit-log
# Mongo collection (``cron_alert_history``) that every alerter appends
# to on every page + recovery, plus three thin GET endpoints (one per
# pill) so the AdminHealth dashboard can render a "show paged history"
# panel without admins having to dig through Slack logs.
#
# Schema is intentionally flat (one doc per event, indexed by
# ``lock_id`` + ``created_at``) so the same collection serves all
# three pills and a future pill (Task #905+) only has to call the
# shared ``record_cron_alert_event`` helper. The recording side is
# best-effort and never raises (it's already running inside the
# alerter's "fire-and-forget" notification block); the read side
# always returns 200 with an empty ``events`` list when Mongo is down
# or the alerter has never fired (mirrors the ``/alert-state``
# defensive contract so the dashboard never crashes on infra hiccups).

_HISTORY_COLLECTION = "cron_alert_history"
# Defensive cap on stored events per pill. 200 ≫ the 20 the dashboard
# renders, so admins still get history across replica restarts /
# brief Mongo blips, but the collection cannot grow unbounded if the
# alerter ever flaps repeatedly. Trim runs best-effort on every
# insert; a missed trim just means the next insert will catch up.
_HISTORY_MAX_PER_LOCK = 200
# Default page size returned by the GET endpoints. Matches the task
# spec ("the last ~20 alerter events per pill"). Callers can override
# via ``?limit=`` on the endpoint up to a hard cap of _HISTORY_MAX_PER_LOCK
# (so a misbehaving client can't page through every event in one shot).
_HISTORY_DEFAULT_LIMIT = 20


async def record_cron_alert_event(
    db,
    *,
    lock_id: str,
    kind: str,
    sub_kind: Optional[str],
    health: dict[str, Any],
    now_utc: datetime,
) -> None:
    """Append one alerter event to the ``cron_alert_history`` collection.

    Called from each alerter's ``_send_cron_alert`` after the in-app
    notification has been persisted, so a recorded history event
    always corresponds to a notification that actually went out (or
    at least an attempt — the email + Slack fan-outs are themselves
    best-effort, but the in-app notification persists synchronously
    and is the canonical "we paged" signal).

    Best-effort by contract:
      * never raises — wrap the whole body in a broad ``except`` and
        log at DEBUG, mirroring the alerter's notification persist
        block which already swallows Mongo errors;
      * does NOT block on the trim — a missed trim just means the
        next insert will catch up, and the read endpoint caps results
        anyway;
      * keeps the doc shape parallel to the lock-doc fields the
        ``/alert-state`` helper above projects, so the dashboard's
        history panel can render the same primitives the inline
        alert-state caption uses (status, run url, conclusion, age).

    ``kind`` is ``"broken"`` / ``"silent"`` (the alerter's own label)
    on the broken side and ``"recovered"`` after a recovery. Stored
    verbatim so the panel can render the alerter's vocabulary
    instead of reverse-mapping it.
    """
    try:
        import uuid as _uuid
        doc = {
            "_id": str(_uuid.uuid4()),
            "lock_id": lock_id,
            "kind": kind,
            "sub_kind": sub_kind,
            "paged_at": now_utc.isoformat(),
            # Indexed for the bounded-cap trim below + the
            # /alert-history endpoint's sort. Stored as a real datetime
            # (not the ISO string) so motor's BSON encoder roundtrips
            # cleanly and Mongo can sort numerically.
            "created_at": now_utc,
            "last_html_url": health.get("html_url"),
            "last_run_url": (
                health.get("lastRunUrl") or health.get("html_url")
            ),
            "last_workflow_url": health.get("workflowUrl"),
            "last_conclusion": health.get("conclusion"),
            "last_age_seconds": health.get("ageSeconds"),
            "last_run_id": health.get("runId"),
            "last_head_sha": health.get("headSha"),
            "last_pill_status": health.get("status"),
        }
        await db[_HISTORY_COLLECTION].insert_one(doc)
    except Exception as exc:
        logger.debug(
            f"[admin-health] alert-history insert failed for "
            f"{lock_id}: {exc}"
        )
        return
    # Best-effort cap: if the collection has grown past the per-lock
    # ceiling, drop the oldest events. Done in a separate try so a
    # trim failure can't undo the insert above.
    try:
        count = await db[_HISTORY_COLLECTION].count_documents(
            {"lock_id": lock_id}
        )
        excess = int(count) - _HISTORY_MAX_PER_LOCK
        if excess > 0:
            cursor = (
                db[_HISTORY_COLLECTION]
                .find({"lock_id": lock_id}, {"_id": 1})
                .sort("created_at", 1)
                .limit(excess)
            )
            stale_ids: list[Any] = []
            async for doc in cursor:
                stale_ids.append(doc.get("_id"))
            if stale_ids:
                await db[_HISTORY_COLLECTION].delete_many(
                    {"_id": {"$in": stale_ids}}
                )
    except Exception as exc:
        logger.debug(
            f"[admin-health] alert-history trim failed for "
            f"{lock_id}: {exc}"
        )


def _shape_history_event(doc: dict[str, Any]) -> dict[str, Any]:
    """Project a stored ``cron_alert_history`` doc into the JSON
    shape the dashboard's history panel renders. Mirrors the
    snake_case→camelCase convention ``_build_alert_state_response``
    uses so the two payloads can share frontend rendering primitives.
    """
    paged_at = doc.get("paged_at")
    if paged_at is None and isinstance(doc.get("created_at"), datetime):
        paged_at = doc["created_at"].isoformat()
    return {
        "id": str(doc.get("_id")) if doc.get("_id") is not None else None,
        "pagedAt": paged_at,
        "kind": doc.get("kind"),
        "subKind": doc.get("sub_kind"),
        "lastHtmlUrl": doc.get("last_html_url"),
        "lastRunUrl": doc.get("last_run_url"),
        "lastWorkflowUrl": doc.get("last_workflow_url"),
        "lastConclusion": doc.get("last_conclusion"),
        "lastAgeSeconds": doc.get("last_age_seconds"),
        "lastRunId": doc.get("last_run_id"),
        "lastHeadSha": doc.get("last_head_sha"),
        "lastPillStatus": doc.get("last_pill_status"),
    }


async def _build_alert_history_response(
    lock_id: str,
    *,
    limit: int = _HISTORY_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Read the last ``limit`` events for ``lock_id`` and shape for JSON.

    Always 200-ready. Mongo unavailable / no events / stub returns
    cursor errors → ``events: []`` so the dashboard's history panel
    renders an empty-state row instead of a server error. Mirrors
    the ``_build_alert_state_response`` defensive contract above.

    ``limit`` is clamped to ``[1, _HISTORY_MAX_PER_LOCK]`` so a
    misbehaving caller cannot page through every stored event in one
    shot. The caller (FastAPI route) is responsible for parsing the
    ``?limit=`` query param; the helper enforces the bounds.
    """
    safe_limit = max(1, min(int(limit or _HISTORY_DEFAULT_LIMIT),
                            _HISTORY_MAX_PER_LOCK))
    base: dict[str, Any] = {
        "lockId": lock_id,
        "limit": safe_limit,
        "events": [],
    }
    try:
        from deps import db, is_mongo_available  # type: ignore
        if not await is_mongo_available():
            return base
        cursor = (
            db[_HISTORY_COLLECTION]
            .find({"lock_id": lock_id})
            .sort("created_at", -1)
            .limit(safe_limit)
        )
        docs = await cursor.to_list(length=safe_limit)
    except Exception as exc:
        logger.debug(
            f"[admin-health] alert-history read failed for {lock_id}: {exc}"
        )
        return base
    base["events"] = [_shape_history_event(d) for d in (docs or [])]
    return base


@router.get("/admin/health/edge-proxy-deploy/cron/alert-history")
async def admin_edge_proxy_deploy_cron_alert_history(
    limit: int = _HISTORY_DEFAULT_LIMIT,
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Audit-log of pages issued by the edge-proxy-deploy silence
    alerter (Task #893), most recent first.

    Closes the gap left by Task #902 — the lock-doc snapshot at
    ``/alert-state`` only carries the most recent page, so admins
    couldn't tell whether the workflow had been flapping
    (paged-recovered-paged-recovered) or broken steadily. Each entry
    here is one alerter event (``kind`` ∈ {``"broken"``,
    ``"recovered"``}), and the dashboard renders them as a small
    expandable panel under the pill.

    Always 200; returns ``events: []`` when the alerter has never
    fired or when Mongo is unavailable.
    """
    from routes.admin_edge_proxy_deploy_cron_alerts import _LOCK_ID
    return await _build_alert_history_response(_LOCK_ID, limit=limit)


# ──────────────────────────────────────────────────────────────────────────────
# Task #DIAGNOSTICS — System diagnostics endpoint
# ──────────────────────────────────────────────────────────────────────────────

# ─── Task #133 — Cloudflare weekly audit card ─────────────────────────────
#
# Surface the latest ``cloudflare-weekly-audit.yml`` run on the admin health
# panel so teams see pass/warn/fail counts without navigating to GitHub.
#
# Strategy for the summary counts:
#   • The run ``conclusion`` ("success"/"failure") tells us whether any FAIL
#     occurred, but not the individual PASS/WARN/FAIL/PLAN_REQUIRED totals.
#   • The detailed counts live in the ``cf-audit-report-<run_id>`` artifact
#     (a ZIP containing cf-audit-report.json written by cloudflare-full-audit.js).
#   • We download and parse that ZIP with a short timeout and cache the result
#     in Redis (keyed by run_id) for 4 hours so repeated dashboard loads are
#     cheap.  If the download fails we return ``summary: null`` and the card
#     degrades to showing only the run status / age.
#
# Stale threshold: 8 days (the workflow runs weekly; a gap longer than this
# means the schedule broke or the workflow was disabled — shown as amber).

_CF_AUDIT_WORKFLOW = os.environ.get(
    "CF_AUDIT_WORKFLOW", "cloudflare-weekly-audit.yml"
)
_CF_AUDIT_STALE_S = int(os.environ.get("CF_AUDIT_STALE_THRESHOLD_S") or 8 * 86400)
_CF_AUDIT_ARTIFACT_CACHE_TTL_S = 4 * 3600  # 4 hours in Redis


def _cf_audit_github_headers(token: str) -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _download_cf_audit_summary(
    repo: str, run_id: int, token: str
) -> Optional[dict[str, Any]]:
    """Download the cf-audit-report artifact for *run_id* and return its summary dict.

    Returns ``None`` when the artifact is not found, the download fails, or
    the JSON cannot be parsed — so callers can degrade gracefully.
    """
    headers = _cf_audit_github_headers(token)
    artifact_list_url = (
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts"
    )
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
            list_resp = await client.get(artifact_list_url)
            if list_resp.status_code != 200:
                return None
            artifacts = (list_resp.json() or {}).get("artifacts") or []
            report_art = next(
                (a for a in artifacts if a.get("name", "").startswith("cf-audit-report-")),
                None,
            )
            if not report_art:
                return None
            art_id = report_art["id"]
            zip_url = (
                f"https://api.github.com/repos/{repo}/actions/artifacts/{art_id}/zip"
            )
            zip_resp = await client.get(zip_url, follow_redirects=True)
            if zip_resp.status_code != 200:
                return None
            with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
                names = zf.namelist()
                json_name = next(
                    (n for n in names if n.endswith(".json")), None
                )
                if not json_name:
                    return None
                with zf.open(json_name) as f:
                    report = _json.load(f)
            raw = report.get("summary") or {}
            # Normalise: ensure all expected keys exist with int defaults.
            return {
                "pass": int(raw.get("pass") or 0),
                "warn": int(raw.get("warn") or 0),
                "fail": int(raw.get("fail") or 0),
                "skip": int(raw.get("skip") or 0),
                "plan_required": int(raw.get("plan_required") or 0),
                "total": int(raw.get("total") or 0),
            }
    except Exception as exc:
        logger.debug(f"[cf-audit] artifact download failed for run {run_id}: {exc}")
        return None


async def _get_cf_audit_latest() -> dict[str, Any]:
    """Fetch the latest cloudflare-weekly-audit.yml run plus its artifact summary.

    Always returns a dict; never raises.  The ``summary`` key will be ``None``
    when the artifact cannot be fetched (the card degrades to run-status only).
    """
    cfg = {
        "repo": (os.environ.get("GITHUB_REPO") or "").strip(),
        "token": (os.environ.get("GITHUB_TOKEN") or "").strip(),
        "workflow": _CF_AUDIT_WORKFLOW,
    }
    workflow_url = (
        f"https://github.com/{cfg['repo'] or 'syrabit/syrabit'}"
        f"/actions/workflows/{cfg['workflow']}"
    )
    base: dict[str, Any] = {
        "configured": bool(cfg["repo"]),
        "workflowUrl": workflow_url,
        "staleThresholdSeconds": _CF_AUDIT_STALE_S,
    }

    if not cfg["repo"]:
        return {
            **base,
            "status": "not_configured",
            "conclusion": None,
            "lastRunUrl": None,
            "ageSeconds": None,
            "runId": None,
            "summary": None,
            "error": None,
        }

    headers = _cf_audit_github_headers(cfg["token"])
    runs_url = (
        f"https://api.github.com/repos/{cfg['repo']}"
        f"/actions/workflows/{cfg['workflow']}/runs?per_page=1"
    )

    try:
        async with httpx.AsyncClient(timeout=6.0, headers=headers) as client:
            resp = await client.get(runs_url)
    except Exception as exc:
        logger.warning(f"[cf-audit] GitHub fetch failed: {exc}")
        return {
            **base, "status": "unknown", "conclusion": None, "lastRunUrl": None,
            "ageSeconds": None, "runId": None, "summary": None,
            "error": f"github unreachable: {type(exc).__name__}",
        }

    if resp.status_code == 404:
        return {
            **base, "status": "never_observed", "conclusion": None,
            "lastRunUrl": None, "ageSeconds": None, "runId": None,
            "summary": None, "error": None,
        }
    if resp.status_code != 200:
        return {
            **base, "status": "unknown", "conclusion": None, "lastRunUrl": None,
            "ageSeconds": None, "runId": None, "summary": None,
            "error": f"github returned {resp.status_code}",
        }

    try:
        data = resp.json() or {}
    except Exception:
        data = {}
    runs = data.get("workflow_runs") or []
    if not runs:
        return {
            **base, "status": "never_observed", "conclusion": None,
            "lastRunUrl": None, "ageSeconds": None, "runId": None,
            "summary": None, "error": None,
        }

    run = runs[0]
    conclusion = run.get("conclusion")
    html_url = run.get("html_url")
    updated_at = run.get("updated_at") or run.get("created_at")
    age_s = _age_seconds(updated_at)
    run_id = run.get("id")

    if (conclusion or "").lower() == "failure":
        pill_status = "silent"
    elif age_s is not None and age_s > _CF_AUDIT_STALE_S:
        pill_status = "degraded"
    else:
        pill_status = "healthy"

    # Attempt to load the artifact summary — Redis-cached per run_id.
    summary: Optional[dict[str, Any]] = None
    if run_id:
        cache_key = f"cf_audit_summary:{run_id}"
        try:
            from deps import redis_client
            if redis_client:
                raw_cached = await redis_client.get(cache_key)
                if raw_cached:
                    summary = _json.loads(raw_cached)
        except Exception:
            pass

        if summary is None:
            summary = await _download_cf_audit_summary(cfg["repo"], run_id, cfg["token"])
            if summary is not None:
                try:
                    from deps import redis_client
                    if redis_client:
                        await redis_client.set(
                            cache_key,
                            _json.dumps(summary),
                            ex=_CF_AUDIT_ARTIFACT_CACHE_TTL_S,
                        )
                except Exception:
                    pass

    return {
        **base,
        "status": pill_status,
        "conclusion": conclusion,
        "lastRunUrl": html_url,
        "ageSeconds": age_s,
        "updatedAt": updated_at,
        "runId": run_id,
        "runNumber": run.get("run_number"),
        "headSha": (run.get("head_sha") or "")[:7] or None,
        "event": run.get("event"),
        "summary": summary,
        "error": None,
    }


@router.get("/admin/health/cf-audit/latest")
async def admin_cf_audit_latest(
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Return the latest cloudflare-weekly-audit run shaped for the admin health panel.

    Always 200.  The ``summary`` key holds the per-status item counts
    (pass/warn/fail/skip/plan_required/total) parsed from the GitHub Actions artifact
    ZIP; it will be ``null`` when the artifact cannot be fetched (the card degrades
    gracefully to showing only run status and age).

    Task #133.
    """
    return await _get_cf_audit_latest()


@router.get("/admin/diagnostics")
async def admin_diagnostics(admin: dict = Depends(get_admin_user)) -> dict[str, Any]:
    """Return comprehensive system health diagnostics.
    
    Provides a JSON payload summarizing:
    - LLM Provider Status (Gemini/Vertex, Groq, Sarvam, etc.)
    - Database Pool Health (PostgreSQL, MongoDB, Redis)
    - Cache Hit Rates (if available)
    - Last D1 Sync Timestamp
    - Circuit Breaker States
    
    This endpoint stops 404 spam from monitoring systems and provides
    real-time visibility into system health metrics via the admin UI.
    
    Always returns 200; individual component failures are reported in
    the response body rather than raising exceptions.
    """
    from deps import db, pg_pool, is_mongo_available, redis_client
    import time as _time_mod
    
    result: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "llm_providers": {},
        "databases": {},
        "cache": {},
        "d1_sync": {},
        "circuit_breakers": {},
    }
    
    # LLM Provider Status
    try:
        import vertex_services
        # Task #490 — `vertex_chat` module deleted; `vertex_format` now
        # exposes `auth_mode()` for parity. Vertex is `content_format`
        # only (NotebookLM-style polish).
        import vertex_format as _vc
        vertex_health = await vertex_services.health_check()
        ai_entry = {
            "status": "healthy" if vertex_health.get("ok") else "unhealthy",
            "auth_mode": vertex_health.get("auth_mode"),
            "format_auth_mode": _vc.auth_mode(),
            "via_gateway": vertex_health.get("via_cf_gateway"),
            "embeddings": vertex_health.get("embeddings"),
            "generation": vertex_health.get("generation"),
            "details": vertex_health.get("reason") if not vertex_health.get("ok") else None,
        }
        result["llm_providers"]["workers_ai"] = ai_entry
        result["llm_providers"]["gemini"] = ai_entry
        result["llm_providers"]["vertex_gemini"] = ai_entry
    except Exception as e:
        err_entry = {"status": "error", "error": str(e)}
        result["llm_providers"]["workers_ai"] = err_entry
        result["llm_providers"]["gemini"] = err_entry
        result["llm_providers"]["vertex_gemini"] = err_entry

    # Task #494 — content_formatter dispatcher panel. Surfaces the rolling
    # in-process counts of how many recent polish invocations landed on
    # Vertex / WAI-Llama-3.3-70b / passthrough so operators can spot a
    # Vertex outage even when /admin/llm/health still reports it healthy.
    try:
        from content_formatter import get_recent_breakdown as _cf_breakdown
        result["llm_providers"]["content_formatter"] = {
            "policy": "V4 §15 §6 — Vertex primary → workers_ai_llama33_70b fallback (formatter only)",
            "recent": _cf_breakdown(),
        }
    except Exception as e:
        result["llm_providers"]["content_formatter"] = {"status": "error", "error": str(e)}
    
    # Check Sarvam status — V4 §15 / Task #492 scopes Sarvam to the
    # `assamese_rag_chat` LLM client only. There is no longer a translate
    # client, so we report the chat client readiness as the single signal.
    try:
        from deps import sarvam_llm_client
        result["llm_providers"]["sarvam_llm_client"] = {
            "client_ready": sarvam_llm_client is not None,
            "scope": "assamese_rag_chat",
            "policy": "V4 §15 / Task #492 — single surviving Sarvam surface",
        }
    except Exception as e:
        result["llm_providers"]["sarvam_llm_client"] = {"status": "error", "error": str(e)}

    # Task #554 — Azure OpenAI was retired (chat now routes Vertex →
    # Workers-AI Llama-3.2-3B). Report Vertex chat configuration instead so
    # operators can see whether the Gemini 2.5 Flash hot path is wired.
    try:
        from providers import vertex_chat as _vx
        result["llm_providers"]["vertex_chat"] = {
            "status": "healthy" if _vx.is_configured() else "unhealthy",
            "model": (os.environ.get("VERTEX_GEMINI_MODEL") or "gemini-2.5-flash"),
            "details": None if _vx.is_configured()
                       else "GOOGLE_APPLICATION_CREDENTIALS_JSON not set",
        }
    except Exception as e:
        result["llm_providers"]["vertex_chat"] = {"status": "error", "error": str(e)}
    
    # Database Pool Health
    # MongoDB
    try:
        mongo_ok = await is_mongo_available()
        result["databases"]["mongodb"] = {
            "status": "healthy" if mongo_ok else "unhealthy",
        }
    except Exception as e:
        result["databases"]["mongodb"] = {"status": "error", "error": str(e)}
    
    # PostgreSQL
    try:
        if pg_pool:
            async with pg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            result["databases"]["postgresql"] = {"status": "healthy"}
        else:
            result["databases"]["postgresql"] = {"status": "not_configured"}
    except Exception as e:
        result["databases"]["postgresql"] = {"status": "unhealthy", "error": str(e)}
    
    # Redis
    try:
        if redis_client:
            await redis_client.ping()
            result["databases"]["redis"] = {"status": "healthy"}
        else:
            result["databases"]["redis"] = {"status": "not_configured"}
    except Exception as e:
        result["databases"]["redis"] = {"status": "unhealthy", "error": str(e)}
    
    # Cache Stats (if ai_cache is available)
    try:
        import ai_cache
        stats = ai_cache.get_stats()
        result["cache"] = {
            "enabled": True,
            "hits": stats.get("hits", 0),
            "misses": stats.get("misses", 0),
            "hit_rate": round(
                stats.get("hits", 0) / max(1, stats.get("hits", 0) + stats.get("misses", 0)),
                3
            ),
        }
    except Exception:
        result["cache"] = {"enabled": False}
    
    # D1 Sync Status
    try:
        import d1_sync
        result["d1_sync"] = {
            "configured": d1_sync.is_d1_configured(),
            "preview_fanout": d1_sync.is_preview_fanout_configured(),
            "targets": [t[0] for t in d1_sync._sync_targets()],
        }
    except Exception as e:
        result["d1_sync"] = {"status": "error", "error": str(e)}
    
    # Circuit Breaker States
    try:
        import vertex_services
        breaker_snapshot = vertex_services._breaker.snapshot()
        result["circuit_breakers"]["vertex"] = {
            "state": breaker_snapshot.get("state", "unknown"),
            "last_reason": breaker_snapshot.get("last_reason"),
            "failure_count": breaker_snapshot.get("failure_count", 0),
        }
    except Exception as e:
        result["circuit_breakers"]["vertex"] = {"status": "error", "error": str(e)}
    
    return result


# ── Pinecone index health (Task #207) ─────────────────────────────────────────

_PINECONE_CTRL = "https://api.pinecone.io"
_PINECONE_API_VERSION = "2024-10"
_PINECONE_HEALTH_TIMEOUT = 10.0


def _pinecone_cfg() -> dict[str, str]:
    key = (
        os.environ.get("PINECONE_KEY", "").strip()
        or os.environ.get("PINECONE_API_KEY", "").strip()
    )
    index = (os.environ.get("PINECONE_INDEX", "syrabit-ahsec") or "syrabit-ahsec").strip()
    try:
        dims = int(os.environ.get("PINECONE_INDEX_DIMS", "1024") or "1024")
        if dims <= 0:
            dims = 1024
    except (ValueError, TypeError):
        dims = 1024
    return {"key": key, "index": index, "dims": dims}


def _pinecone_ctrl_headers(key: str) -> dict:
    return {
        "Api-Key": key,
        "Content-Type": "application/json",
        "X-Pinecone-API-Version": _PINECONE_API_VERSION,
    }


@router.get("/admin/health/pinecone")
async def admin_pinecone_health(admin: dict = Depends(get_admin_user)) -> dict[str, Any]:
    """Return Pinecone index health: status, vector count, and last-query latency.

    Always returns 200. Individual failures are reported inline.

    Response shape::

        {
          "configured": bool,
          "index_name": str,
          "status": "ready" | "initializing" | "unknown" | "error",
          "state": str,          # raw Pinecone status.state string
          "total_vectors": int | null,
          "dimensions": int,
          "latency_ms": float | null,
          "host": str | null,
          "error": str | null,
        }
    """
    cfg = _pinecone_cfg()

    if not cfg["key"]:
        return {
            "configured": False,
            "index_name": cfg["index"],
            "status": "not_configured",
            "state": None,
            "total_vectors": None,
            "dimensions": cfg["dims"],
            "latency_ms": None,
            "host": None,
            "error": "PINECONE_KEY is not set",
        }

    ctrl_headers = _pinecone_ctrl_headers(cfg["key"])
    index_name = cfg["index"]
    dims = cfg["dims"]
    host: Optional[str] = None
    state: Optional[str] = None
    status_str: str = "unknown"
    total_vectors: Optional[int] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None

    # ── 1. Describe the index (control plane) ─────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=_PINECONE_HEALTH_TIMEOUT) as client:
            r = await client.get(
                f"{_PINECONE_CTRL}/indexes/{index_name}",
                headers=ctrl_headers,
            )
            if r.status_code == 200:
                data = r.json()
                raw_host = data.get("host", "")
                if raw_host and not raw_host.startswith("https://"):
                    raw_host = f"https://{raw_host}"
                host = raw_host or None

                status_obj = data.get("status") or {}
                state = str(status_obj.get("state") or "unknown").lower()
                ready = bool(status_obj.get("ready", False))
                if ready and state in ("ready",):
                    status_str = "ready"
                elif state in ("initializing", "scaling", "creating"):
                    status_str = "initializing"
                elif state in ("ready",):
                    status_str = "ready"
                else:
                    status_str = state or "unknown"
            elif r.status_code == 404:
                status_str = "not_found"
                error = f"Index '{index_name}' not found"
            else:
                status_str = "error"
                error = f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as exc:
        status_str = "error"
        error = str(exc)[:200]

    # ── 2. Describe index stats (data plane) to get vector count ──────────────
    if host:
        try:
            async with httpx.AsyncClient(timeout=_PINECONE_HEALTH_TIMEOUT) as client:
                r = await client.post(
                    f"{host}/describe_index_stats",
                    headers=_pinecone_ctrl_headers(cfg["key"]),
                    json={},
                )
                if r.status_code == 200:
                    stats = r.json()
                    total_vectors = int(stats.get("totalVectorCount", 0))
                else:
                    logger.warning(
                        "[pinecone_health] describe_index_stats HTTP %d: %s",
                        r.status_code, r.text[:100],
                    )
        except Exception as exc:
            logger.warning("[pinecone_health] describe_index_stats failed: %s", exc)

    # ── 3. Test query latency ─────────────────────────────────────────────────
    if host and status_str == "ready":
        try:
            zero_vec = [0.0] * dims
            import time as _time
            t0 = _time.perf_counter()
            async with httpx.AsyncClient(timeout=_PINECONE_HEALTH_TIMEOUT) as client:
                r = await client.post(
                    f"{host}/query",
                    headers=_pinecone_ctrl_headers(cfg["key"]),
                    json={"vector": zero_vec, "topK": 1, "includeMetadata": False},
                )
            latency_ms = round((_time.perf_counter() - t0) * 1000, 1)
            if r.status_code != 200:
                logger.warning(
                    "[pinecone_health] test query HTTP %d: %s",
                    r.status_code, r.text[:100],
                )
                latency_ms = None
        except Exception as exc:
            logger.warning("[pinecone_health] test query failed: %s", exc)
            latency_ms = None

    return {
        "configured": True,
        "index_name": index_name,
        "status": status_str,
        "state": state,
        "total_vectors": total_vectors,
        "dimensions": dims,
        "latency_ms": latency_ms,
        "host": host,
        "error": error,
    }


# ─────────────────────────────────────────────────────────────────────
# Task #558 — Observability card + weekly digest
# ─────────────────────────────────────────────────────────────────────
@router.get("/admin/health/observability")
async def admin_observability(
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Backs the AdminHealth "Observability" card.

    Sources:
      * **Tracing** — OTEL → GCP Cloud Trace (sole exporter, Task #558).
        Pulled from ``tracing.get_otel_health()``: last successful
        export ts, last error, ingestion lag, exported-span count.
      * **Errors** — Sentry Developer free tier (errors-only sink).
        Pulled from ``observability.get_sentry_health()``.

    The 1h / 24h / 7d error-rate windows the task spec mentions are
    rendered client-side from the Sentry events API using the standard
    Sentry token; the card here only ships the init/health snapshot
    and the trace ingestion-lag signal that has no Sentry equivalent.
    """
    from tracing import get_otel_health
    from observability import get_sentry_health

    otel = get_otel_health()
    sentry = get_sentry_health()
    lag = otel.get("ingestion_lag_seconds")
    if lag is None:
        otel_status = "never_observed" if otel.get("enabled") else "not_configured"
    elif lag > 600:
        otel_status = "degraded"  # > 10 min lag → page on-call.
    elif otel.get("last_export_error"):
        otel_status = "silent"
    else:
        otel_status = "healthy"

    return {
        "tracing": {
            "exporter": "gcp_trace",
            "status":   otel_status,
            **otel,
        },
        "errors": {
            "sink":   "sentry_developer_free",
            **sentry,
        },
        "weekly_digest": {
            "transport": "ses",
            "recipient_role": "founder",
            "schedule":  "weekly @ Mon 09:00 UTC",
        },
    }


async def _emit_observability_weekly_digest(db: Any) -> dict[str, Any]:
    """Compose + send the weekly observability digest (Task #558 §D).

    Wired into the existing weekly-digest cron loop (the same one that
    delivers the SEO weekly digest in ``routes.bot_discovery``). This
    function is kept side-effect free w.r.t. the cron scheduler — the
    caller decides cadence — and returns the composed payload so a
    dry-run admin invocation can preview it without SES delivery.

    Body sections:
      1. Top 10 errors (last 7 d) — fetched from Sentry events API
         using ``SENTRY_AUTH_TOKEN`` (admin-only, server-side).
      2. Trace export health — ingestion lag, exported-span count,
         last error from ``tracing.get_otel_health()``.
      3. Sentry init snapshot from ``observability.get_sentry_health()``.

    Delivery uses the SES wrapper that ships in the email-canonical
    task (#557). Until that ships the digest path skips the send and
    returns ``{"sent": False, "reason": "ses-pending-task-557"}`` —
    the umbrella TODO_557 ban will guarantee the SES adapter is the
    only valid sink the day this gets switched on.
    """
    from tracing import get_otel_health
    from observability import get_sentry_health

    otel = get_otel_health()
    sentry = get_sentry_health()
    payload = {
        "subject": "Syrabit observability — weekly digest",
        "tracing": otel,
        "errors": sentry,
        "top_errors_7d": [],  # filled in once the Sentry events API token is wired.
    }
    try:
        from ses_email import send_email  # type: ignore
    except Exception:
        return {"sent": False, "reason": "ses-pending-task-557", "payload": payload}
    try:
        recipient = os.environ.get("OBSERVABILITY_DIGEST_TO", "").strip()
        if not recipient:
            return {"sent": False, "reason": "OBSERVABILITY_DIGEST_TO unset", "payload": payload}
        await send_email(
            to=recipient,
            subject=payload["subject"],
            text_body=_json.dumps(payload, indent=2, default=str),
        )
        return {"sent": True, "payload": payload}
    except Exception as exc:
        logger.warning("[observability] weekly digest send failed: %s", exc)
        return {"sent": False, "reason": str(exc)[:120], "payload": payload}
