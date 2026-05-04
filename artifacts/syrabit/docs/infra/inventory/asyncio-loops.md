# Inventory — In-process `asyncio` background loops on the Python backend

**Captured:** 2026-05-03 (source: `artifacts/syrabit-backend/server.py` lines
1170–1700, plus ripgrep for `asyncio.create_task` across the backend).
**Method:** static enumeration of every `asyncio.create_task(...)` reached
from the FastAPI startup hook in `server.py`. Per-request fire-and-forget
tasks (e.g. `seo_engine.py:_seo_log`, `middleware.py:_bg_track`) are
excluded — they run synchronously to the request and do not need a
separate landing surface.
**Scope:** every loop listed here must be classified as either
`landing=aca-job` (cron-shaped — port to Azure Container Apps Jobs),
`landing=sqs-lambda` (event-shaped — port to AWS SQS + Lambda), or
`landing=do-in-process` (per-replica leader election or per-worker
warm-up — must stay co-located with the API).

| # | Loop                                              | Defined in                                    | Cadence (today)            | Leader-gated | Landing                  |
|--:|---------------------------------------------------|-----------------------------------------------|----------------------------|--------------|--------------------------|
| 1 | `_rate_limiter_cleanup`                           | `deps.py`                                     | every 60 s                 | no           | `do-in-process`          |
| 2 | `_bg_health_loop`                                 | `server.py`                                   | every 30 s                 | no           | `do-in-process`          |
| 3 | `_prewarm_library_cache`                          | `server.py`                                   | once at boot               | no           | `do-in-process`          |
| 4 | `neural_mesh.warm_all`                            | `neural_mesh.py`                              | once at boot               | no           | `do-in-process`          |
| 5 | `health_snapshot_cache.warm_all_probes`           | `health_snapshot_cache.py`                    | once at boot               | no           | `do-in-process`          |
| 6 | `_seed_syllabus_embeddings`                       | `server.py`                                   | once at boot, leader only  | yes          | `aca-job` (one-shot job) |
| 7 | `_load_ga4_from_db`                               | `server.py`                                   | once at boot               | no           | `do-in-process`          |
| 8 | `_exam_reminder_loop`                             | `routes/admin_notifications.py`               | every 5 min                | no           | `aca-job`                |
| 9 | `_push_prune_loop`                                | `routes/admin_notifications.py`               | nightly                    | yes          | `aca-job`                |
|10 | `ensure_synthetic_alerts_ttl_index`               | `routes/admin_notifications.py`               | once at boot, leader only  | yes          | `aca-job` (one-shot)     |
|11 | `_synthetic_alert_cleanup_loop`                   | `routes/admin_notifications.py`               | hourly                     | no           | `aca-job`                |
|12 | `_alerting_loop`                                  | `server.py`                                   | every 60 s                 | no           | `sqs-lambda` (alert fanout) |
|13 | `_cf_access_silent_lockout_loop`                  | `cf_access.py`                                | every 5 min                | yes          | `aca-job`                |
|14 | `_endpoint_health_alert_loop`                     | `routes/bot_discovery.py`                     | every 5 min                | no           | `aca-job`                |
|15 | `_hydrate_alert_loop`                             | `routes/analytics.py`                         | every 10 min               | yes          | `aca-job`                |
|16 | `_review_prompt_alert_loop`                       | `routes/admin_review_prompts.py`              | every 15 min               | yes          | `aca-job`                |
|17 | `_review_prompt_weekly_digest_loop`               | `routes/admin_review_prompts.py`              | weekly (Mon 09:00 IST)     | yes          | `aca-job`                |
|18 | `_sitemap_indexnow_diff_loop`                     | `routes/bot_discovery.py`                     | hourly                     | yes          | `aca-job`                |
|19 | `_bing_submit_daily_loop`                         | `routes/bot_discovery.py`                     | daily                      | yes          | `aca-job`                |
|20 | `_bing_keyword_refresh_loop`                      | `routes/bot_discovery.py`                     | monthly                    | yes          | `aca-job`                |
|21 | `_seo_health_alert_loop`                          | `seo_engine.py`                               | every 15 min               | no           | `aca-job`                |
|22 | `_seo_weekly_digest_loop`                         | `seo_engine.py`                               | weekly                     | no           | `aca-job`                |
|23 | `_entity_seo_loop`                                | `entity_seo_health.py`                        | weekly                     | mongo-lease  | `aca-job`                |
|24 | `_topic_discovery_loop`                           | `topic_discovery_service.py`                  | nightly                    | yes          | `aca-job`                |
|25 | `_seo_remediation_loop`                           | `seo_remediation_service.py`                  | every 30 s drain           | yes          | `sqs-lambda`             |
|26 | `_internal_linker_loop`                           | `seo_internal_linker.py`                      | nightly                    | mongo-lease  | `aca-job`                |
|27 | `_grounded_recall_nightly_loop`                   | `bench/grounded_recall.py`                    | nightly                    | mongo-lease  | `aca-job`                |
|28 | `per_language_nightly_loops` (×3 — as / hi / bn)  | `bench/grounded_recall.py`                    | nightly                    | mongo-lease  | `aca-job`                |
|29 | `_seo_auto_publish_loop`                          | `seo_engine.py`                               | every 15 min               | mongo-lease  | `aca-job`                |
|30 | `_seo_auto_publish_staleness_loop`                | `seo_engine.py`                               | hourly                     | mongo-lease  | `aca-job`                |
|31 | `_seo_staleness_heartbeat_loop`                   | `seo_engine.py`                               | every 6 h                  | mongo-lease  | `aca-job`                |
|32 | `_ci_alert_loop`                                  | `routes/admin_ci_alerts.py`                   | every 10 min               | mongo-lease  | `aca-job`                |
|33 | `_trustpilot_feed_alert_loop`                     | `routes/admin_trustpilot_alerts.py`           | hourly                     | no           | `aca-job`                |
|34 | `_trustpilot_refresh_cron_alert_loop`             | `routes/admin_trustpilot_cron_alerts.py`      | hourly                     | mongo-lease  | `aca-job`                |
|35 | `_cf_waf_drift_cron_alert_loop`                   | `routes/admin_cf_waf_drift_cron_alerts.py`    | hourly                     | mongo-lease  | `aca-job`                |
|36 | `_cf_pull_silence_alert_loop`                     | `routes/admin_logs_cf_pull_silence_alerts.py` | every 10 min               | mongo-lease  | `aca-job`                |
|37 | `_edge_proxy_deploy_cron_alert_loop`              | `routes/admin_edge_proxy_deploy_cron_alerts.py`| hourly                    | mongo-lease  | `aca-job`                |
|38 | `_slack_webhook_missing_alert_loop`               | `routes/admin_slack_webhook_missing_alerts.py`| every 6 h                  | mongo-lease  | `aca-job`                |
|39 | `_cf_bot_report_loop`                             | `cf_bot_report.py`                            | every 5 min                | mongo-lease  | `aca-job`                |
|40 | `pages_deploy.nightly_loop`                       | `pages_deploy.py`                             | nightly                    | mongo-lease  | `aca-job`                |
|41 | `_bot_traffic_report_loop`                        | `cf_bot_report.py`                            | every 15 min               | mongo-cas    | `aca-job`                |
|42 | `_init_blocked_ip_cache`                          | `middleware.py`                               | once at boot               | no           | `do-in-process`          |
|43 | `_collection_size_snapshot_loop`                  | `routes/admin_advanced.py`                    | hourly                     | no           | `aca-job`                |
|44 | `_cache_warm_loop`                                | `routes/admin_advanced.py`                    | every 6 h                  | mongo-lease  | `aca-job`                |
|45 | `chat_speedup_metrics.periodic_flush_loop`        | `chat_speedup_metrics.py`                     | every 60 s, per-worker     | no           | `do-in-process` (per-worker) |
|46 | `_assamese_purity_refresh_loop`                   | `routes/cms_sarvam_health.py`                 | every 15 s, per-worker     | no           | `do-in-process` (per-worker) |
|47 | `ensure_assamese_runs_index`                      | `routes/cms_sarvam_health.py`                 | once at boot               | no           | `do-in-process`          |
|48 | `ensure_assamese_audit_index`                     | `routes/cms_sarvam_health.py`                 | once at boot               | no           | `do-in-process`          |
|49 | `ensure_trustpilot_jsonld_runs_index`             | `routes/admin_trustpilot_jsonld_status.py`    | once at boot               | no           | `do-in-process`          |
|50 | `_vertex_startup_probe`                           | `server.py:350` (scheduled at `server.py:1633`) | once at boot             | no           | `aca-job` (one-shot)     |
|51 | `_vertex_periodic_probe_loop`                     | `server.py:520` (scheduled at `server.py:1641`) | every 5 min              | no           | `aca-job`                |
|52 | `_unified_logs_cf_pull_loop`                      | `unified_logs_dao.py`                         | every 60 s                 | mongo-lease  | `aca-job`                |
|53 | `unified_logs_dao._BatchedWriter._run`            | `unified_logs_dao.py`                         | continuous, per-worker     | no           | `do-in-process` (per-worker) |
|54 | `wai_chapter_index._bg_build` / `_persist_to_redis`| `wai_chapter_index.py`                       | per-subject, on demand     | no           | `do-in-process` (per-request fanout) |
|55 | `vectorize_client._send_alert_async`              | `vectorize_client.py`                         | per-error, fire-and-forget | no           | `sqs-lambda`             |
|56 | `metrics._dispatch_push_to_admins`                | `metrics.py`                                  | per-event, fire-and-forget | no           | `sqs-lambda`             |

## Landing summary

- **`aca-job` (cron-shaped → Azure Container Apps Jobs):** 38 loops.
- **`sqs-lambda` (event-shaped → AWS SQS + Lambda):** 4 loops.
- **`do-in-process` (must stay on DO with the API):** 14 loops (boot
  warmups, per-replica leases, per-worker caches, per-request fanouts).

The ADR's Phase 4 DoD ("zero in-process background loops outside
`background_lease.py`") is updated to read: "the only `asyncio.create_task`
loops surviving on DO are the 14 entries marked `do-in-process` above."
