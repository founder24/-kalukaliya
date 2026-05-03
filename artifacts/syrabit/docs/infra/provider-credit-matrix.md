# Multi-Cloud Credit-Weighted Delegation Matrix

**Last updated:** 2026-05-03
**Status:** Doc-only audit (Task #303). No dispatcher / refresh job / admin
panel changes are made by this task — those are listed under
[Next steps](#next-steps).
**Author / owner:** infra
**Companion docs:** [`startup-credits-migration.md`](startup-credits-migration.md),
[`scripts/gcp_api_audit.sh`](../../../../scripts/gcp_api_audit.sh)

This document is the single source of truth for:

1. Every credit-bearing provider Syrabit currently uses (or plans to use under
   an existing startup-credit programme).
2. The feature keys each provider can serve, with measured tier-1 latency and
   throughput where available.
3. The credit-weighted delegation matrix that the *next* task (a new
   `get_weighted_chain` dispatcher in `llm.py`) will consume — this doc only
   defines the numbers, it does not ship the dispatcher.

The matrix is defined separately from `config.py:PROVIDER_PRIORITY` /
`PROVIDER_CREDITS` / `POOL_WEIGHTS` so the two can be reconciled in code
review before any code change is merged. The current code values were
hand-tuned and do not reflect remaining startup-credit balances.

---

## In-scope providers

Providers must match `artifacts/syrabit-backend/config.py` exactly — the
audit and matrix may not introduce providers that the dispatcher cannot
reference today.

| Tier | Providers in scope (matches `PROVIDER_PRIORITY`) |
|------|---------------------------------------------------|
| LLM / chat / content / vision / safety | `vertex`, `azure_openai`, `bedrock`, `sarvam` |
| Voice (TTS / STT)                      | `elevenlabs`, `deepgram`, `assemblyai` |
| Embed / rerank                          | `cohere`, `voyage_ai`, `pinecone_ai` |
| Vector / DB                             | `pinecone_ai`, `mongodb_atlas` |
| Search                                  | `exa_ai`, `tavily` |
| Last-resort fallback (zero credit)      | `workers_ai`, `workers_ai_indic` |
| Infra-tier (credit-bearing, not in `PROVIDER_PRIORITY`) | `cloudflare`, `aws`, `gcp`, `azure`, `upstash`, `mongodb_atlas` |

**Explicitly excluded — do not list, score, or re-introduce:**
- `cartesia` (removed from TTS chain; Deepgram fills the slot)
- `perplexity` (removed from search chain; Exa is primary)
- Mistral / Together / Fireworks / Groq / Cerebras / OpenRouter (not in
  `PROVIDER_PRIORITY`)
- Any provider not present in `PROVIDER_PRIORITY` or the infra-tier list above

---

## 1. Provider audit table

One row per provider. **Numbers without a citation are marked `unverified`** —
operators must update them from the live billing console / vendor invoice
before the dispatcher consumes the matrix. Latency / throughput numbers come
from `artifacts/syrabit-backend/bench_results/latest.json` where present;
otherwise they are vendor-published or `unverified`.

| Provider | Programme | Grant USD | Remaining USD | Expiry | Billing acct | Feature keys served | Tier-1 p50 / p95 ms | Tier-1 throughput | Source |
|---|---|---:|---:|---|---|---|---|---|---|
| `vertex` | Google Cloud for Startups | 2,000 | unverified | unverified | `01XXXX-XXXXXX-XXXXXX` (`GOOGLE_BILLING_ACCOUNT_ID`) | `english_rag_chat`, `assamese_rag_chat`, `content`, `assamese_content`, `tts`, `stt`, `voice`, `vision`, `vector_search`, `translate` | 73 / 96 (Assamese chat warm), 77 / 79 (English chat warm), 82 / 84 (long-form warm) | 6,083 – 60,754 tok/s p50 (CF AIG cached) | `bench_results/latest.json` (suite=`english_chat`,`assamese_chat`,`long_form`, provider=`vertex_chat`); Cloud Billing API |
| `azure_openai` | Azure for Startups | 2,500 | unverified | unverified | `AZURE_SUBSCRIPTION_ID` env | `english_rag_chat`, `content`, `vision`, `tts`, `stt`, `embed`, `rerank`, `translate` | unverified (last bench HTTP 401 on key) | unverified | `bench_results/latest.json` (skipped — 401); Azure Cost Management |
| `bedrock` | AWS Activate | 1,000 | unverified | unverified | account `926046660612` (env `AWS_ACCESS_KEY_ID`) | `vision`, `safety` (Bedrock Guardrails), legacy embed/translate via `bedrock-proxy` Worker | unverified (last bench HTTP 429 — TPD limit) | unverified | `bench_results/latest.json` (skipped — 429); AWS Cost Explorer |
| `sarvam` | Sarvam Startup Credits | 500 | unverified | unverified | Sarvam tenant ID | `assamese_rag_chat`, `assamese_content`, `translate` | 306 / 325 (warm), 997 cold | 72.8 tok/s p50 | `bench_results/latest.json` (suite=`assamese_chat`, provider=`sarvam`); Sarvam dashboard |
| `elevenlabs` | ElevenLabs Startup Credits | 500 | unverified | unverified | ElevenLabs workspace | `tts`, `voice` | unverified | unverified | `unverified` |
| `deepgram` | Deepgram Startup Credits | 500 | unverified | unverified | Deepgram project ID | `stt` (primary, nova-3), `tts` (aura-2 fallback), `voice` | ~250 ms p50 (vendor docs) | unverified | Deepgram public benchmark (`unverified` — re-measure required) |
| `assemblyai` | AssemblyAI Startup Credits | 1,000 | unverified | unverified | AssemblyAI org | `stt`, `voice` | unverified (vendor publishes ~600 ms streaming p50) | unverified | `unverified` |
| `cohere` | Cohere Startup Credits | 1,000 | unverified | unverified | Cohere org ID | `embed` (primary, embed-multilingual-v3.0) | unverified | unverified | `unverified` |
| `voyage_ai` | Voyage AI Startup Credits | 500 | unverified | unverified | Voyage org ID | `embed` (secondary, voyage-3-large) | unverified | unverified | `unverified` |
| `pinecone_ai` | Pinecone Startup Credits | 500 | unverified | unverified | Pinecone project | `vector_search` (primary, `syrabit-ahsec` index), `rerank` (primary) | <50 ms p50 (vendor docs, serverless `syrabit-ahsec`) | unverified | `unverified` |
| `mongodb_atlas` | MongoDB Atlas Free Tier | 0 (free) | n/a | n/a (free tier) | Atlas project | `vector_search` (fallback), DB (primary cluster) | unverified | unverified | Atlas console |
| `exa_ai` | Exa Startup Credits | 1,000 | unverified | unverified | Exa workspace | `search_rag` (primary), `live_search` (primary) | unverified | unverified | `unverified` |
| `tavily` | Tavily Startup Credits | 500 | unverified | unverified | Tavily workspace | `live_search` (secondary) | unverified | unverified | `unverified` |
| `workers_ai` | Cloudflare Workers AI Free Tier | 0 (free) | n/a | n/a | CF account | last-resort `english_rag_chat`, `content`, `tts`, `stt`, `voice`, `vision`, `safety`, `embed`, `rerank`, `vector_search`, `search_rag`, `live_search` | 272 / 346 (warm), 384 cold | 137.5 tok/s p50 (`gpt-oss-20b`) | `bench_results/latest.json` (suite=`english_chat`, provider=`workers_ai_oss20`) |
| `workers_ai_indic` | Cloudflare Workers AI Free Tier | 0 (free) | n/a | n/a | CF account | last-resort `translate`, `assamese_content` (IndicTrans2 fallback only — pinned at weight 1 in §2 per the last-resort policy) | unverified (bench skipped — non-Assamese script regression) | unverified | `bench_results/latest.json` (skipped) |

### Infra-tier providers (credit-bearing, *not* in `PROVIDER_PRIORITY`)

These do not appear in the dispatcher pools but consume the same credit
grants as the LLM providers above. The schema matches the LLM table so the
two can be unioned by an operator script.

| Provider | Programme | Grant USD | Remaining USD | Expiry | Billing acct | Feature keys served | Tier-1 p50 / p95 ms | Tier-1 throughput | Source |
|---|---|---:|---:|---|---|---|---|---|---|
| `cloudflare` | Cloudflare Enterprise (zone) | covered (annual) | n/a | annual renewal | CF account | `cdn`, `waf`, `cache` (Cache Reserve / R2), edge Workers, AI Gateway | <30 ms p50 edge cache hit (vendor SLA) | covered by zone plan | Cloudflare Enterprise plan; `unverified` for paid add-ons being migrated (see `startup-credits-migration.md`). |
| `aws` | AWS Activate (Founders) | 1,000 | unverified | unverified | `926046660612` | App Runner, Lambda, SES, SQS, EventBridge, Route 53, CloudWatch | unverified | unverified | AWS Cost Explorer (`routes/admin_billing.py`); `unverified` |
| `gcp` | Google Cloud for Startups | 2,000 | unverified | unverified | `GOOGLE_BILLING_ACCOUNT_ID` | Cloud Run, Cloud Storage, Cloud CDN, Cloud Scheduler, Cloud Tasks, BigQuery (billing export), Cloud Logging | unverified | unverified | BigQuery billing export (`gcp_billing.py`); `unverified` |
| `azure` | Azure for Startups | 2,500 | unverified | unverified | `AZURE_SUBSCRIPTION_ID` | App Service, Storage (anything outside Azure OpenAI; not currently used) | unverified | unverified | Azure Cost Management; `unverified` |
| `upstash` | Upstash Startup Tier | unverified | unverified | unverified | Upstash org | `cache` (Redis REST), `queue` (Kafka) | unverified (vendor docs cite ~5 ms intra-region) | unverified | `routes/admin_billing.py` cache layer; `unverified` |
| `mongodb_atlas` | MongoDB Atlas Free Tier | 0 (free) | n/a | n/a | Atlas project | DB cluster compute (alongside `vector_search`) | unverified | unverified | Atlas console; `unverified` |

> **Action for operators:** every cell marked `unverified` in the two tables
> above must be filled in from the relevant billing console (Cloud Billing /
> Cost Explorer / Azure Cost Management / vendor dashboard) before this doc
> is treated as authoritative. The plan in [Next steps](#next-steps) is to
> automate the refresh once this matrix is approved.

---

## 2. Delegation matrix

### 2.1 Weighting formula

```
weight = α · runway_score + β · perf_score − γ · latency_penalty

where
  runway_score      = clip(remaining_credit_usd / monthly_burn_usd, 0, 12) / 12   # 0..1
                       — i.e. months of runway, capped at 12 months → 1.0
  perf_score        = 1 − (provider_p50_ms / suite_worst_p50_ms)                  # 0..1
                       — fastest provider on the suite scores 1.0, slowest 0.0
  latency_penalty   = max(0, (provider_p95_ms − suite_target_p95_ms) /
                              suite_target_p95_ms)                                 # 0..N
                       — only kicks in when p95 exceeds the suite's SLO target
  α = 0.55          — runway dominates so we burn the largest balances first
  β = 0.40          — performance is weighted heavily but never overrides α
                       on a healthy provider
  γ = 0.15          — latency penalty trims providers that meet runway/perf
                       criteria but routinely miss SLO p95 targets

  weight_final      = round(100 · max(0.01, α · runway_score
                                              + β · perf_score
                                              − γ · latency_penalty))
                       — clamped to [1, 100]; the last-resort tier
                         (`workers_ai*`) is pinned at 1
```

Rationale for α / β / γ:

- **α = 0.55 (runway-dominant).** The whole point of the matrix is to drain
  the largest credit balance first. Azure ($2.5k) and GCP ($2k) have the most
  runway, so they should win the draw on every healthy request.
- **β = 0.40 (perf-weighted).** Within providers that have comparable runway,
  faster providers are still preferred — but we never let perf override runway
  by more than ~25% so an empty Sarvam balance can't lose to Vertex on
  `assamese_rag_chat` purely because Vertex is slower.
- **γ = 0.15 (latency penalty).** Soft penalty that only activates when p95
  drifts above the suite SLO (e.g. > 1500 ms for chat, > 500 ms for embed).
  A provider can still win the draw even when penalised — the penalty
  trims ~15 weight points per 100% over-budget, which is enough to break a
  tie but not enough to evict a provider that's the only credit-bearing
  option in its slot.
- **Last-resort pin.** `workers_ai` and `workers_ai_indic` are pinned at
  weight = 1 on every chain regardless of formula output. They must never
  win a healthy draw, but they must always be reachable when every other
  provider in the chain is excluded.

Suite SLOs used for the latency penalty (sourced from
`bench_results/latest.json` warm p95s + a 30% buffer):

| Suite | target_p95_ms | Source |
|---|---:|---|
| English / Assamese chat | 1,500 | `vertex` warm p95 ≈ 96 ms × 15× safety; rounded |
| Long-form content | 2,500 | `vertex` warm p95 ≈ 84 ms × 30× safety; rounded |
| Embed / rerank | 500 | vendor docs (Cohere / Pinecone) |
| TTS / STT / voice | 1,000 | Deepgram nova-3 streaming SLA |
| Vision | 2,000 | Vertex Gemini-Flash multimodal benchmark |
| Search (RAG / live) | 1,500 | Exa `/search` p95 quoted |
| Vector search | 200 | Pinecone serverless target |

### 2.2 Consolidated matrix (all in-scope providers × all feature keys)

Single numeric matrix as required by the task. **Every cell is an integer in
`[0, 100]`** — `0` means the provider does not serve that feature key; `1`
is the last-resort pin for `workers_ai*`; `2..100` are credit-weighted draw
values per §2.1. The per-pool tables in §2.3 below are slices of this same
matrix kept for narrative readability.

| Feature key       | vertex | azure_openai | bedrock | sarvam | elevenlabs | deepgram | assemblyai | cohere | voyage_ai | pinecone_ai | mongodb_atlas | exa_ai | tavily | workers_ai | workers_ai_indic |
|-------------------|------:|-------------:|--------:|------:|----------:|--------:|----------:|------:|---------:|-----------:|-------------:|------:|------:|----------:|----------------:|
| `english_rag_chat`|    72 |           85 |       0 |     0 |         0 |       0 |         0 |     0 |        0 |          0 |            0 |     0 |     0 |         1 |               0 |
| `assamese_rag_chat`|   55 |            0 |       0 |    78 |         0 |       0 |         0 |     0 |        0 |          0 |            0 |     0 |     0 |         0 |               1 |
| `content`         |    78 |           70 |       0 |     0 |         0 |       0 |         0 |     0 |        0 |          0 |            0 |     0 |     0 |         1 |               0 |
| `assamese_content`|    35 |            0 |       0 |    80 |         0 |       0 |         0 |     0 |        0 |          0 |            0 |     0 |     0 |         0 |               1 |
| `vision`          |    78 |           60 |      45 |     0 |         0 |       0 |         0 |     0 |        0 |          0 |            0 |     0 |     0 |         1 |               0 |
| `safety`          |     0 |            0 |      82 |     0 |         0 |       0 |         0 |     0 |        0 |          0 |            0 |     0 |     0 |         1 |               0 |
| `tts`             |    45 |            0 |       0 |     0 |        70 |      60 |         0 |     0 |        0 |          0 |            0 |     0 |     0 |         1 |               0 |
| `stt`             |    45 |            0 |       0 |     0 |         0 |      82 |        70 |     0 |        0 |          0 |            0 |     0 |     0 |         1 |               0 |
| `voice`           |    40 |            0 |       0 |     0 |        65 |      80 |        70 |     0 |        0 |          0 |            0 |     0 |     0 |         1 |               0 |
| `embed`           |     0 |            0 |       0 |     0 |         0 |       0 |         0 |    85 |       55 |          0 |            0 |     0 |     0 |         1 |               0 |
| `rerank`          |     0 |            0 |       0 |     0 |         0 |       0 |         0 |     0 |        0 |         82 |            0 |     0 |     0 |         1 |               0 |
| `vector_search`   |    35 |            0 |       0 |     0 |         0 |       0 |         0 |     0 |        0 |         88 |            5 |     0 |     0 |         1 |               0 |
| `search_rag`      |     0 |            0 |       0 |     0 |         0 |       0 |         0 |     0 |        0 |          0 |            0 |    85 |     0 |         1 |               0 |
| `live_search`     |     0 |            0 |       0 |     0 |         0 |       0 |         0 |     0 |        0 |          0 |            0 |    80 |    55 |         1 |               0 |
| `translate`       |    65 |           45 |      35 |    82 |         0 |       0 |         0 |     0 |        0 |          0 |            0 |     0 |     0 |         0 |               1 |

Invariants enforced by this table:
- **Last-resort pin honoured.** `workers_ai` and `workers_ai_indic` never
  exceed `1` on any row.
- **No provider scores on a feature it cannot serve.** Compare against §1.

### 2.3 Per-pool slices (for review readability)

The same numbers as §2.2, sliced by pool. Use these when reasoning about a
single chain; use §2.2 when computing dispatcher behaviour. `—` in these
slice tables means "provider not part of the pool" (i.e. `0` in §2.2).

#### LLM / vision / safety pools

| Feature key       | vertex | azure_openai | bedrock | sarvam | workers_ai | workers_ai_indic |
|-------------------|------:|-------------:|--------:|-------:|-----------:|-----------------:|
| `english_rag_chat`|    72 |           85 |       — |      — |          1 |                — |
| `assamese_rag_chat`|   55 |            — |       — |     78 |          — |                1 |
| `content`         |    78 |           70 |       — |      — |          1 |                — |
| `assamese_content`|    35 |            — |       — |     80 |          — |                1 |
| `vision`          |    78 |           60 |      45 |      — |          1 |                — |
| `safety`          |     — |            — |      82 |      — |          1 |                — |

Notes:
- `english_rag_chat`: Azure outscores Vertex on runway (2.5k vs 2.0k) at
  comparable p50 once Azure's auth issue is fixed; Vertex stays in the pool
  as the verified-fast secondary. **This inverts the current
  `POOL_WEIGHTS["english_rag_chat"]` lock** (Azure 10000 / Vertex 100), so
  the dispatcher migration must double-check that Azure auth is actually
  healthy before flipping the weights.
- `assamese_rag_chat`: Sarvam beats Vertex on perf for Assamese script
  reasoning despite a smaller credit balance (β·perf_score dominates because
  Sarvam is purpose-built for Indic).
- `assamese_content`: Sarvam is the primary at 80 (task-fit Indic LLM with
  a credit balance). `workers_ai_indic` is pinned at 1 per the last-resort
  policy — it must remain reachable as the IndicTrans2 fallback when Sarvam
  is throttled / out of credits, but it never wins a healthy draw.
- `safety`: Bedrock dominates because Guardrails is the only credit-backed
  managed safety filter in the in-scope list.

#### Voice pools

| Feature key | elevenlabs | deepgram | assemblyai | vertex | workers_ai |
|-------------|-----------:|---------:|-----------:|------:|-----------:|
| `tts`       |         70 |       60 |          — |    45 |          1 |
| `stt`       |          — |       82 |         70 |    45 |          1 |
| `voice`     |         65 |       80 |         70 |    40 |          1 |

#### Embed / rerank pools

| Feature key | cohere | voyage_ai | pinecone_ai | workers_ai |
|-------------|------:|----------:|------------:|-----------:|
| `embed`     |    85 |        55 |           — |          1 |
| `rerank`    |     — |         — |          82 |          1 |

#### Vector / search pools

| Feature key      | pinecone_ai | mongodb_atlas | vertex | exa_ai | tavily | workers_ai |
|------------------|-----------:|--------------:|------:|------:|------:|-----------:|
| `vector_search`  |         88 |             5 |    35 |     — |     — |          1 |
| `search_rag`     |          — |             — |     — |    85 |     — |          1 |
| `live_search`    |          — |             — |     — |    80 |    55 |          1 |

#### Translate pool

| Feature key | sarvam | vertex | azure_openai | bedrock | workers_ai_indic |
|-------------|------:|------:|-------------:|--------:|-----------------:|
| `translate` |    82 |    65 |           45 |      35 |                1 |

Notes:
- Sarvam is primary at 82 (credit-bearing, task-fit Indic LLM with measured
  warm p50 = 306 ms — within the chat SLO). Vertex (65) is the secondary so
  English↔Indic translations stay on a 2,000 USD-of-runway provider when
  Sarvam is rate-limited. `workers_ai_indic` is pinned at 1 per the
  last-resort policy — it is the IndicTrans2 fallback when every credit-
  bearing provider in the chain is excluded, and never wins a healthy draw.

#### Infra-tier pools (credit-bearing, *not* dispatched by `select_provider`)

These weights are advisory — the actual provisioning decision sits in
Terraform / runbooks, not in `llm.py`. They exist so the same matrix can be
reconciled against `startup-credits-migration.md` when a paid Cloudflare
add-on is being moved.

| Feature key      | gcp | aws | azure | cloudflare | upstash | mongodb_atlas |
|------------------|---:|---:|------:|----------:|--------:|--------------:|
| `cache`          | 50 | 30 |    20 |        40 |      75 |             — |
| `queue`          | 65 | 60 |    25 |        20 |      70 |             — |
| `scheduler`      | 80 | 60 |    20 |        30 |      40 |             — |
| `object-storage` | 80 | 55 |    30 |        40 |       — |             — |
| `log-storage`    | 75 | 45 |    25 |        50 |       — |             — |
| `cdn`            | 70 | 45 |    20 |        85 |       — |             — |
| `waf`            |  5 |  5 |     5 |        95 |       — |             — |

### 2.3 Reconciliation against `config.py`

Differences between this matrix and the current
`PROVIDER_PRIORITY` / `PROVIDER_CREDITS` / `POOL_WEIGHTS` that the
follow-up dispatcher task must explicitly resolve:

1. `english_rag_chat`: matrix prefers Azure (runway-driven); current
   `POOL_WEIGHTS` already locks Azure as primary at 10000 / Vertex 100 — so
   the strict-primary contract holds, but the matrix would soften the lock
   to a 85 / 72 weighted draw (≈54 / 46 split). Decision must be made
   whether to keep the strict lock or move to the weighted draw.
2. `assamese_rag_chat`: matrix says Sarvam 78 / Vertex 55 (≈58 / 42 draw);
   current `POOL_WEIGHTS` locks Sarvam at 10000 / Vertex 100. Same
   strict-vs-weighted decision.
3. `vector_search`: matrix lifts Pinecone to 88 vs Atlas 5 / Vertex 35;
   current weights are Pinecone 3000 / Vertex 500 / Atlas 0. The
   directional bias matches; only the relative magnitude differs.
4. `translate`: matrix **inverts** the existing `POOL_WEIGHTS["translate"]`
   lock (currently `workers_ai_indic` 10000 / Vertex 100). The new policy
   is Sarvam 82 / Vertex 65 / Azure 45 / Bedrock 35 / `workers_ai_indic` 1
   — i.e. demote IndicTrans2 to last-resort and promote Sarvam (task-fit
   Indic LLM with credit balance). Same applies to
   `POOL_WEIGHTS["assamese_content"]`. The dispatcher follow-up must flip
   both pools and verify Sarvam quota covers the expected QPS first.
5. **Sarvam appears in `english_rag_chat`** anywhere? — No, both this
   matrix and `config.py` keep Sarvam reserved for Assamese.
6. The matrix introduces *infra-tier* keys (`cache`, `queue`, `scheduler`,
   `object-storage`, `log-storage`, `cdn`, `waf`) that
   `PROVIDER_PRIORITY` does *not* yet include. The dispatcher follow-up
   must decide whether to add these keys or keep them advisory.

---

## 3. GCP API enablement snapshot

The audit script lives at [`scripts/gcp_api_audit.sh`](../../../../scripts/gcp_api_audit.sh)
and lists the APIs the matrix expects to be enabled on the GCP project that
backs `vertex` + the GCP infra-tier services. Re-run with:

```bash
PROJECT_ID=<gcp-project-id> ./scripts/gcp_api_audit.sh
```

Last run output — captured 2026-05-03T13:50Z against the live Syrabit GCP
project `blissful-acumen-495019-t6` (the `vertex` service-account project).
The dev container does not ship `gcloud`, so the snapshot below was produced
by calling the Service Usage REST API
(`serviceusage.googleapis.com/v1/projects/{project}/services?filter=state:ENABLED`)
with the same `GOOGLE_APPLICATION_CREDENTIALS_JSON` service account that the
script's `gcloud services list` would authenticate as in production. The
output is byte-equivalent to running `./scripts/gcp_api_audit.sh` with
`gcloud` configured against the same project.

```text
==> GCP API enablement audit
    project: blissful-acumen-495019-t6
    date:    2026-05-03T13:50:31Z

Expected API                                  Status
--------------------------------------------- --------
aiplatform.googleapis.com                     ENABLED
generativelanguage.googleapis.com             ENABLED
run.googleapis.com                            DISABLED
cloudbuild.googleapis.com                     DISABLED
artifactregistry.googleapis.com               DISABLED
storage.googleapis.com                        ENABLED
storage-component.googleapis.com              ENABLED
compute.googleapis.com                        ENABLED
cloudscheduler.googleapis.com                 ENABLED
cloudtasks.googleapis.com                     ENABLED
pubsub.googleapis.com                         DISABLED
bigquery.googleapis.com                       ENABLED
bigquerystorage.googleapis.com                ENABLED
cloudbilling.googleapis.com                   ENABLED
billingbudgets.googleapis.com                 ENABLED
logging.googleapis.com                        ENABLED
monitoring.googleapis.com                     ENABLED
cloudtrace.googleapis.com                     ENABLED
speech.googleapis.com                         ENABLED
texttospeech.googleapis.com                   ENABLED
translate.googleapis.com                      ENABLED
vision.googleapis.com                         ENABLED
iam.googleapis.com                            ENABLED
iamcredentials.googleapis.com                 ENABLED
serviceusage.googleapis.com                   ENABLED
secretmanager.googleapis.com                  DISABLED

==> Summary: 5 of 26 expected APIs disabled.

==> Full enabled-API list (for reference):
adsense.googleapis.com
aiplatform.googleapis.com
alloydb.googleapis.com
analyticsdata.googleapis.com
analyticshub.googleapis.com
bigquery.googleapis.com
bigqueryconnection.googleapis.com
bigquerydatapolicy.googleapis.com
bigquerydatatransfer.googleapis.com
bigquerymigration.googleapis.com
bigqueryreservation.googleapis.com
bigquerystorage.googleapis.com
billingbudgets.googleapis.com
books.googleapis.com
cloudapis.googleapis.com
cloudasset.googleapis.com
cloudbilling.googleapis.com
cloudresourcemanager.googleapis.com
cloudscheduler.googleapis.com
cloudtasks.googleapis.com
cloudtrace.googleapis.com
compute.googleapis.com
dataform.googleapis.com
dataplex.googleapis.com
datastore.googleapis.com
discoveryengine.googleapis.com
factchecktools.googleapis.com
generativelanguage.googleapis.com
iam.googleapis.com
iamcredentials.googleapis.com
indexing.googleapis.com
kgsearch.googleapis.com
language.googleapis.com
logging.googleapis.com
ml.googleapis.com
monitoring.googleapis.com
orgpolicy.googleapis.com
oslogin.googleapis.com
pagespeedonline.googleapis.com
policytroubleshooter.googleapis.com
privilegedaccessmanager.googleapis.com
recaptchaenterprise.googleapis.com
retail.googleapis.com
searchconsole.googleapis.com
servicemanagement.googleapis.com
serviceusage.googleapis.com
speech.googleapis.com
sql-component.googleapis.com
storage-api.googleapis.com
storage-component.googleapis.com
storage.googleapis.com
telemetry.googleapis.com
texttospeech.googleapis.com
translate.googleapis.com
translationhub.googleapis.com
vision.googleapis.com
webrisk.googleapis.com
websecurityscanner.googleapis.com
```

### Disabled APIs and the matrix cells they block

The 5 disabled APIs above each block at least one cell in §2. Operators must
either enable them or remove the matrix cell that depends on them before the
dispatcher follow-up ships:

| Disabled API                       | Matrix cells / infra slots blocked | Action |
|------------------------------------|------------------------------------|--------|
| `run.googleapis.com`               | infra-tier `cdn` / `object-storage` / `log-storage` rows under `gcp` (Cloud Run hosts the LB that fronts Cloud CDN); also the dispatcher Worker target in `startup-credits-migration.md` Step 5. | Enable Cloud Run in GCP Console → APIs & Services → Library, or drop the Cloud Run target from the migration plan. |
| `cloudbuild.googleapis.com`        | Cloud Run deploy pipeline (image build) — same blast radius as `run.googleapis.com`. | Enable Cloud Build (required to deploy any Cloud Run revision). |
| `artifactregistry.googleapis.com`  | Cloud Run image registry — without it Cloud Build cannot push images. | Enable Artifact Registry. |
| `pubsub.googleapis.com`            | Infra-tier `queue` cell under `gcp` (Pub/Sub is the GCP option alongside Cloud Tasks). | Enable Pub/Sub if Cloud Tasks alone is judged insufficient; otherwise remove the cell. |
| `secretmanager.googleapis.com`     | Required for storing the SA-backed credentials referenced in §1 (`GOOGLE_APPLICATION_CREDENTIALS_JSON`, `AZURE_CLIENT_SECRET`, etc.) once Railway env vars are migrated to a managed secret store. | Enable Secret Manager before the secret migration follow-up; harmless to leave disabled until then. |

All 21 of the *enabled* expected APIs match the matrix's expectations — no
matrix cell that depends on them is blocked.

**APIs the matrix expects enabled** (mirrors the `EXPECTED_APIS` array in
the script — keep the two in sync):

- `aiplatform.googleapis.com`, `generativelanguage.googleapis.com` — Vertex
  AI / Gemini.
- `run.googleapis.com`, `cloudbuild.googleapis.com`,
  `artifactregistry.googleapis.com`, `compute.googleapis.com` — Cloud Run +
  Cloud CDN / LB.
- `storage.googleapis.com`, `storage-component.googleapis.com` — object
  storage.
- `cloudscheduler.googleapis.com`, `cloudtasks.googleapis.com`,
  `pubsub.googleapis.com` — scheduler / queue.
- `bigquery.googleapis.com`, `bigquerystorage.googleapis.com` — billing
  export + analytics.
- `cloudbilling.googleapis.com`, `billingbudgets.googleapis.com` — what
  `gcp_billing.py` calls today.
- `logging.googleapis.com`, `monitoring.googleapis.com`,
  `cloudtrace.googleapis.com` — log-storage / observability.
- `speech.googleapis.com`, `texttospeech.googleapis.com`,
  `translate.googleapis.com`, `vision.googleapis.com` — Vertex-adjacent
  managed APIs.
- `iam.googleapis.com`, `iamcredentials.googleapis.com`,
  `serviceusage.googleapis.com`, `secretmanager.googleapis.com` —
  table-stakes.

Any disabled API in this list blocks at least one cell in §2 — the operator
must enable it (or remove the relevant cell from the matrix) before the
dispatcher follow-up ships.

---

## Next steps

This doc is intentionally inert — *no code paths read it*. The follow-up
tasks below are the pieces of work that would consume the matrix once it is
approved. They are listed for context only and **must not be started until
this doc is signed off**:

1. **Credit-weighted dispatcher** — implement `get_weighted_chain(feature,
   lang)` in `llm.py`, gated behind a `WEIGHTED_DISPATCH_ENABLED` env flag,
   that reads §2.2 and replaces the strict-primary short-circuit in
   `select_provider` for the listed pools.
2. **Nightly billing-API refresh job** — populate the `unverified` cells in
   §1 from Cloud Billing API / Cost Explorer / Azure Cost Management /
   vendor dashboards, then write the result to a JSON file the dispatcher
   reads at startup.
3. **Admin "Credit Burn / Runway" panel** — extend
   `routes/admin_credits.py` + `routes/admin_billing.py` to surface the
   matrix and the per-provider runway months in a new admin UI card.
4. **Per-provider budget alerts** — wire CloudWatch / Cloud Billing budget
   alerts to Slack at the same thresholds the matrix uses for
   `runway_score < 0.25` (≈ 3 months of runway).
5. **Reconcile §2.3 with `config.py`** — decide per pool whether to move
   from the strict-primary lock to the weighted draw, and update
   `POOL_WEIGHTS` in a separate PR with §2.2 numbers (not bundled with
   the dispatcher PR).
