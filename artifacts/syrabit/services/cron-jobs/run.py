"""
Container Apps Jobs entrypoint.

Phase 4 — Cron port (Task #332).

This is the single binary executed by every aca-job-* Container Apps
Job defined in `infra/azure/container-apps-jobs.tf`. Terraform sets
two env vars per job:

    JOB_NAME — matches the key in DISPATCH below
    JOB_KIND — "scheduler" (was a Cloud Scheduler job) or
               "loop"      (was an in-process asyncio loop)

We dispatch on JOB_NAME to the matching coroutine in the legacy
backend module (artifacts/syrabit-backend/) and run it for exactly
one iteration of the migrated body. The Container Apps Job's cron
expression is the schedule.

One-shot execution contract
---------------------------
There are TWO modes per dispatch entry:

* **Native one-shot** — the legacy module exports a
  `<fn>_run_once` sibling. ``_resolve()`` returns it directly, no
  sleep patching needed. This is the recommended long-term form.

* **Sleep-bracketed fallback** — the legacy module only exposes the
  infinite ``while True: <body>; await asyncio.sleep(N)`` form. The
  third element of the DISPATCH tuple, ``has_boot_stagger``,
  declares whether the body is preceded by an
  ``await asyncio.sleep(stagger)`` call:
    - ``False`` (default) — exit at the FIRST long sleep, which is
      the inter-iteration wait at the bottom of `while True:`. Body
      ran exactly once; no risk of double-execution.
    - ``True`` — short-circuit the FIRST long sleep (boot stagger
      present), then exit at the SECOND long sleep (the inter-
      iteration wait). Body still runs exactly once.

The flag is per-job because legacy code is inconsistent: most loops
have a stagger, but a handful do not. The default of ``False`` is
the safe choice — a missing-stagger flag means body runs once
without stagger; an extra-stagger flag would mean body never runs.

Sleeps shorter than ``ITERATION_SLEEP_THRESHOLD_S`` always pass
through unchanged so retry/backoff inside the body keep working.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import signal
import sys
import time
from typing import Any, Awaitable, Callable

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cron-jobs.run")


# ─── Argument adapters ───────────────────────────────────────────────────────
async def _with_db(coro_factory: Callable[[Any], Awaitable[None]]) -> None:
    from server import db  # type: ignore  # resolved at runtime
    await coro_factory(db)


async def _per_language_loop(lang: str) -> None:
    """Resolve and invoke the language-specific nightly loop via the
    `per_language_nightly_loops()` factory in bench.grounded_recall."""
    from bench.grounded_recall import per_language_nightly_loops  # type: ignore
    factories = per_language_nightly_loops()
    if lang not in factories:
        raise SystemExit(f"per_language_nightly_loops missing entry for {lang!r}")
    await factories[lang]()


# ─── DISPATCH ────────────────────────────────────────────────────────────────
# {JOB_NAME: (target, one_shot_timeout_s, has_boot_stagger)}
#   target            "module:callable" or "__adapter:<key>"
#   one_shot_timeout  hard ceiling for the body; should be < the
#                     matching `replica_timeout_in_seconds` in
#                     container-apps-jobs.tf so wait_for() trips
#                     BEFORE Container Apps SIGTERMs the pod.
#   has_boot_stagger  see module docstring. Set per audit of the
#                     loop body's first ``asyncio.sleep`` call.
DISPATCH: dict[str, tuple[str, int, bool]] = {
    # ── 38 ex-asyncio loops ──
    "seed-syllabus-embeddings":         ("server:_seed_syllabus_embeddings",                                       300,  False),
    "exam-reminder":                    ("routes.admin_notifications:_exam_reminder_loop",                          60,  False),  # 30s stagger < threshold
    "push-prune":                       ("routes.admin_notifications:_push_prune_loop",                            600,  True),
    "ensure-synthetic-alerts-ttl":      ("routes.admin_notifications:ensure_synthetic_alerts_ttl_index",            60,  False),  # one-shot helper
    "synthetic-alert-cleanup":          ("routes.admin_notifications:_synthetic_alert_cleanup_loop",               300,  True),
    "cf-access-silent-lockout":         ("server:_cf_access_silent_lockout_loop",                                  120,  True),
    "endpoint-health-alert":            ("routes.bot_discovery:_endpoint_health_alert_loop",                       120,  True),
    "hydrate-alert":                    ("routes.analytics:_hydrate_alert_loop",                                   180,  True),
    "review-prompt-alert":              ("routes.admin_review_prompts:_review_prompt_alert_loop",                  240,  True),
    "review-prompt-weekly-digest":      ("routes.admin_review_prompts:_review_prompt_weekly_digest_loop",          900,  True),
    "sitemap-indexnow-diff":            ("routes.bot_discovery:_sitemap_indexnow_diff_loop",                       600,  True),
    "bing-submit-daily":                ("routes.bot_discovery:_bing_submit_daily_loop",                           600,  True),
    "bing-keyword-refresh":             ("routes.bot_discovery:_bing_keyword_refresh_loop",                        600,  True),
    "seo-health-alert":                 ("routes.bot_discovery:_seo_health_alert_loop",                            240,  True),
    "seo-weekly-digest":                ("routes.bot_discovery:_seo_weekly_digest_loop",                           900,  True),
    "entity-seo":                       ("entity_seo_health:_entity_seo_loop",                                     600,  True),
    "topic-discovery":                  ("topic_discovery_service:_topic_discovery_loop",                          900,  True),
    "internal-linker":                  ("__adapter:internal_linker",                                              900,  True),
    "grounded-recall-nightly":          ("bench.grounded_recall:_grounded_recall_nightly_loop",                   1800,  True),
    "seo-auto-publish":                 ("seo_engine:_seo_auto_publish_loop",                                      900,  True),
    "seo-auto-publish-staleness":       ("seo_engine:_seo_auto_publish_staleness_loop",                            300,  True),
    "seo-staleness-heartbeat":          ("seo_engine:_seo_staleness_heartbeat_loop",                               300,  True),
    "ci-alert":                         ("routes.admin_ci_alerts:_ci_alert_loop",                                  120,  True),
    "trustpilot-feed-alert":            ("routes.admin_trustpilot_alerts:_trustpilot_feed_alert_loop",             180,  True),
    "trustpilot-refresh-cron-alert":    ("routes.admin_trustpilot_cron_alerts:_trustpilot_refresh_cron_alert_loop", 180, True),
    "cf-waf-drift-cron-alert":          ("routes.admin_cf_waf_drift_cron_alerts:_cf_waf_drift_cron_alert_loop",    180,  True),
    "cf-pull-silence-alert":            ("routes.admin_logs_cf_pull_silence_alerts:_cf_pull_silence_alert_loop",   180,  True),
    "edge-proxy-deploy-cron-alert":     ("routes.admin_edge_proxy_deploy_cron_alerts:_edge_proxy_deploy_cron_alert_loop", 180, True),
    "slack-webhook-missing-alert":      ("routes.admin_slack_webhook_missing_alerts:_slack_webhook_missing_alert_loop", 180, False),  # audit inconclusive — safe default
    "cf-bot-report":                    ("routes.bot_discovery:_cf_bot_report_loop",                               600,  True),
    "pages-deploy-nightly":             ("pages_deploy:nightly_loop",                                             1800,  False),
    "bot-traffic-report":               ("routes.bot_traffic_report:_bot_traffic_report_loop",                     900,  True),
    "collection-size-snapshot":         ("routes.admin_advanced:_collection_size_snapshot_loop",                   300,  True),
    "cache-warm":                       ("routes.admin_advanced:_cache_warm_loop",                                 600,  True),
    "vertex-startup-probe":             ("server:_vertex_startup_probe",                                            60,  False),  # one-shot probe
    "vertex-periodic-probe":            ("server:_vertex_periodic_probe_loop",                                     120,  True),
    "unified-logs-cf-pull":             ("routes.admin_logs:_unified_logs_cf_pull_loop",                           120,  False),  # 30s stagger < threshold
    # Task #434 — alert on-call when the embed backfill stalls or starts
    # failing. Boot stagger = ALERT_LOOP_INTERVAL_S (default 300s ≥ 45s
    # threshold) so the first long sleep is the stagger, second is the
    # inter-iteration wait. One body iteration evaluates state once and
    # may dispatch via metrics._dispatch_alert.
    "embed-backfill-alert":             ("__adapter:embed_backfill_alert",                                         120,  True),
    # ── 3 additional periodic loops migrated alongside the original 38
    #    so the API tier is fully out of the cron business once the
    #    aca-jobs takeover is on (Task #332 reviewer rev #8).
    "alerting":                         ("metrics:_alerting_loop",                                                 180,  True),   # 60s stagger ≥ threshold
    "chat-speedup-flush":               ("chat_speedup_metrics:periodic_flush_loop",                                60,  False),  # short flush interval, no stagger
    "seo-remediation":                  ("seo_remediation_service:_seo_remediation_loop",                          600,  False),  # idle_backoff_secs first; no boot stagger
    "rate-limiter-cleanup":             ("auth_deps:_rate_limiter_cleanup",                                        120,  False),  # short interval
    "bg-health":                        ("metrics:_bg_health_loop",                                                120,  False),
    "library-prewarm":                  ("server:_prewarm_library_cache",                                          300,  False),  # one-shot prewarm
    "assamese-purity-refresh":          ("routes.cms_sarvam_health:_assamese_purity_refresh_loop",                  60,  False),
    # Per-language grounded-recall — factory pattern, see _per_language_loop.
    "grounded-recall-as":               ("__adapter:lang-as",                                                     1800,  True),
    "grounded-recall-hi":               ("__adapter:lang-hi",                                                     1800,  True),
    "grounded-recall-bn":               ("__adapter:lang-bn",                                                     1800,  True),
}


def _resolve(job_name: str) -> tuple[Callable[[], Awaitable[None]], int, bool, str, bool]:
    """Return (callable, timeout_s, has_boot_stagger, label, is_native_one_shot)."""
    spec = DISPATCH.get(job_name)
    if not spec:
        raise SystemExit(f"Unknown JOB_NAME={job_name!r}; not in services/cron-jobs/run.py DISPATCH")
    target, timeout_s, has_stagger = spec

    if target == "__adapter:internal_linker":
        from seo_internal_linker import _internal_linker_loop  # type: ignore
        return ((lambda: _with_db(_internal_linker_loop)), timeout_s, has_stagger,
                "seo_internal_linker:_internal_linker_loop(db)", False)
    if target == "__adapter:embed_backfill_alert":
        # Task #434 — db-bound alert watcher for the embedding backfill.
        # Same shape as the internal_linker adapter above.
        from aca_jobs.embed_backfill import alert_loop as _ebf_alert_loop  # type: ignore
        return ((lambda: _with_db(_ebf_alert_loop)), timeout_s, has_stagger,
                "aca_jobs.embed_backfill:alert_loop(db)", False)
    if target.startswith("__adapter:lang-"):
        lang = target.split("-", 1)[1]
        return ((lambda: _per_language_loop(lang)), timeout_s, has_stagger,
                f"bench.grounded_recall:per_language_nightly_loops()[{lang!r}]", False)

    mod_name, _, fn_name = target.partition(":")
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, fn_name)
    if not callable(fn):
        raise SystemExit(f"DISPATCH[{job_name}]={target} is not callable")
    once = getattr(mod, f"{fn_name}_run_once", None)
    if callable(once):
        return once, timeout_s, has_stagger, f"{mod_name}:{fn_name}_run_once", True
    return fn, timeout_s, has_stagger, f"{mod_name}:{fn_name}", False


class _ExitAfterFirstIteration(Exception):
    """Sentinel raised inside the patched ``asyncio.sleep`` to break
    out of a legacy ``while True:`` loop after exactly one body run."""


# Sleeps SHORTER than this threshold pass through unchanged — they
# are presumed to be retry/backoff inside the loop body. Sleeps
# at-or-above the threshold are presumed to be boot stagger or the
# inter-iteration wait at the bottom of `while True:`.
ITERATION_SLEEP_THRESHOLD_S = 45


async def _invoke_loop_once(
    fn: Callable[[], Awaitable[None]],
    timeout_s: int,
    has_boot_stagger: bool,
    is_native_one_shot: bool,
) -> None:
    """Run `fn()` for exactly one iteration of the migrated loop.

    Native one-shot path (``is_native_one_shot=True``): just await
    inside ``wait_for``. No sleep patching.

    Sleep-bracketed fallback: see module docstring for the contract.
    """
    if is_native_one_shot:
        await asyncio.wait_for(fn(), timeout=timeout_s)
        return

    real_sleep = asyncio.sleep
    long_sleep_count = 0
    # If has_boot_stagger then we need to skip the first long sleep
    # (the stagger) and exit on the second; otherwise we exit on the
    # first long sleep (the inter-iteration wait).
    exit_at = 2 if has_boot_stagger else 1

    async def _patched_sleep(delay, *args, **kwargs):
        nonlocal long_sleep_count
        if not isinstance(delay, (int, float)) or delay < ITERATION_SLEEP_THRESHOLD_S:
            return await real_sleep(delay, *args, **kwargs)
        long_sleep_count += 1
        if long_sleep_count == exit_at:
            raise _ExitAfterFirstIteration(
                f"exit at long sleep #{long_sleep_count} (delay={delay}s, "
                f"has_boot_stagger={has_boot_stagger})"
            )
        # has_boot_stagger=True and this is the first long sleep
        # (the stagger) — short-circuit it so the body actually runs.
        log.info("[one-shot] short-circuiting boot-stagger sleep(%ss)", delay)
        return await real_sleep(0)

    asyncio.sleep = _patched_sleep  # type: ignore[assignment]
    try:
        try:
            await asyncio.wait_for(fn(), timeout=timeout_s)
        except _ExitAfterFirstIteration as e:
            log.info("loop body completed; %s", e)
        except asyncio.TimeoutError:
            log.warning(
                "hard timeout (%ss) elapsed before loop body finished its first iteration "
                "— either raise replica_timeout_in_seconds in container-apps-jobs.tf or "
                "add an explicit %s_run_once sibling in the legacy module",
                timeout_s, fn.__name__ if hasattr(fn, "__name__") else "<loop>",
            )
            raise
    finally:
        asyncio.sleep = real_sleep  # type: ignore[assignment]


async def _heartbeat(job_name: str, status: str, started_at: float, error: str | None = None) -> None:
    try:
        from cron_heartbeats import record  # type: ignore
        await record(
            job_name=job_name,
            status=status,
            duration_ms=int((time.time() - started_at) * 1000),
            error=error,
        )
    except Exception:  # pragma: no cover
        log.exception("heartbeat write failed for %s (status=%s)", job_name, status)


async def _run() -> int:
    job_name = os.environ.get("JOB_NAME", "").strip()
    job_kind = os.environ.get("JOB_KIND", "loop").strip()
    if not job_name:
        log.error("JOB_NAME env var is required")
        return 2

    log.info("starting cron job name=%s kind=%s", job_name, job_kind)
    started = time.time()

    # Task #333 — observability rewire. Wrap the dispatch in a single
    # OTel root span (``cron.<job_name>``) that fans out to App Insights
    # + Axiom in parallel. Returns ``None`` when the SDK isn't
    # installed (smoke rigs); the surrounding logic is span-agnostic.
    try:
        from observability import configure_otel as _configure_otel  # type: ignore
        _otel_span_cm = _configure_otel(job_name)
    except Exception as _otel_err:
        log.warning("[otel] configure_otel failed (non-fatal): %s", _otel_err)
        _otel_span_cm = None
    if _otel_span_cm is not None:
        _otel_span_cm.__enter__()

    # Wrap the whole body in try/finally so the OTel root span ends
    # cleanly on every return path (config_error, error, ok). We
    # capture exception state explicitly so the OTel context manager
    # receives real (exc_type, exc, tb) on failure paths — App
    # Insights then surfaces the exception as the span's error
    # status + records the stack trace.
    _exc_info: tuple = (None, None, None)
    try:
        aborted = asyncio.Event()

        def _on_sigterm(*_a):
            log.warning("SIGTERM received — marking %s as aborted", job_name)
            aborted.set()

        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, _on_sigterm)

        try:
            fn, timeout_s, has_stagger, target, is_native = _resolve(job_name)
        except SystemExit as e:
            log.error("dispatch failure: %s", e)
            await _heartbeat(job_name, status="config_error", started_at=started, error=str(e))
            return 2

        log.info(
            "dispatching to %s (timeout=%ss, native_one_shot=%s, has_boot_stagger=%s)",
            target, timeout_s, is_native, has_stagger,
        )
        try:
            await _invoke_loop_once(fn, timeout_s, has_stagger, is_native)
        except Exception as e:  # noqa: BLE001
            log.exception("cron job %s raised", job_name)
            status = "aborted" if aborted.is_set() else "error"
            await _heartbeat(job_name, status=status, started_at=started, error=repr(e))
            _exc_info = (type(e), e, e.__traceback__)
            return 1

        await _heartbeat(job_name, status="ok", started_at=started)
        log.info("cron job %s finished ok in %.1fs", job_name, time.time() - started)
        return 0
    except BaseException as _be:
        # Ensure SystemExit / KeyboardInterrupt also propagate exception
        # info into the OTel span before re-raising.
        _exc_info = (type(_be), _be, _be.__traceback__)
        raise
    finally:
        if _otel_span_cm is not None:
            try:
                _otel_span_cm.__exit__(*_exc_info)
            except Exception:  # pragma: no cover
                log.exception("[otel] root span end failed")


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
