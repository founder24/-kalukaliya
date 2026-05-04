# ADR-0001 — Four-Way Hosting Rebalance (Cloudflare / DO / AWS / Azure)

**Status:** Accepted (planning only — no workloads moved by this ADR)
**Last updated:** 2026-05-03
**Owner:** infra
**Task:** #327
**Supersedes:** #336 (standalone Railway → DO move; folded into Phase 3 below)
**Companion docs:**
[`provider-credit-matrix.md`](provider-credit-matrix.md),
[`startup-credits-migration.md`](startup-credits-migration.md),
[`../CLOUDFLARE_OBSERVATORY.md`](../CLOUDFLARE_OBSERVATORY.md)

---

## 1. Context

Today the application is spread unevenly:

- **Railway** runs the Python FastAPI backend and the Rust core (single
  point of cost, single point of failure, no free credit balance left).
- **GCP** runs Cloud Run (`dispatch-v2`), Cloud Build (CI), Cloud Tasks
  (async fan-out), Cloud Scheduler (cron), Cloud CDN, Cloud Logging — and
  *also* Vertex AI / Vision / STT / TTS / Discovery Engine / Web Risk.
  All sit on a single **$2 000** Google for Startups grant.
- **AWS** is used only for Bedrock (vision / safety) and the recently
  ported `email-worker` + `bedrock-proxy` Lambdas. **$1 000** Activate
  balance is largely untouched.
- **Azure** is used only for Azure OpenAI (fallback) + Cosmos DB cache +
  Front Door. **$2 500** Azure for Startups balance is largely untouched.
- **Cloudflare** runs Pages (frontend), edge Workers, R2, D1, AI Gateway,
  WAF, Turnstile, Zero Trust — Enterprise zone is paid annually and is
  not in scope for cost rebalancing.
- **Upstash Redis** — serverless, provider-neutral, stays as-is.

The single-grant exposure on GCP and the single-host exposure on Railway
are the two largest infra risks today. We also want to stop spending
GCP credits on hosting / CI / cron so the **$2 000 GCP balance is reserved
exclusively for Vertex AI inference**, which is the highest-value use of
that grant.

## 2. Decision

Adopt a four-way hosting split with GCP demoted to AI-only:

| Surface                                | Target home   | Why                                                              |
|----------------------------------------|--------------|------------------------------------------------------------------|
| Frontend (`syrabit.ai`), edge logic, R2, D1, AI Gateway, WAF, Turnstile, Zero Trust | **Cloudflare** | Already there; Enterprise zone covers it; no other provider matches edge cache + WAF in one place. |
| Sync API (FastAPI) + Rust core (gRPC + HTTP) | **Digital Ocean App Platform** | DO Hatch + $200 trial; App Platform supports HTTP/2 **and** gRPC, so the Rust core does not need a Droplet; single-platform deploy keeps ops simple. |
| Async workers, durable fan-out queues, transactional email fallback | **AWS** | SQS + Lambda + SES is the most mature credit-backed combo; $1 000 Activate is currently underspent; SES dedicated IP gives Resend a real fallback. |
| Scheduled / cron jobs + unified observability sink | **Azure** | Container Apps Jobs is the cleanest cron primitive on Azure; App Insights + Log Analytics absorbs Cloudflare Logpush and DO/AWS metrics in one place; **$2 500** balance is the largest unspent grant. |
| AI APIs only — Vertex AI, Vision, STT, TTS, Discovery Engine, Web Risk | **GCP** | Vertex is the dispatcher's primary on multiple feature keys (see credit matrix §1). No hosting, no cron, no CI on GCP after this rebalance. |
| L2 cache, atomic rate-limiting, 429 burst counter | **Upstash Redis** (unchanged) | Serverless, provider-neutral; no migration cost; already used by the dispatcher. |
| Supabase, Pinecone, MongoDB Atlas, Sentry, PostHog, Axiom, Resend, Stripe, Razorpay, Sarvam, Cohere, Voyage, Exa, Tavily, ElevenLabs, Deepgram, AssemblyAI | **Unchanged** | All managed SaaS with their own credit programmes; not part of this rebalance. |

### 2.1 Decommissions

- **Railway** — entire account decommissioned at the end of Phase 7.
- **GCP Cloud Run (`dispatch-v2`)** — replaced by DO App Platform service.
- **GCP Cloud Build** — replaced by GitHub Actions (already used for the
  Rust core); DO App Platform builds the Python container directly from
  the GitHub source.
- **GCP Cloud Tasks** — every queue ported to **AWS SQS**.
- **GCP Cloud Scheduler** — every job ported to **Azure Container Apps
  Jobs** (cron expression preserved verbatim).
- **GCP Cloud Logging** as the central sink — replaced by **Azure App
  Insights** + Log Analytics (Cloudflare Logpush + DO + AWS CloudWatch
  all forward there). Cloud Logging stays enabled at the project level
  only because Vertex emits service logs there; we do not query it.

### 2.2 Explicitly out of scope for this ADR

Provisioning, code changes, or deploys; replacing Upstash, Cloudflare,
Supabase, Pinecone, or any AI provider; frontend changes.

## 3. Responsibility boundaries

```
                ┌────────────────── Cloudflare (edge) ──────────────────┐
                │  Pages • Workers • R2 • D1 • AI Gateway • WAF         │
                │  Turnstile • Zero Trust • Logpush                     │
                └──────┬───────────────────────────┬────────────────────┘
                       │ HTTPS                     │ Logpush
                       ▼                           ▼
   ┌─────────── Digital Ocean App Platform ───────────┐    ┌──────── Azure ────────┐
   │  syrabit-api  (Python FastAPI, HTTP/2)            │    │  Container Apps Jobs  │
   │  syrabit-core (Rust, gRPC + HTTP)                 │◄───┤   (every cron job)    │
   └──────┬─────────────────────┬──────────────────────┘    │  Application Insights │
          │ enqueue              │ AI inference              │  Log Analytics (sink) │
          ▼                      ▼                           └──────────┬────────────┘
   ┌──────── AWS ────────┐    ┌──── GCP (AI only) ────┐                 │
   │  SQS  → Lambda      │    │  Vertex AI            │                 │
   │  SES (email fallbk) │    │  Vision / STT / TTS   │                 │
   │  CloudWatch alarms  │    │  Discovery Engine     │ ─── metrics ──► │
   └──────────┬──────────┘    │  Web Risk             │                 │
              │ alarms        └───────────────────────┘                 │
              └──────────────────── alarms ───────────────────────────► │
                                                                        ▼
                                                            (Slack #infra-alerts)
```

Boundary rules (enforced in code review):

1. **Sync request path** stays on DO. A DO service may call AWS SQS,
   Vertex (via CF AI Gateway), Azure App Insights, or Cloudflare R2 / D1
   on the request path. It must **not** call Cloud Run, Cloud Tasks, or
   Cloud Scheduler.
2. **Async work** is enqueued to AWS SQS. Any Python `asyncio.create_task`
   that survives the request lifetime must be migrated to an SQS-backed
   Lambda before Phase 5 closes.
3. **Cron** lives in Azure Container Apps Jobs only. Any `apscheduler`
   loop or `@app.on_event("startup")` background task that runs on a
   schedule must be migrated before Phase 5 closes.
4. **Observability** has one sink: Azure Log Analytics. Sentry stays for
   error tracking; PostHog stays for product analytics; Axiom stays for
   ad-hoc log explorer. App Insights is the *primary* metrics + alert
   pane.
5. **AI calls** always go through Cloudflare AI Gateway, which fronts
   Vertex / Workers AI / Bedrock / Azure OpenAI. No direct provider SDK
   calls from DO or Lambda — the gateway is mandatory so caching, cost
   metering, and fallbacks all stay centralised.

## 4. Traffic & dependency map

The §4.1–§4.5 tables below are the **finalised** Phase-1 inventory.
The full machine-readable enumeration lives at:

- `docs/infra/inventory/railway.json` — every Railway service, port,
  secret, and external dependency.
- `docs/infra/inventory/cloud-run.json` — every Cloud Run service.
- `docs/infra/inventory/cloud-tasks.json` — every Cloud Tasks queue with
  producer file, consumer route, target SQS queue + Lambda + DLQ.
- `docs/infra/inventory/cloud-scheduler.json` — every Cloud Scheduler
  job with cron expression, timezone, target ACA Job.
- `docs/infra/inventory/cloud-build.json` — every Cloud Build pipeline.
- `docs/infra/inventory/asyncio-loops.md` — all 56 in-process
  `asyncio.create_task` loops with their cadence, leader-gating, and
  landing classification (`aca-job` / `sqs-lambda` / `do-in-process`).

The summary tables below are kept inline for narrative readability;
the JSON / Markdown files above are the source of truth and any drift
between them and these tables must be resolved in favour of the file.

### 4.1 Sync HTTP / gRPC entry points (today → target)

| Entry point                         | Today                | Target           |
|-------------------------------------|----------------------|------------------|
| `https://syrabit.ai/*` (frontend)   | Cloudflare Pages     | Cloudflare Pages (unchanged) |
| `https://api.syrabit.ai/*`          | Railway              | DO App Platform `syrabit-api` |
| `https://api.syrabit.ai/api/ai/bedrock/*` | CF Worker → AWS Lambda | Unchanged (already AWS) |
| `https://api.syrabit.ai/webhooks/ses-sns/*` | CF Worker → backend | Unchanged (CF Worker → DO API) |
| `dispatch.syrabit.ai/*`             | CF Worker → GCP Cloud Run | CF Worker → DO `syrabit-api` `/internal/dispatch` |
| Rust core gRPC (internal)            | Railway              | DO App Platform `syrabit-core` (HTTP/2 + gRPC) |

### 4.2 Async / fan-out queues (today GCP Cloud Tasks → AWS SQS)

Source of truth: [`inventory/cloud-tasks.json`](inventory/cloud-tasks.json).
Eight queues, each with a target SQS queue, Lambda consumer, and DLQ:

| Queue (Cloud Tasks)              | Producer                    | Consumer URL                | Target SQS queue                |
|----------------------------------|-----------------------------|-----------------------------|---------------------------------|
| `seo-indexnow`                   | `seo_engine.py`             | `/internal/seo/indexnow`    | `syrabit-seo-indexnow`          |
| `seo-internal-linker`            | `seo_internal_linker.py`    | `/internal/seo/relink`      | `syrabit-seo-internal-linker`   |
| `discovery-engine-ingest`        | `discovery_engine_ingest.py`| `/internal/dei/ingest`      | `syrabit-discovery-ingest`      |
| `bing-keyword-refresh`           | `bing_keyword_client.py`    | `/internal/bing/keyword`    | `syrabit-bing-keyword`          |
| `bing-submit`                    | `bing_submit_client.py`     | `/internal/bing/submit`     | `syrabit-bing-submit`           |
| `cf-bot-crosscheck`              | `cf_bot_crosscheck.py`      | `/internal/cf/bot-recheck`  | `syrabit-cf-bot-crosscheck`     |
| `unified-logs-cf-pull`           | `unified_logs_dao.py`       | `/internal/logs/pull`       | `syrabit-unified-logs-pull`     |
| `email-fallback`                 | `notify.py` (Resend → SES)  | `https://<lambda-url>`      | `syrabit-email-fallback`        |

> **Verification at the start of Phase 4.** Re-run
> `cloud_tasks_client.list_queues()` against the live project and diff
> against `inventory/cloud-tasks.json`. Any queue present in the live
> response that is not in the JSON is a finding and must be added
> before producers are switched.

### 4.3 Cron jobs (today GCP Cloud Scheduler → Azure Container Apps Jobs)

Source of truth: [`inventory/cloud-scheduler.json`](inventory/cloud-scheduler.json).
Ten jobs, each mapped to its target ACA Job with the cron preserved verbatim:

| Job (Cloud Scheduler)            | Cron                | Target                          | Notes                                   |
|----------------------------------|---------------------|---------------------------------|-----------------------------------------|
| `seo-auto-publish`               | `*/15 * * * *`      | `aca-job-seo-auto-publish`      | Hits `/internal/seo/auto-publish`       |
| `seo-publish-indexnow`           | `*/30 * * * *`      | `aca-job-seo-indexnow`          | Folds into `seo-indexnow` SQS pipeline  |
| `trustpilot-refresh`             | `0 */6 * * *`       | `aca-job-trustpilot-refresh`    | Heartbeat must reach App Insights       |
| `vertex-startup-probe`           | `*/5 * * * *`       | `aca-job-vertex-probe`          | AI-only; still calls GCP Vertex via CF AIG |
| `nightly-smoke`                  | `0 2 * * *`         | `aca-job-nightly-smoke`         | Existing GitHub Actions kept as backup  |
| `cf-bot-report`                  | `0 9 * * *`         | `aca-job-cf-bot-report`         | Reads CF Logs via Logpush               |
| `db-cleanup`                     | `0 3 * * *`         | `aca-job-db-cleanup`            | Atlas + Supabase prune                  |
| `bing-webmaster-refresh`         | `0 */4 * * *`       | `aca-job-bing-refresh`          | -                                       |
| `entity-seo-health`              | `0 1 * * *`         | `aca-job-entity-seo-health`     | -                                       |
| `cliffhanger-engine-refresh`     | `*/10 * * * *`      | `aca-job-cliffhanger-refresh`   | -                                       |

### 4.4 In-process `asyncio` background loops

Source of truth: [`inventory/asyncio-loops.md`](inventory/asyncio-loops.md).
**56 loops total**, classified as:

- **38 `aca-job`** (cron-shaped) → port to Azure Container Apps Jobs in
  Phase 4. Includes every periodic alerter (`_seo_health_alert_loop`,
  `_trustpilot_*_alert_loop`, `_cf_*_alert_loop`, `_ci_alert_loop`,
  `_slack_webhook_missing_alert_loop`, etc.), every nightly bench
  (`_grounded_recall_nightly_loop` + 3 per-language siblings), every
  hourly heartbeat (`_seo_staleness_heartbeat_loop`,
  `_collection_size_snapshot_loop`), and the ten "monthly /
  weekly / daily" digest loops.
- **4 `sqs-lambda`** (event-shaped) → port to AWS SQS + Lambda:
  `_alerting_loop`, `_seo_remediation_loop`,
  `vectorize_client._send_alert_async`, `metrics._dispatch_push_to_admins`.
- **14 `do-in-process`** (must stay co-located with the API): boot
  warm-ups (`_prewarm_library_cache`, `neural_mesh.warm_all`,
  `health_snapshot_cache.warm_all_probes`), per-replica leases
  (`background_lease.py`, `_rate_limiter_cleanup`,
  `_assamese_purity_refresh_loop`), per-worker caches
  (`chat_speedup_metrics.periodic_flush_loop`,
  `unified_logs_dao._BatchedWriter._run`), and per-request fanouts
  (`wai_chapter_index._bg_build`).

The Phase 4 DoD reads accordingly: "the only `asyncio.create_task`
loops surviving on DO are the 14 entries marked `do-in-process` in
`inventory/asyncio-loops.md`."

### 4.5 CI / build pipelines

| Pipeline               | Today              | Target                              |
|------------------------|--------------------|-------------------------------------|
| Frontend Pages build   | Cloudflare Pages   | Cloudflare Pages (unchanged)        |
| Python backend build   | GCP Cloud Build    | DO App Platform native build (from GitHub source) |
| Rust core build        | GitHub Actions + Cloud Build | GitHub Actions only; image pushed to DO Container Registry |
| Lambda images          | Local + GitHub Actions | GitHub Actions → ECR (unchanged)  |
| ACA Job images         | n/a                | GitHub Actions → ACR                |
| Worker deploys (CF)    | Wrangler from GHA  | Unchanged                           |

## 5. Cutover sequence

Each phase has a **Definition of Done** that the next phase blocks on.
No phase touches production traffic until its DoD is met.

### Phase 0 — This ADR (no workload moved)
- DoD: ADR committed and approved; inventory scripts queued for Phase 1.

### Phase 1 — Inventory & landing zones (parallelisable)
- **1a. Backend surface inventory.** ✅ **Delivered with this ADR** at
  `docs/infra/inventory/{railway.json, cloud-run.json, cloud-tasks.json,
  cloud-scheduler.json, cloud-build.json, asyncio-loops.md}`. Phase 4
  re-verifies the Cloud Tasks / Cloud Scheduler JSONs against the live
  GCP API before producers are switched (see §4.2 / §4.3 verification
  notes).
- **1b. AWS landing zone** *(downstream task: "Stand up the AWS landing
  zone for async workers, queues and SES")*. Account hardening, OIDC
  trust to GitHub Actions, baseline VPC, ECR repos, SES domain identity,
  CloudWatch destination for cross-account alarms.
- **1c. Azure landing zone** *(downstream task: "Stand up the Azure
  landing zone for cron jobs and observability sink")*. Subscription,
  resource group, Container Apps environment, ACR, App Insights
  workspace, Log Analytics workspace, Logpush ingestion endpoint.
- **1d. DO landing zone.** DO team, App Platform project, Container
  Registry, Spaces (object storage for build artefacts), DO Hatch credit
  application linked.
- DoD: every secret needed by Phase 2 is present in the new providers'
  secret stores; inventory JSON committed; landing-zone Terraform
  applied; smoke probes return 200 from each provider's health surface.

### Phase 2 — CI/CD rewire
- Add GitHub Actions workflows: `do-deploy-api.yml`,
  `do-deploy-core.yml`, `aws-deploy-workers.yml`, `azure-deploy-jobs.yml`.
- Cloud Build pipelines stay running but stop being the source of truth
  — DO App Platform builds from `master` directly and is gated by GHA.
- DoD: a no-op commit produces successful deploys to staging on DO, AWS
  (Lambda alias `staging`), and Azure (`*-staging` jobs). Cloudflare
  Pages / Workers stay on their existing pipelines.

### Phase 3 — DO app port (absorbs Task #336)
- Build the Python FastAPI image; deploy as DO App Platform service
  `syrabit-api` (HTTP/2 enabled, internal port 8000, scale 2–6).
- Build the Rust core image; deploy as DO App Platform service
  `syrabit-core` (HTTP/2 enabled, gRPC route exposed). **Risk gate:**
  validate gRPC with `grpcurl` over the App Platform public hostname.
  If gRPC fails, fall back to the HTTP/JSON shim already in the Rust
  core (the shim covers every method except streaming RPCs — the four
  streaming RPCs would temporarily degrade to long-poll until DO ships
  bidi gRPC GA or we move the core to a Droplet).
- Wire `dispatch.syrabit.ai` Cloudflare Worker to call DO instead of
  Cloud Run via dual-write (10% canary, then 50%, then 100%). Cloud Run
  stays warm as rollback for 7 days.
- DoD: `nightly-smoke.js` green against `api-do.syrabit.ai`; p95
  request latency within 20% of Railway baseline; gRPC p95 within 30%
  of the on-Railway baseline.

### Phase 4 — AWS workers + Azure cron port
- For each Cloud Tasks queue: create the SQS queue + Lambda consumer +
  DLQ; switch the producer to dual-publish (Cloud Tasks **and** SQS) for
  48 h; then flip the consumer over and stop dual-publishing. Drain the
  Cloud Tasks queue to empty before deleting it.
- For each Cloud Scheduler job: create the ACA Job with the same cron
  expression; pause the Cloud Scheduler job; verify the ACA Job ran on
  its next tick and produced the expected side-effect; delete the Cloud
  Scheduler job after 7 days of clean runs.
- Migrate every in-process `asyncio` background loop classified in §4.4
  to either an SQS Lambda or an ACA Job. The DO API ships with those
  loops disabled by feature flag (`BACKGROUND_LOOPS_DISABLED=1`) once
  the corresponding ACA/SQS replacement is green.
- DoD: zero Cloud Tasks queues, zero Cloud Scheduler jobs, and the only
  `asyncio.create_task` loops surviving on DO are the **14
  `do-in-process` entries** in `inventory/asyncio-loops.md` (boot
  warm-ups, per-replica leases incl. `background_lease.py`, per-worker
  caches, per-request fanouts). Heartbeat metrics for every cron job
  visible in App Insights.

### Phase 5 — Observability rewire
- Cloudflare Logpush → Azure Log Analytics ingestion endpoint (replaces
  the existing GCP Cloud Logging sink). Axiom ingest stays as a parallel
  destination for ad-hoc query.
- DO + AWS + ACA → App Insights (OTEL collector running as a sidecar in
  each DO service; CloudWatch metric stream → App Insights; ACA
  diagnostic settings → Log Analytics).
- Existing Cloud Monitoring alert policies in
  `infra/gcp/cloud-logging-axiom.tf` are deprecated; equivalent App
  Insights metric alerts are created with the same thresholds and the
  same Slack channel as notification target.
- DoD: every alert that fires in GCP Cloud Monitoring today fires in App
  Insights with the same threshold; Slack `#infra-alerts` receives the
  alert from App Insights only (the GCP path is silenced for 7 days
  before being deleted).

### Phase 6 — Edge / DNS cutover
- Update `api.syrabit.ai` DNS at Cloudflare from Railway origin → DO
  origin via a weighted CNAME (10% → 50% → 100% over 72 h). Route 53
  latency records (`infra/aws/route53-latency.tf`) are updated to point
  the GCP-region record at DO instead.
- Lower TTL to 60 s 24 h before the cutover; raise back to 300 s after
  100% traffic on DO is stable for 24 h.
- DoD: `dig +short api.syrabit.ai` returns the DO origin from every
  region; CF Pages still resolves; no 5xx spike in App Insights for 24 h.

### Phase 7 — Decommission
- **Railway:** delete services, delete project, cancel subscription.
  Snapshot env vars to 1Password before deletion.
- **GCP hosting:** delete `dispatch-v2` Cloud Run service, delete every
  Cloud Tasks queue, delete every Cloud Scheduler job, delete Cloud Build
  triggers, delete the Pub/Sub log-export topic. Keep `aiplatform`,
  `vision`, `speech`, `texttospeech`, `discoveryengine`, `webrisk` APIs
  enabled. Disable `run`, `cloudbuild`, `cloudtasks`, `cloudscheduler`,
  `pubsub` APIs at the project level.
- **Cloudflare:** decommission unused Workers (`dispatch-v2` shim once
  the route points at DO directly).
- DoD: GCP project shows zero non-AI service charges for one full
  billing day; Railway invoice shows $0; DO + AWS + Azure invoices match
  the projection in §7 within ±15%.

## 6. Risk register & rollback

| ID  | Risk                                                | Phase | Likelihood | Impact | Mitigation                                                      | Rollback                                                  |
|----:|-----------------------------------------------------|-------|------------|--------|----------------------------------------------------------------|-----------------------------------------------------------|
| R1  | DO App Platform gRPC quirks for the Rust core       | 3     | Medium     | High   | `grpcurl` validation gate; HTTP/JSON shim already shipped       | Move Rust core to DO Droplet (manifest pre-prepared)      |
| R2  | In-flight Cloud Tasks lost during queue cutover     | 4     | Medium     | High   | 48-h dual-publish window; drain Cloud Tasks to empty            | Re-enable Cloud Tasks consumer on DO API; reverse SQS     |
| R3  | Cron job missed during scheduler swap               | 4     | Low        | Medium | ACA Job runs once before Cloud Scheduler is paused              | Resume Cloud Scheduler job (it is paused, not deleted)    |
| R4  | DNS TTL outlives rollback window                    | 6     | Medium     | High   | TTL pre-lowered to 60 s 24 h before cutover                     | Flip CNAME back to Railway; wait one TTL cycle            |
| R5  | Secret rotation mismatch (DO has new value, GCP old)| 2–3   | Medium     | High   | Doppler / 1Password sync runs nightly; pre-cutover diff job     | Re-deploy with previous secret bundle from 1Password      |
| R6  | Cost overrun on AWS Lambda after fan-out            | 4     | Low        | Medium | CloudWatch + Cost Anomaly alarms at $50/day per service         | Scale Lambda concurrency cap to 0; failover to in-process |
| R7  | App Insights ingest delay hides outage              | 5     | Low        | High   | Sentry + Axiom retained as fallback paging path                 | Re-enable GCP Cloud Monitoring alert policy (kept silent for 7 d) |
| R8  | DO Hatch credit denied / smaller than expected      | 1d    | Low        | Medium | Provision under $200 trial credits first; switch on Hatch later | Stay on $200 trial + paid until next credit cycle         |
| R9  | Vertex API outage with no GCP hosting fallback      | 7     | Low        | Medium | CF AI Gateway already failovers to Workers AI / Azure OpenAI    | Provider chain in `llm.py` already covers this            |
| R10 | Logpush job stuck on old GCP destination            | 5     | Medium     | Low    | Add the Azure destination *before* removing the GCP one         | Re-attach the GCP destination (kept for 7 days)           |
| R11 | Background loops re-enabled accidentally on DO      | 4     | Low        | High   | Boot-time assertion: refuses to start loops when corresponding ACA Job is registered | Toggle `BACKGROUND_LOOPS_DISABLED=1`; redeploy            |

## 7. Credit & cost projection

All numbers are **monthly** USD, projected at current traffic. Credits
balances are point-in-time (re-check before each phase). Operator must
update `provider-credit-matrix.md` § 1 when remaining balances change.

| Provider     | Today (paid) | Today (credit-burn) | Projected paid | Projected credit-burn | Credit balance | Months runway* |
|--------------|-------------:|--------------------:|---------------:|----------------------:|---------------:|---------------:|
| Railway      |        ~$45  |                  $0 |             $0 |                    $0 |             $0 |          —     |
| GCP (hosting)|         $0   |                ~$60 |             $0 |                    $0 |     **$2 000** |    *AI-only; ~12* |
| GCP (AI)     |         $0   |                ~$40 |             $0 |                  ~$80 |  *(same pool)* |    *~10*       |
| AWS          |         $0   |                ~$15 |             $0 |                  ~$80 |     **$1 000** |    ~12         |
| Azure        |         $0   |                ~$10 |             $0 |                  ~$70 |     **$2 500** |    ~36         |
| DO           |         $0   |                  $0 |            $20 |             ~$30 trial |  $200 trial + Hatch (TBD) | depends on Hatch |
| Cloudflare   |        ~$50† |                  $0 |             $0‡|                    $0 |     Enterprise |    annual      |
| Upstash      |         $0   |                  ~$5 |             $0 |                   ~$5 |  startup tier  |    n/a         |
| **Total**    |       **~$95** |                **~$130** |          **~$20**|              **~$265** |                |                |

\* Runway = remaining credit ÷ projected monthly credit-burn, capped at
12 months.
† Cloudflare paid add-ons are already being decommissioned by Task #263
(see `startup-credits-migration.md`); the projection assumes that work
finishes before Phase 7 of this ADR.
‡ Cloudflare Enterprise zone fee is annual and excluded from the
monthly figure.

### 7.1 Credit expiry table

Captured **2026-05-03**. All grants follow the standard programme
window (12 months from acceptance for AWS Activate Founders, 12 months
for Azure for Startups, 12 months for Google for Startups Tier 1, 12
months for vendor startup credits). Acceptance dates are sourced from
`startup-credits-migration.md` (which records when each programme was
activated against the Syrabit account).

| Provider / programme              | Grant USD | Activated   | Standard window | Expiry      | Months remaining (from 2026-05-03) | Source of truth                                          |
|-----------------------------------|----------:|-------------|-----------------|-------------|-----------------------------------:|----------------------------------------------------------|
| Google Cloud for Startups (GCP)   |    $2 000 | 2025-11-15  | 12 months       | 2026-11-15  |                              ~6.5 | `provider-credit-matrix.md` §1, Cloud Billing console    |
| AWS Activate (Founders)           |    $1 000 | 2025-09-01  | 12 months       | 2026-09-01  |                              ~4.0 | AWS Activate console (account `926046660612`)            |
| Azure for Startups                |    $2 500 | 2025-12-01  | 12 months       | 2026-12-01  |                              ~7.0 | Azure Cost Management (`AZURE_SUBSCRIPTION_ID`)          |
| Sarvam Startup Credits            |      $500 | 2025-10-01  | 12 months       | 2026-10-01  |                              ~5.0 | Sarvam dashboard                                         |
| Deepgram Startup Credits          |      $500 | 2025-10-01  | 12 months       | 2026-10-01  |                              ~5.0 | Deepgram dashboard                                       |
| ElevenLabs Startup Credits        |      $500 | 2025-10-01  | 12 months       | 2026-10-01  |                              ~5.0 | ElevenLabs workspace                                     |
| AssemblyAI Startup Credits        |    $1 000 | 2025-10-01  | 12 months       | 2026-10-01  |                              ~5.0 | AssemblyAI org                                           |
| Cohere Startup Credits            |    $1 000 | 2025-10-01  | 12 months       | 2026-10-01  |                              ~5.0 | Cohere org                                               |
| Pinecone Startup Credits          |      $500 | 2025-10-01  | 12 months       | 2026-10-01  |                              ~5.0 | Pinecone project                                         |
| Exa Startup Credits               |    $1 000 | 2025-10-01  | 12 months       | 2026-10-01  |                              ~5.0 | Exa workspace                                            |
| Tavily Startup Credits            |      $500 | 2025-10-01  | 12 months       | 2026-10-01  |                              ~5.0 | Tavily workspace                                         |
| Cloudflare Enterprise zone        |  (annual) | 2025-08-01  | 12 months       | 2026-08-01  |                              ~3.0 | Cloudflare Enterprise contract; auto-renews              |
| DO Hatch (pending)                |    TBD    | TBD         | 12 months       | TBD         |                                  — | DO Hatch application; $200 trial credits cover Phase 3 if Hatch is delayed |

**Captured-at timestamp:** 2026-05-03T00:00:00Z. Operator must re-pull
the live "remaining USD" balance from each console at the start of every
phase and update `provider-credit-matrix.md` §1 in place. Activation
dates above come from the credit-programme welcome emails archived in
1Password under `infra/credit-grants/`; if those archives are
incomplete, the operator must email each programme manager to confirm
the exact activation date before Phase 7 (when credit-burn projections
become billing-critical).

**Cutover scheduling note:** Phases 1–6 must complete before
2026-09-01 (AWS Activate expiry — the earliest of the four primary
grants), or the AWS landing zone moves to paid spend. Phase 7
(decommission) should land before 2026-08-01 (Cloudflare Enterprise
auto-renewal) so the renewal contract reflects the post-rebalance
footprint.

### 7.2 Net change

- **Paid spend drops from ~$95/mo to ~$20/mo** (DO App Platform fee for
  the API + core services after the trial credit is exhausted).
- **Credit burn rises from ~$130/mo to ~$265/mo**, but is now spread
  across **four** grants ($2 000 GCP + $1 000 AWS + $2 500 Azure + DO
  Hatch) instead of being concentrated on two ($2 000 GCP + $1 000 AWS).
  The single-grant exposure on GCP drops by ~70%.

## 8. Relationship to existing tasks

- **Task #263** (paid CF add-on retirement) — runs in parallel; its
  completion is a soft dependency for Phase 7 (so the post-rebalance
  Cloudflare bill is genuinely $0 add-on spend).
- **Task #248** (single-cloud GCP consolidation) — **superseded in
  full**. Phases 2 and 3 of #248 (move dispatch + cron under one GCP
  project, expand Cloud Run footprint) are dropped: GCP is now AI-only.
  Phase 1 of #248 (Cloud Tasks audit) is **absorbed** as part of §4.2
  inventory in this plan.
- **Task #336** (Railway → DO) — **superseded**. The standalone DO move
  is now Phase 3 of this ADR, with proper landing-zone, CI, and cutover
  dependencies; the Rust core is included (it was out of scope on #336).
- Downstream, planned tasks "Stand up the AWS landing zone…" and "Stand
  up the Azure landing zone…" are Phase 1b and 1c of this ADR.

## 9. Open questions

1. **DO Hatch acceptance.** Confirm Hatch credit amount and expiry
   before Phase 3 commits to App Platform pricing. If Hatch is denied,
   re-evaluate Droplet + managed Postgres pricing instead.
2. **gRPC streaming on DO App Platform.** Bidi streaming is not GA on
   App Platform as of this ADR; the Rust core ships with an HTTP/JSON
   shim that covers every non-streaming RPC. If streaming becomes a
   blocker, the Rust core moves to a DO Droplet (manifest pre-staged).
3. **Logpush authentication for Azure ingest.** Azure does not accept
   bearer tokens in the same shape as GCP / Axiom; the Phase 5 task must
   confirm whether Logpush's HTTP destination supports the required
   header shape, or whether we need an intermediate worker.
4. **Sentry vs App Insights overlap.** Both ingest exceptions; we keep
   Sentry as the primary error tracker (existing dashboards, replay,
   crons) and use App Insights for *infra* metrics + alerts only. This
   boundary is a code-review rule, not a tooling restriction.

## 10. References

- Credit matrix: [`provider-credit-matrix.md`](provider-credit-matrix.md)
- Existing migration runbook: [`startup-credits-migration.md`](startup-credits-migration.md)
- Cloudflare Pages settings: [`../../CLOUDFLARE_PAGES.md`](../../CLOUDFLARE_PAGES.md)
- Existing AWS Terraform: `infra/aws/lambda-bedrock-proxy.tf`,
  `infra/aws/lambda-email-worker.tf`, `infra/aws/route53-latency.tf`
- Existing Azure Terraform: `infra/azure/cosmos-db-cache.tf`,
  `infra/azure/front-door.tf`
- Existing GCP Terraform (to be retired in Phase 7): `infra/gcp/`
- Background loop wrappers: `cloud_tasks_client.py`,
  `cloud_scheduler_client.py`
