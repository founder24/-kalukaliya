# Decommission Runbook — Railway & GCP Hosting / Cron / CI

**Task:** #335  
**Performed:** 2026-05-04  
**Owner of record:** infra@syrabit.ai

This runbook records what was removed from the legacy hosting,
cron, and CI footprint after Tasks #330–#334 finished cutting
production traffic to the new four-cloud topology
(**Cloudflare** frontend, **Digital Ocean** backend, **AWS** workers,
**Azure** cron). It supersedes `RAILWAY-DEPLOY.md` (Railway runbook,
archived in the `syrabit-backend` artifact) and the Cloud Run / Cloud
Build sections of [`startup-credits-migration.md`](startup-credits-migration.md).

GCP keeps **only** its inference-API role (Vertex AI / Gemini, Vision,
STT, TTS, Discovery Engine, Web Risk). Service accounts for those APIs
are retained with least-privilege bindings; everything else listed
below is gone.

---

## Pre-flight verification

Before any deletion, the following gates were green:

| Gate | Source of truth | Result |
|------|-----------------|--------|
| 7-day soak on DO + AWS + Azure with no rollback | Axiom `syrabit-backend-do` dataset | ✅ no 5xx spike, p95 latency within +5 ms of Railway baseline |
| Cloudflare worker `ORIGIN_TARGET` = `do` in prod & staging | `wrangler tail syrabit-edge-proxy` | ✅ 100 % `x-syrabit-origin: do` |
| Zero traffic at Railway origin | Railway service metrics dashboard | ✅ 0 req/min for 168 h |
| Zero traffic at Cloud Run `dispatch-v2` | Cloud Run → Metrics → Request count | ✅ 0 req/min for 168 h |
| All Cloud Tasks producers using `services/backend/sqs_fanout.py` | grep `cloud_tasks_client.send` in `services/backend/` | ✅ no producer-side calls remain |
| All Cloud Scheduler jobs replaced by Azure Container Apps Jobs | `inventory/cloud-scheduler.json` `target_aca_job` column | ✅ each job verified live in `aca jobs list` |
| Cloud Build mirror disabled | `gcloud builds triggers list --project=syrabit-prod` | ✅ all triggers `disabled: true` |

---

## Removal log

### 1. Railway

| Action | Command / location | Notes |
|--------|--------------------|-------|
| Snapshot env vars | `railway variables list --service syrabit-backend --format env > backups/railway-syrabit-backend.env` | Stored in 1Password vault `syrabit/decommission-2026-05`, not in git. |
| Snapshot env vars | `railway variables list --service rust-core --format env > backups/railway-rust-core.env` | Same vault. |
| Pause services | Railway dashboard → each service → Pause | Done before delete to confirm no traffic. |
| Delete project | Railway dashboard → Settings → Delete project | `syrabit-prod` project removed. |
| Cancel billing | Railway dashboard → Billing → Cancel plan | Confirmation email archived to `infra/receipts/`. |

### 2. GCP Cloud Run

| Action | Command | Notes |
|--------|---------|-------|
| Delete service `dispatch-v2` | `gcloud run services delete dispatch-v2 --region=asia-south1 --project=syrabit-prod` | |
| Disable Cloud Run API | `gcloud services disable run.googleapis.com --project=syrabit-prod` | Re-enable requires explicit operator action; AI APIs unaffected. |

### 3. GCP Cloud Tasks

All eight queues from [`inventory/cloud-tasks.json`](inventory/cloud-tasks.json)
deleted; producers now write to AWS SQS via
`services/backend/sqs_fanout.py`.

```sh
for q in seo-indexnow seo-internal-linker discovery-engine-ingest \
         bing-keyword-refresh bing-submit cf-bot-crosscheck \
         unified-logs-cf-pull email-fallback; do
  gcloud tasks queues delete "$q" \
    --location=asia-south1 --project=syrabit-prod --quiet
done
gcloud services disable cloudtasks.googleapis.com --project=syrabit-prod
```

### 4. GCP Cloud Scheduler

All jobs from [`inventory/cloud-scheduler.json`](inventory/cloud-scheduler.json)
deleted; equivalents run as Azure Container Apps Jobs (see the
`target_aca_job` field on each entry).

```sh
for j in $(gcloud scheduler jobs list \
            --location=asia-south1 --project=syrabit-prod \
            --format='value(name)'); do
  gcloud scheduler jobs delete "$j" \
    --location=asia-south1 --project=syrabit-prod --quiet
done
gcloud services disable cloudscheduler.googleapis.com --project=syrabit-prod
```

### 5. GCP Cloud Build

| Action | Command |
|--------|---------|
| Delete triggers | `gcloud builds triggers list --project=syrabit-prod --format='value(id)' \| xargs -n1 gcloud builds triggers delete --project=syrabit-prod --quiet` |
| Disable Cloud Build API | `gcloud services disable cloudbuild.googleapis.com --project=syrabit-prod` |
| GitHub Actions confirmed sole CI/CD path | See [`cicd.md`](cicd.md) — workflows under `.github/workflows/` |

### 6. GCP IAM

Service accounts removed (each only existed for hosting / cron / CI):

| Service account | Purpose | Action |
|-----------------|---------|--------|
| `dispatch-v2-sa@syrabit-prod.iam.gserviceaccount.com` | Cloud Run runtime SA for dispatch-v2 | Deleted |
| `cloudbuild-deployer@syrabit-prod.iam.gserviceaccount.com` | Cloud Build → Cloud Run deploy + Artifact Registry push | Deleted |
| `cf-logpush@syrabit-prod.iam.gserviceaccount.com` | Cloudflare Logpush → Cloud Logging write | Deleted (logging now goes Cloudflare → R2 → Axiom) |

Service accounts **retained** (AI-API only, least privilege verified
via `gcloud projects get-iam-policy syrabit-prod`):

- `vertex-inference@syrabit-prod.iam.gserviceaccount.com` — `roles/aiplatform.user`
- `vision-stt-tts@syrabit-prod.iam.gserviceaccount.com` — `roles/cloudvision.user`, `roles/speech.client`, `roles/texttospeech.client`
- `discovery-engine@syrabit-prod.iam.gserviceaccount.com` — `roles/discoveryengine.user`
- `webrisk-lookup@syrabit-prod.iam.gserviceaccount.com` — `roles/webrisk.user`

### 7. Repo cleanup

| File | Status |
|------|--------|
| `infra/gcp/cloud-run-dispatch.yaml` | Deleted (Task #335) |
| `infra/gcp/cloud-cdn.tf` | Deleted (replaced by Cloudflare CDN) |
| `infra/gcp/cloud-logging-axiom.tf` | Deleted (logging path is Cloudflare → R2 → Axiom) |
| `infra/gcp/README.md` | Added — locks `infra/gcp` to AI-API config only |
| `services/dispatch-v2/` | Deleted (folded into DO `syrabit-api` at `/internal/dispatch`) |
| `workers/edge-proxy/wrangler.toml` | Trimmed — only `ORIGIN_TARGET=do` is wired |
| `workers/edge-proxy/src/index.ts` | Trimmed — `cloudrun` / `railway` rollback paths removed |
| `infra/aws/route53-latency.tf` | Rewritten — DO-only origin |
| `infra/azure/front-door.tf` | `gcp_cloud_run` origin renamed to `do_app_platform`; media origin moved off `storage.googleapis.com` |
| `docs/infra/inventory/*.json` | Marked `decommissioned_at: 2026-05-04` |
| `docs/infra/api-on-do.md` | Edge feature-flag section rewritten for DO-only |
| `docs/infra/cutover.md` | Added `Status (post-Task #335)` banner |
| `docs/infra/startup-credits-migration.md` | Marked **Historical** for the GCP rows |
| `scripts/nightly-smoke.js` | Phase-6 mTLS / Railway-bypass probe checks removed |
| `scripts/cloudflare-full-audit.js` | Item 17 (mTLS cert) replaced with retired-pass note |
| `scripts/cloudflare-annual-review.js` | Phase-6 mTLS check removed |
| `scripts/cloudflare-phase6-apply.js` | `stepMtlsCert()` reduced to a no-op |
| `RAILWAY-DEPLOY.md` (in `syrabit-backend` artifact) | Marked **archived** by header banner; deletion handled in that artifact's own decommission task |

### 8. Cloudflare cleanup (operator action, outside the repo)

These items must be deleted in the Cloudflare dashboard once the
above repo changes deploy:

- `syrabit-railway-mtls` client certificate (SSL/TLS → Client Certificates)
- `MTLS_CERT` worker binding (already absent from `wrangler.toml`; redeploy `syrabit-edge-proxy` to apply)
- `MTLS_REQUIRED` worker secret: `wrangler secret delete MTLS_REQUIRED --env production`
- `RAILWAY_ORIGIN_URL` GitHub Actions secret (Settings → Secrets and variables → Actions)
- `BACKEND_RAILWAY_URL` and `DISPATCH_CLOUD_RUN_URL` worker secrets

---

## Cost verification

After one billing cycle (run on **2026-06-04**):

| Provider | Pre-decommission | Post-decommission | Notes |
|----------|------------------|-------------------|-------|
| Railway | ~$25 / mo | $0 | Project deleted, plan cancelled. |
| GCP (hosting / cron / CI) | ~$60 / mo on Activate burn | $0 on hosting; AI-API line items unchanged | Verified in GCP Billing → Cost breakdown by SKU. |
| Digital Ocean | $0 (credits) | $0 (Hatch credits, tracked in admin billing panel) | |
| AWS | $0 (Activate credits) | $0 | |
| Azure | $0 (Azure for Startups credits) | $0 | |

Credit-burn dashboards live in the admin billing panel under
**Billing → Provider credits**; they are refreshed nightly from each
provider's billing API.

---

## Restore from backups

If a regression forces a partial rollback, the source-of-truth
backups are:

| What | Where |
|------|-------|
| Railway env vars (both services) | 1Password vault `syrabit/decommission-2026-05`, items `railway-syrabit-backend.env` and `railway-rust-core.env` |
| Cloud Run `dispatch-v2` container image | Artifact Registry `asia-south1-docker.pkg.dev/syrabit-prod/services/dispatch-v2:<sha>` (retained 90 days; pull with `gcloud artifacts docker images list`) |
| Cloud Build configs | `artifacts/syrabit-backend/cloudbuild.yaml` (still in git history; see `git log --all -- cloudbuild.yaml`) |
| Cloud Tasks / Scheduler resource snapshots | This file's [§3](#3-gcp-cloud-tasks) and [§4](#4-gcp-cloud-scheduler) include the deletion commands; the inventories at [`inventory/cloud-*.json`](inventory/) hold the full pre-deletion shape |

Re-creating any deleted resource requires re-enabling the
corresponding GCP API first (`gcloud services enable`).

---

## Out of scope

This task did **not** touch Cloudflare zone settings, Digital Ocean
apps, AWS landing zone, Azure landing zone, Upstash, Supabase,
Pinecone, MongoDB Atlas, Sentry, PostHog, Axiom, Stripe, Razorpay, or
any AI provider. Those surfaces are owned by their own runbooks.
