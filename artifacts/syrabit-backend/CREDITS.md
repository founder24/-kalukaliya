# Syrabit.ai — Provider Credit Grants

This document maps every cloud credit grant to the specific services consuming
it, the credential required, and the estimated monthly burn so the team can
track runway and plan renewals.

---

## Google Cloud Platform — $2,000 Founders Credit Grant

**Grant:** $2,000 USD  
**Credential:** `GOOGLE_APPLICATION_CREDENTIALS_JSON` — single service account JSON, set as a Replit Secret  
**Project:** Set via `GOOGLE_CLOUD_PROJECT` env var (or falls back to `VERTEX_PROJECT_ID`)  
**Budget alerts:** $1,800 (90%) and $1,900 (95%) fire to the ops Slack channel. Set `GOOGLE_BILLING_ALERT=1` when the webhook fires to surface the admin panel warning.

### Services in Use

| Service | Model / API | Use Case | Pricing | Est. Monthly Burn |
|---------|-------------|----------|---------|-------------------|
| **Cloud Speech-to-Text v2** | `chirp_2` | Indic STT — Hindi (hi-IN), Bengali (bn-IN), Assamese (as-IN). Workers AI Whisper is primary for English. | ~$0.016 / min audio | ~$4 / month |
| **Cloud Text-to-Speech** | Neural2 — `hi-IN-Neural2-A`, `hi-IN-Neural2-C`, `bn-IN-Neural2-A`, `as-IN-Wavenet-B` | Indic TTS. Cartesia sonic-2 is primary for English and is the fallback for Indic when Google TTS is unavailable. | ~$16 / 1M chars | ~$3 / month |
| **Cloud Translation v3** | `translateText` endpoint | Indic translation — Hindi, Bengali, Assamese. Workers AI indictrans2 is fallback. Non-Indic bypasses Google entirely. | ~$20 / 1M chars | ~$6 / month |
| **Cloud Vision** | `DOCUMENT_TEXT_DETECTION` | OCR for Devanagari/Bengali script documents (past papers, textbooks). Triggers when Workers AI vision confidence < 0.80 or document language is Indic. Workers AI vision remains primary for Latin-script. | ~$1.50 / 1K images | ~$2 / month |
| **Gemini 2.0 Flash** | `gemini-2.0-flash` via AI Studio | Chat fallback — position-2 in the chat chain: `workers_ai → gemini → groq → cerebras`. Activates only when Workers AI load > 80%. | ~$0.075 / 1M tokens (input) | ~$3 / month |
| **Vertex AI Embeddings** | `text-embedding-004` (768-dim) | Embed fallback for long-form content (> 2048 tokens) or when Workers AI embed is in cooldown. **NOTE:** 768-dim — do NOT mix with main 1024-dim bge-large index. | ~$0.00013 / 1K chars | ~$1 / month |

**Total estimated monthly burn: ~$19/month → ~8 years runway at current scale.**

### Why Google Cloud for Indic Stack?

- **Chirp_2 STT** is the most accurate ASR model for Hindi, Bengali, and Assamese — Whisper and AssemblyAI have significantly higher WER on these languages.
- **Neural2 TTS** is the only production-grade neural TTS for all three Indic languages — Cartesia, Deepgram, and ElevenLabs have zero native Indic support.
- **Translation v3** outperforms Workers AI `indictrans2-en-indic-1B` on Bengali and Assamese (lower BLEU gap on educational domain text).
- **Vision OCR** is the only provider with trained Devanagari and Bengali script layout-aware document detection — Workers AI llama-3.2 vision treats Devanagari as noisy Latin.

### Fallback Chain Summary

```
STT:         [Indic audio] → Google Chirp_2 → Sarvam Saaras → Workers AI Whisper
             [English audio] → Sarvam Saaras (unchanged primary) → Workers AI Whisper

TTS:         [Indic] → Google Neural2 → Cartesia sonic-2 (fallback only; no Indic voice)
             [English] → Cartesia sonic-2 → Workers AI Deepgram Aura-2

Translation: [hi/bn/as target] → Google Translation v3 → Workers AI indictrans2 → LLM prompt
             [other target]    → Workers AI indictrans2 → LLM prompt

OCR:         [Latin script / confidence ≥ 0.80] → Workers AI llama-3.2 vision
             [Devanagari/Bengali / confidence < 0.80] → Google Vision DOCUMENT_TEXT_DETECTION

Chat:        workers_ai(llama-3.3-70b) → gemini-2.0-flash → groq → cerebras → openrouter

Embeddings:  workers_ai(bge-large-en-v1.5, 1024-dim) [primary]
             → vertex_ai(text-embedding-004, 768-dim) [long-form fallback only]
             → cohere(embed-multilingual-v3.0)
```

### Credentials Required

| Env Var | Description |
|---------|-------------|
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Full service account JSON (single line). Grants access to STT v2, TTS, Translation v3, Vision, and Vertex AI. |
| `GOOGLE_CLOUD_PROJECT` | GCP project ID (optional — auto-detected from service account JSON if not set). |
| `GEMINI_API_KEY` | Google AI Studio key for Gemini 2.0 Flash chat fallback (already set for CF BYOK). |
| `GOOGLE_BILLING_ALERT` | Set to `1` when the GCP budget webhook fires to surface admin panel warning. |
| `GOOGLE_BILLING_ACCOUNT_ID` | **Task #253** — Billing account ID (format: `XXXXXX-XXXXXX-XXXXXX`). Found in GCP Console → Billing → Account overview. Enables live budget data from the Cloud Billing Budget API. |

### Live Billing Data Setup (Task #253)

The `/api/admin/vertex/gcp-credits` endpoint queries the **Cloud Billing Budget API**
(`billingbudgets.googleapis.com/v1`) for real budget thresholds when
`GOOGLE_BILLING_ACCOUNT_ID` is set. Without it the endpoint falls back to the
static $19/month estimate.

**One-time setup:**

1. **Find your billing account ID:** GCP Console → Billing → Account overview.
   Copy the ID in the format `XXXXXX-XXXXXX-XXXXXX`.

2. **Set the secret:** Add `GOOGLE_BILLING_ACCOUNT_ID=XXXXXX-XXXXXX-XXXXXX` to
   Replit Secrets (or Railway env vars).

3. **Grant permission to the service account:**
   - GCP Console → Billing → Account management → Permissions
   - Add the service account email (from `GOOGLE_APPLICATION_CREDENTIALS_JSON` →
     `client_email` field) with role **`Billing Account Viewer`** (`roles/billing.viewer`)
   - This is a *billing account* permission — not a project IAM binding.

4. **Enable the Budget API on the project:**
   - GCP Console → APIs & Services → Enable APIs
   - Enable: **Cloud Billing Budget API** (`billingbudgets.googleapis.com`)
   - Also ensure: **Cloud Billing API** (`cloudbilling.googleapis.com`) is enabled

5. **Verify:** Call `GET /api/admin/vertex/gcp-credits` and check `live_data: true`
   in the response. If `billing_api_error` is set, follow the error message.

**What the live data provides:**
- `budget_warn_threshold_usd` / `budget_critical_threshold_usd` — auto-read from
  Budget API threshold rules (replaces hardcoded values)
- `spend_mtd_usd` — real month-to-date spend if the Budget API exposes `currentSpend`
  (availability depends on billing account configuration)
- `billing_account_name` / `billing_account_open` — billing account status
- `budgets[]` — full list of budgets with thresholds for the account

**Note on per-service spend:** Budget thresholds are always read live from the
Budget API when `GOOGLE_BILLING_ACCOUNT_ID` is set. For real per-service spend
(`services_detail[*].spend_mtd_usd` with `spend_is_live: true`), BigQuery
Billing Export must also be enabled — see the section below.

### BigQuery Billing Export Setup (for real per-service spend)

Enabling GCP Billing Export to BigQuery is the only way to get genuine per-service
MTD spend from the API.  Without it, per-service figures in `services_detail` are
proportional estimates (clearly labeled with `spend_is_live: false`).

**One-time BigQuery setup:**

1. **Enable BigQuery export:**
   GCP Console → Billing → Billing export → BigQuery export → **Enable**
   Choose a project and dataset name (e.g. `billing_export`).

2. **Grant service account access:**
   - `roles/bigquery.jobUser` on the GCP project (project IAM)
   - `roles/bigquery.dataViewer` on the billing export dataset (dataset IAM)

3. **Table name:** The standard table is auto-derived from the billing account ID:
   `gcp_billing_export_v1_{ACCOUNT_ID_with_underscores}`
   e.g. for `12A3B4-C5D6E7-F8G9H0` → `gcp_billing_export_v1_12A3B4_C5D6E7_F8G9H0`
   Override with `GOOGLE_BILLING_BIGQUERY_TABLE` if your setup differs.

4. **Optionally set env vars** (all auto-derived when `GOOGLE_BILLING_ACCOUNT_ID`
   and `GOOGLE_CLOUD_PROJECT` are set):

   | Env Var | Default | Description |
   |---------|---------|-------------|
   | `GOOGLE_BILLING_BIGQUERY_PROJECT` | `GOOGLE_CLOUD_PROJECT` | Project with the export dataset |
   | `GOOGLE_BILLING_BIGQUERY_DATASET` | `billing_export` | Dataset name |
   | `GOOGLE_BILLING_BIGQUERY_TABLE` | auto-derived | Table name |
   | `GOOGLE_BILLING_BIGQUERY_LOCATION` | `US` | Dataset region (e.g. `EU`, `us-central1`) |

5. **Verify:** Call `GET /api/admin/vertex/gcp-credits` and check `live_spend_data: true`
   and `spend_mtd_source: "bigquery_billing_export"` in the response.

**Note:** The BigQuery export typically has a 1–2 day lag, so spend figures reflect
costs up to ~48 hours ago, not real-time. For real-time alerting use the Budget API
webhook flow below.

### Budget Alert Setup (Google Cloud Console)

1. Navigate to: **Billing → Budgets & alerts → Create budget**
2. Create two alerts on the grant budget:
   - **$1,800 (90%)** — warning threshold
   - **$1,900 (95%)** — critical threshold
3. Set Pub/Sub topic to forward to the ops Slack channel webhook
4. When the webhook fires, set `GOOGLE_BILLING_ALERT=1` in the Railway/Replit environment
5. **With Task #253 live data enabled:** the thresholds are auto-read from the budget
   and `GOOGLE_BILLING_ALERT` is only needed as a real-time override when a Pub/Sub
   notification fires between polling cycles.

---

## Cloudflare — $5,000 Startup Credit Grant

**Credential:** `CLOUDFLARE_API_TOKEN` + `CF_AI_GATEWAY_ACCOUNT_ID`

| Service | Use | Est. Monthly Burn |
|---------|-----|-------------------|
| Workers AI (LLM) | Primary chat — llama-3.3-70b-fp8, gpt-oss-20b, qwen2.5-72b | ~$0 (included in plan) |
| Workers AI (Embed) | Primary embeddings — bge-large-en-v1.5 (1024-dim) | ~$0 (included) |
| Workers AI (STT) | English transcription — Whisper large-v3-turbo | ~$0 (included) |
| Workers AI (TTS) | English TTS — Deepgram Aura-2 | ~$0 (included) |
| Workers AI (Vision) | Image analysis — llama-3.2-11b-vision | ~$0 (included) |
| Workers AI (Translate) | indictrans2 fallback | ~$0 (included) |
| R2 Storage | Media/document storage | ~$0–$5 / month |
| AI Gateway | Caching, logging, BYOK | ~$0 (included) |
| Vectorize | Vector search index | ~$0 (included in plan) |

**Total: ~$0–$5/month against $5,000 credit grant → 80+ years runway.**

---

## AWS — $1,000 Credit Grant

**Credential:** `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` + `AWS_REGION`

| Service | Use | Est. Monthly Burn |
|---------|-----|-------------------|
| App Runner | Backend hosting | ~$30–$80 / month |
| S3 | Backup storage | ~$2 / month |

**Total: ~$32–$82/month against $1,000 grant → ~12–30 months runway.**

---

*Last updated: May 2026 (Task #247)*
