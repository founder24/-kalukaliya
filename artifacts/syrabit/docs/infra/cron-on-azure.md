# Cron on Azure — runbook

> ⚠️ **V4 cross-reference (2026-05-06).** The locked source of truth for the
> overall Syrabit architecture is [`infra/v4-locked-architecture.md`](../../../../infra/v4-locked-architecture.md).
> If anything below disagrees with V4, V4 wins. This doc is preserved as the
> operator runbook for cron jobs on Azure (Task #332). Regions, namespaces,
> providers, and failover semantics are governed by V4 (see V4 §0–§11).

**Status:** active (Phase 4 — Task #332)
**Owners:** infra@syrabit.ai
**Pages to:** `azurerm_monitor_action_group.ops_alerts` (see `infra/azure/observability.tf`)

This runbook covers the Azure Container Apps Jobs that replaced:
* the 10 GCP Cloud Scheduler entries catalogued in
  [`docs/infra/inventory/cloud-scheduler.json`](inventory/cloud-scheduler.json),
  and
* the 38 in-process asyncio loops marked `aca-job` in
  [`docs/infra/inventory/asyncio-loops.md`](inventory/asyncio-loops.md).

---

## Topology

```
┌──────────────────────────────────────────┐
│ Container Apps Environment               │
│   syrabit-cron-env                       │
│   - Consumption workload profile         │
│   - Bound to syrabit-cron-jobs-subnet    │
│   - Logs → syrabit-cron-obs-law          │
└─────────────┬────────────────────────────┘
              │ owns ~48 jobs
              ▼
┌──────────────────────────────────────────┐
│ aca-job-<name>     (cron-triggered)      │
│   image  syrabit-cron-jobs:latest        │
│   identity user-assigned managed ident.  │
│   secret  app-insights-conn → Key Vault  │
│   env     JOB_NAME=<name>, JOB_KIND=…    │
│   ENTRYPOINT  python /app/run.py         │
└──────────────────────────────────────────┘
```

`run.py` reads `JOB_NAME`, looks up the matching coroutine in its
`DISPATCH` table, runs it once, exits 0/1/2. The cron expression is
configured per-job in Terraform — there is no internal `while True`
loop inside the container, so a former in-process loop that ran
every 5 minutes now spawns a fresh container every 5 minutes that
runs the body once and exits.

Resources:

| Concern | Terraform file |
| --- | --- |
| Container Apps Environment + 48 jobs | `infra/azure/container-apps-jobs.tf` |
| Per-job failure metric alerts (→ ops_alerts) | `infra/azure/container-apps-jobs.tf` |
| User-assigned managed identity | `infra/azure/iam-github-oidc.tf` (`cron_jobs_runtime`) |
| Container Registry (image source) | `infra/azure/container-registry.tf` |
| Key Vault secret `app-insights-connection-string` | `infra/azure/key-vault.tf` |
| Action group `ops_alerts` (paging) | `infra/azure/observability.tf` |

Image build + entrypoint + dispatch table:
`services/cron-jobs/{Dockerfile, run.py, requirements.txt, README.md}`.

---

## Health surfaces

1. **Per-job metric alert** `aca-job-<name>-failed` — fires when the
   `JobExecutionsFailedCount` metric is > 0 over a 15 min window
   (5 min frequency). Pages `ops_alerts`.
2. **Job run history** — exposed by the Azure Resource Manager API
   per Container App Job. Proxied to the React admin panel via
   `GET /admin/azure/cron/health` so the bundle never holds an ARM
   token.
3. **App Insights traces** — every run emits an OTEL span named
   `cron-<name>` via the connection string mounted from Key Vault.
   Look in Application Insights → Transaction search filtered on
   `cloud_RoleName = cron-<name>`.
4. **AdminHealth → Infrastructure tab → Cron card** — table of all
   jobs sorted "failing first", with a `Scheduler / Loop / Failing`
   filter set.
5. **`cron_heartbeats` Mongo collection** — `run.py` writes one row
   per run with `{job_name, status, duration_ms, error}`; the four
   "named" CronHealthPills (Trustpilot refresh, edge-proxy deploy,
   CF-WAF drift, unified-logs CF pull) read from this collection so
   their tile state is independent of the ARM control plane.

---

## On-call playbook

### Symptom: `aca-job-<name>-failed` alert fires

1. Open the job in the Azure Portal → Container Apps → Jobs →
   `aca-job-<name>` → **Execution history**. The most recent
   failed replica has its log under **Logs**.
2. Cross-reference App Insights → Failures filtered on
   `cloud_RoleName = cron-<name>` for the stack trace.
3. If the failure is transient (downstream 5xx, Mongo blip),
   manually trigger a re-run from the portal: **Start now**. The
   alert auto-resolves on the next successful execution.
4. If the failure is a code bug, roll back the image: re-tag the
   previous SHA in ACR as `:latest` (the job picks up the new image
   on the next scheduled run; force a sooner pickup with **Start
   now** after the re-tag).

### Symptom: AdminHealth Cron card shows a job as "missing last run"

i.e. `lastRunAt` older than ~3× the cron interval. Causes:

1. **Container Apps quota hit** — Consumption profile has a
   subscription-level CPU cap; check Azure Monitor → Container Apps
   Environment → "Cores reserved" metric.
2. **Image pull failure** — managed identity lost ACR pull access.
   Check `azurerm_role_assignment.cron_jobs_acr_pull` is intact.
3. **Cron expression typo** — Container Apps validates this at
   create-time, but a `terraform apply` to an old environment can
   leave a job with an unparseable expression. Use
   `az containerapp job show -n aca-job-<name> -g syrabit-cron-obs-rg`
   to inspect the live config.

### Symptom: a former-loop job runs but never marks `Succeeded`

The loop coroutine probably has its own internal `while True:` left
over from the GCP era. Find it in `services/cron-jobs/run.py`'s
DISPATCH table, follow the `module:callable` to the legacy backend,
and replace the loop with a single iteration. The Container Apps
Jobs schedule is the new outer loop.

### Symptom: Heartbeat row missing for a job that succeeded

`cron_heartbeats` Mongo writes are best-effort (they must not mask a
job failure). If the row is missing but the Azure run history shows
`Succeeded`, check Mongo connectivity from the cron-jobs subnet and
confirm the Cosmos private endpoint is healthy.

---

## Adding a new cron job

See `services/cron-jobs/README.md` → "Adding a new cron job". The
Terraform map (`local.cron_jobs` in `container-apps-jobs.tf`) and
the dispatch table (`DISPATCH` in `run.py`) MUST be updated together
— a CI key-set check fails the build otherwise.

---

## Cost guardrails

* Azure for Startups credit covers ~$2 500 over 12 months;
  Container Apps Jobs on Consumption is billed per-vCPU-second and
  per-GiB-second. Current 48-job set is ~$30/mo.
* `azurerm_consumption_budget_subscription.monthly_cost` (in
  `account-billing.tf`) trips at 50 % / 80 % / 100 % of $200/mo.
* Long-running jobs are the usual cost surprise. Audit any job with
  `replica_timeout_in_seconds > 1200` quarterly to confirm it still
  needs the headroom.

---

## Cutover checklist (one-time, kept for reference)

- [ ] Re-run inventory verification against the live GCP project
      (`docs/infra/inventory/cloud-scheduler.json` `verification.command`).
- [ ] Bake + push `syrabit-cron-jobs:latest` to
      `${ACR}.azurecr.io/syrabit-cron-jobs`.
- [ ] `terraform apply` for `infra/azure/container-apps-jobs.tf`.
- [ ] Confirm one execution per job in the Azure portal (use
      "Start now" if the schedule is hours away).
- [ ] Pause the GCP Cloud Scheduler jobs (do NOT delete yet — keep
      as fallback for one week).
- [ ] Comment out the matching `asyncio.create_task(...)` calls in
      the DO API/backend startup code, behind a `RUN_LEGACY_LOOPS`
      feature flag for instant rollback.
- [ ] Watch the AdminHealth Cron card + the per-job alerts for 7
      days. Any `aca-job-<name>-failed` page during the soak
      requires a triage entry in the post-mortem before deletion.
- [ ] Delete the GCP Cloud Scheduler jobs and remove
      `RUN_LEGACY_LOOPS` from the codebase. Update
      `docs/infra/inventory/asyncio-loops.md` to mark the 38 loops
      as "migrated".
