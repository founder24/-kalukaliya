# GCP Landing Zone Runbook — AI APIs, Cloud Trace, Cloud Billing

> ⚠️ **V4 cross-reference (2026-05-06).** The locked source of truth is
> [`infra/v4-locked-architecture.md`](../../../../infra/v4-locked-architecture.md).
> If anything below disagrees with V4, V4 wins. The four-cloud delegation
> matrix at [`infra/four-cloud-delegation.md`](../../../../infra/four-cloud-delegation.md)
> is the canonical "who-owns-what" map; this doc is only the GCP operator runbook.

**Status:** Live (Task #489 — post-cleanup AI-API-only surface)
**Owner:** infra
**Companion:** [`aws-landing-zone.md`](aws-landing-zone.md), [`azure-landing-zone.md`](azure-landing-zone.md), [`providers-task-347-decommission.md`](providers-task-347-decommission.md)
**Terraform root:** [`../../infra/gcp/`](../../infra/gcp/)

---

## 1. What this project hosts

The GCP project (`syrabit-prod`) is **only** the foundation for the
auxiliary AI-API + observability surface. After the post-cleanup pass:

- **AI APIs (API-key-only):** Google Knowledge Graph Search, PageSpeed
  Insights, Fact Check Tools, Cloud Natural Language, **Web Risk**
  (admin + `edu_browser`), Books, GA4, GSC + Indexing API.
- **AI APIs (SA-gated):** Google STT (Chirp 2), Google TTS (Neural2),
  Google Translate v3, Google Vision OCR, Discovery Engine ingest,
  **Vertex Gemini content-formatter** (long-form English + Assamese
  overflow only — wired by sibling task #494), **Vertex Gemini RAI**
  (batch only).
- **OAuth surface:** Google OAuth 2.0 client (used by the live
  `/api/auth/supabase-session` Google login handler — kept until V4
  §13 Phase 4 completes).
- **Observability:** Cloud Trace as the long-retention OTEL backstop
  (Sentry is the live correlator per V4 §7).
- **Billing telemetry:** Cloud Billing API + BigQuery Billing Export →
  Meter A/B/C credit-burn alerts (V4 §10 Rule C, notify-only Slack).

It is **not** the home of:

- The synchronous API tier (FastAPI) or Rust core — both go to **Azure
  Container Apps** (V4 §0).
- Async fan-out queues / workers — those go to **AWS** (SQS + Lambda +
  EventBridge per V4 §3).
- Cron / scheduled jobs — Azure Container Apps Jobs (V4 §0). **Cloud
  Scheduler is forbidden post-Task #489**; the `cloud_scheduler_client`
  module was deleted alongside `cloud_tasks_client` because no GCP
  hosting / cron / CI / queueing workloads remain.
- CI / build — GitHub Actions; **Cloud Build is forbidden**
  (`cloudbuild.yaml` deleted by Task #489).
- Production DNS — Cloudflare keeps the apex.
- Pinecone-writing embeddings — Vertex multilingual embedding is
  **retired** by sibling task #490; the only embedder is Cloudflare
  Workers AI EmbeddingGemma (V4 §2 + §15 amendment).
- Vertex chat — Vertex Gemini is **NOT** on the chat hot path
  (founder-locked 2026-05-06, V4 §4); only the content-formatter and
  batch-RAI roles remain.

## 2. Project & regions

| Item                       | Value                                              |
|----------------------------|----------------------------------------------------|
| Project ID                 | `syrabit-prod`                                     |
| Billing account ID         | _set as Terraform var `gcp_billing_account_id`_     |
| Billing contact            | `ops@syrabit.ai`                                   |
| Free-tier credit balance   | tracked in [`provider-credit-matrix.md`](provider-credit-matrix.md) |
| Primary region (AI APIs)   | `asia-south1` (Mumbai — closest to majority of users) |
| Secondary region           | `us-central1` (Discovery Engine global default)    |
| Monthly budget             | $200 (50 % / 80 % / 100 % alerts → email + Slack) |
| Service-account name       | `syrabit-ai-apis@syrabit-prod.iam.gserviceaccount.com` |

## 3. Service-account roles (least-privilege)

The single SA `syrabit-ai-apis@syrabit-prod.iam.gserviceaccount.com` is
the only principal Syrabit code uses against GCP. Roles bound by
Terraform (`infra/gcp/iam.tf`):

| Role | Scope | Why |
|---|---|---|
| `roles/serviceusage.serviceUsageConsumer` | project | Required to call any enabled API as the SA. |
| `roles/cloudtrace.agent` | project | Lambda / ACA OTEL exporter writes spans here (V4 §7). |
| `roles/billing.viewer` | billing account | `gcp_billing.py` reads budget thresholds + current spend. |
| `roles/bigquery.jobUser` | project | `gcp_billing.py` runs the Billing Export query. |
| `roles/bigquery.dataViewer` | billing-export dataset | Same — reads the export table. |
| `roles/discoveryengine.editor` | data store | `discovery_engine_ingest.py` upserts topics. |
| `roles/aiplatform.user` | project | Sibling task #494 wires Vertex Gemini as **content-formatter only** (no chat role). |

**Roles intentionally NOT granted (and forbidden):**

- `roles/run.developer`, `roles/run.invoker`, `roles/run.admin` — no
  Cloud Run hosting (Task #347 + Task #489 deletion of
  `CLOUDRUN-DEPLOY.md` / `cloudbuild.yaml`).
- `roles/cloudtasks.*`, `roles/cloudscheduler.*` — no queueing or
  scheduling on GCP.
- `roles/compute.*`, `roles/container.*`, `roles/cloudbuild.builds.*` —
  no compute / GKE / CI on GCP.
- `roles/storage.admin` on hosting buckets — no GCS hosting.

The CI drift guard (`.github/workflows/four-cloud-delegation-drift.yml`)
greps for any of the forbidden role grants in `infra/gcp/**/*.tf` and
fails the merge.

## 4. API enablement matrix

| API | Auth mode | Used by |
|---|---|---|
| `webrisk.googleapis.com` | API key (`GOOGLE_WEB_RISK_API_KEY`) | `web_risk_client.check_uri()` from `edu_reader.fetch_and_extract` (post-redirect URL) and from admin `/api/admin/security/web-risk`. |
| `kgsearch.googleapis.com` | API key | `kg_search_client`. |
| `pagespeedonline.googleapis.com` | API key (optional) | `pagespeed_service`. |
| `factchecktools.googleapis.com` | API key | `fact_check_client`. |
| `language.googleapis.com` | API key | `nlp_client`. |
| `books.googleapis.com` | API key (optional) | `books_client`. |
| `speech.googleapis.com` | SA | Google STT (Chirp 2). |
| `texttospeech.googleapis.com` | SA | Google TTS (Neural2). |
| `translate.googleapis.com` | SA | Google Translate v3. |
| `vision.googleapis.com` | SA | Google Vision OCR. |
| `discoveryengine.googleapis.com` | SA | `discovery_engine_client` + `discovery_engine_ingest`. |
| `aiplatform.googleapis.com` | SA | Vertex Gemini content-formatter only (sibling #494). |
| `cloudtrace.googleapis.com` | SA | OTEL exporter from ACA + AWS Lambda (V4 §7). |
| `billingbudgets.googleapis.com` | SA | `gcp_billing.fetch_budgets`. |
| `cloudbilling.googleapis.com` | SA | `gcp_billing.fetch_billing_account`. |
| `bigquery.googleapis.com` | SA | `gcp_billing.fetch_service_spend_from_bigquery`. |

**Forbidden (must remain disabled, asserted by `terraform plan` drift check):**
`run.googleapis.com`, `cloudbuild.googleapis.com`,
`artifactregistry.googleapis.com` (no GCP image hosting),
`cloudtasks.googleapis.com`, `cloudscheduler.googleapis.com`,
`cloudfunctions.googleapis.com`, `compute.googleapis.com`,
`container.googleapis.com`.

## 5. Service-account key rotation

- **Cadence:** quarterly drill (per V4 §6 cadence row).
- **Procedure:**
  1. `gcloud iam service-accounts keys create new.json --iam-account=syrabit-ai-apis@syrabit-prod.iam.gserviceaccount.com`
  2. Stage in Azure Key Vault as `gcp-application-credentials-json-next`.
  3. Flip ACA env `GOOGLE_APPLICATION_CREDENTIALS_JSON` reference from
     `gcp-application-credentials-json` → `…-next`. New revision rolls.
  4. Smoke-test `/api/admin/gcp/services-status` returns
     `service_account_configured: true` and the same project ID.
  5. Promote the `…-next` KV secret to `gcp-application-credentials-json`
     and delete the old GCP key:
     `gcloud iam service-accounts keys delete <OLD_KEY_ID> --iam-account=…`
  6. Drop the `…-next` KV alias.
- **Drill log:** append a row to `docs/ops/dr-drills/YYYY-Qn-drill.md`.

## 6. Quota request procedure

Quotas are project-scoped. The request flow:

1. Confirm the burn vs cap in Cloud Console → IAM & Admin → Quotas.
2. Open a quota-increase request with the **specific API + region +
   target value + 30-day usage chart**.
3. Note the request ID in `docs/infra/quota-requests/YYYY-MM.md`
   (one file per month).
4. After grant: re-check `gcp_billing` Meter A burn for the affected
   service so the new ceiling is reflected in the credit projection.

**Note:** Vertex AI quotas only need to cover the **content-formatter**
role (sibling task #494). Chat-hot-path quota is on Azure OpenAI, not
Vertex (V4 §4).

## 7. Web Risk integration on `edu_browser`

`web_risk_client.check_uri(final_url)` is invoked from
`edu_reader.fetch_and_extract` **after** redirect resolution and
allowlist re-check, but **before** the readability extraction returns
the payload to the caller. This satisfies the
`threat_model.md` Information Disclosure rule that publisher policy must
be enforced on the page actually fetched, not the caller-supplied URL.

- **Block decision:** `safe == false` on any of the configured threat
  types (`MALWARE`, `SOCIAL_ENGINEERING`, `UNWANTED_SOFTWARE`).
- **Failure sink:** `log_blocked_request(final_url, "web_risk_<threats>",
  …)` — this is the same audit pipeline used for allowlist + robots
  rejections, so on-call already triages it via the same dashboard.
- **Disabled mode:** when `GOOGLE_WEB_RISK_API_KEY` is unset the client
  returns `status="disabled"` and the reader **fail-opens** (degraded —
  same posture as before the integration); this is logged as a
  high-severity warning so the missing key is visible in Sentry.
- **Cache:** Web Risk responses include `expireTime`; we honour it via
  the existing edu_reader payload cache (24 h TTL minus 5 min skew),
  capped to the response `expireTime` when shorter.
- **Redirect coverage:** because the check runs against the
  post-redirect `final_url`, not the request input, an open-redirect URL
  on an allowlisted host that walks to a malicious page is still
  blocked.

## 8. Cloud Billing alerts → Meter A/B/C

`gcp_billing.get_billing_summary()` + `get_service_spend()` feed the
existing meter pipeline (`credit_burn_meter.py` /
`credit_burn_meter_runtime.py`):

- **Trigger:** 80 % of the credit pool (V4 §10 Rule C). Notify-only —
  no auto-flip.
- **Sink:** `#syrabit-oncall` Slack alert via `slack_notifier`.
- **Post-cleanup pool:** drops `cerebras`, `cohere`, `voyage_ai`, and
  `vertex_embed` rows because sibling tasks #490/#491 retire them.
  Vertex Gemini (content-formatter only) and Web Risk remain on the
  pool.
- **Test:** `python scripts/credit_burn_smoke.py --force-pct 0.81`
  triggers the alert path without waiting for real burn.

## 9. Cloud Trace OTEL backstop

ACA + AWS Lambda OTEL exporters ship spans to Cloud Trace as the
long-retention backstop (V4 §7). Sentry remains the live correlator.

- **Exporter:** OpenTelemetry Collector with the `googlecloud` exporter,
  authenticated by the same SA via `GOOGLE_APPLICATION_CREDENTIALS_JSON`.
- **Wiring location:**
  - ACA: `infra/azure/aca-syrabit-backend.bicep` adds env
    `OTEL_EXPORTER_GCP_PROJECT_ID=syrabit-prod` +
    `OTEL_TRACES_EXPORTER=googlecloud,sentry` (comma-separated, both
    fire).
  - AWS Lambda: `lambda-otel.tf` adds the same env + bundles the
    `opentelemetry-exporter-gcp-trace` layer.
- **Canary:** GitHub Actions cron `cloud-trace-canary.yml` posts a chat
  turn with a known `traceparent`, then asserts the same trace ID
  appears in Cloud Trace within 60 s. Failure pages on-call.

## 10. Decommissioned (do NOT re-introduce)

| What | Removed by | Replacement |
|---|---|---|
| Cloud Run hosting (`CLOUDRUN-DEPLOY.md`, `cloudbuild.yaml`) | Task #347, file deletion in Task #489 | Azure Container Apps (V4 §0). |
| Cloud Tasks (`cloud_tasks_client.py`) | Task #332 (consumer port), file deletion in Task #489 | AWS SQS + Lambda. |
| Cloud Scheduler (`cloud_scheduler_client.py`) | Task #347, file deletion in Task #489 | AWS EventBridge schedules + Azure Container Apps Jobs (cron). |
| Vertex Gemini chat hot path | Founder choice 2026-05-06 (V4 §4) + sibling #490 | Azure OpenAI `gpt-4.1-nano` SOLE primary. |
| Vertex multilingual embedding (Pinecone-writing) | Sibling #490 (§15 amendment) | Cache-only degraded mode + AWS SQS deferred-embed replay queue. |
| Vertex Vector Search retriever | Sibling #490 | Pinecone Rerank v0 + RRF fusion (`rag.py`). |

The CI drift guard (`.github/workflows/four-cloud-delegation-drift.yml`)
mechanically blocks re-introduction of any of the above.

---

**Audit anchor:** every row in this doc maps 1:1 to a row in
`infra/four-cloud-delegation.md`. If the two ever drift, the matrix
wins; this doc is the operator runbook layered on top of it.
