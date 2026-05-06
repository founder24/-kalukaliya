# Providers & Architecture

> ⚠️ **V4 cross-reference (2026-05-06; updated post-B3 user-lock).** The
> locked source of truth for the overall Syrabit architecture is
> [`infra/v4-locked-architecture.md`](../../../../infra/v4-locked-architecture.md).
> If anything below disagrees with V4, V4 wins. This doc is preserved as the
> v3 four-cloud rebalance map (ADR-0001). The V4-locked chat dispatch order
> is **Azure OpenAI `gpt-4.1-nano` SOLE primary → Workers-AI Mistral-7B →
> Workers-AI Llama-3.2-3B → generic Workers-AI**. The earlier V4-draft
> "token-length + risk-score router → Workers-AI Qwen3-0.6B / Vertex Gemini
> 2.5 Flash co-primary" was **explicitly rejected by the founder on
> 2026-05-06** — no CF Worker dispatch router is built; Vertex is reserved
> for the long-form `content` pool + safety/validation only. Embedding
> namespace separation (`cached_gemma_today` vs
> `fallback_vertex_pending_reembed`) and Pinecone region (`aws-ap-south-1`)
> override anything implied here.

This is the canonical map of *which provider does what* in the
four-cloud rebalance under [`ADR-0001-four-way-hosting-rebalance.md`](ADR-0001-four-way-hosting-rebalance.md).
For request-time call-out behaviour and failure modes see the
matching feature runbooks under [`../features/`](../features/).

---

## 1. Pillar summary

| Pillar | Hosts | Key docs |
|--------|-------|----------|
| Cloudflare | Edge, DNS, WAF, R2 (object store CDN), Logpush | [`../CLOUDFLARE_OBSERVATORY.md`](../CLOUDFLARE_OBSERVATORY.md) |
| AWS | SQS + Lambda async fan-out (incl. Vertex re-embed queue worker), S3 (temp dumps; canonical sync to R2), Textract, Rekognition, Personalize | [`aws-landing-zone.md`](aws-landing-zone.md), [`workers-on-aws.md`](workers-on-aws.md) _(Bedrock-Cohere removed Task #347)_ |
| Azure | Container Apps Jobs (cron), Application Insights + Log Analytics (unified observability sink), Key Vault, Azure OpenAI, Azure AI services | [`azure-landing-zone.md`](azure-landing-zone.md), [`cron-on-azure.md`](cron-on-azure.md), [`../features/azure-native.md`](../features/azure-native.md) |
| GCP | Vision, Speech-to-Text, Text-to-Speech, Discovery Engine, Web Risk | _legacy provider chain — kept as primary tier for these features pending the Azure A/B results_ |
| Sarvam | Indic ↔ English translation (primary) | _vendor — see `lang/router.py`_ |
| Pinecone | RAG vector retriever (primary) | _vendor — see `rag/retriever.py`_ |

S3 is the sole object store across all four clouds; Azure Blob
Storage is **not** used (per §9 of [`cloud-allocation-plan.md`](cloud-allocation-plan.md)).

## 2. Powered by Azure

Azure plays three concrete roles in production today:

### 2a. Hosting infrastructure
- **Azure Container Apps Jobs** — every Cloud Scheduler job and every
  `aca-job`-classified asyncio loop (38 loops total) runs here on its
  original cadence (Task #332).
- **Application Insights + Log Analytics workspace** — the unified
  observability sink that Cloudflare Logpush, DO container OTEL
  exporters, AWS CloudWatch metric stream, and Azure-native
  diagnostic settings all forward to (Task #329).
- **Azure Key Vault + user-assigned managed identity** — the only
  credential surface for cron jobs and the Azure-side AI services.
  No static keys are minted.

### 2b. Advanced AI features (Task #338)

Ten Azure-native AI services are lit up alongside the hosting tier.
Each is registered as an *additional path* in an existing chain —
none replaces a GCP / Sarvam / Cohere / Pinecone / Personalize
provider. Selection is router-controlled and feature-flag gated.

| Surface | Service | Wired into | Failure ladder |
|---------|---------|-----------|----------------|
| LLM routing | **Azure OpenAI** | `ai_gateway/registry.py` as `llm/azure-openai`; primary for `gpt-4.1-nano` chat role (V4 §4 user-locked 2026-05-06) | Azure OpenAI → Workers-AI Mistral-7B → Workers-AI Llama-3.2-3B → generic Workers-AI (V4 §4 A9). _Direct OpenAI / Bedrock-Cohere / Groq / direct Gemini all removed in Task #347; Vertex retained for `content` pool + safety only._ |
| TTS / STT | **Azure AI Speech** (incl. Custom Neural Voice "Syra") | `voice/router.py` | Azure Speech ↔ Google STT/TTS |
| Translation | **Azure AI Translator** | `lang/router.py` | Sarvam → Bhashini → Azure Translator → cached → English passthrough |
| OCR (layout-aware) | **Azure Document Intelligence** | `ocr/router.py` (past papers + marks sheets) | Doc Intelligence → Textract → AI Vision |
| OCR / image understanding | **Azure AI Vision** | `ocr/router.py` (general images) | Google Vision → Azure Vision → Tesseract |
| Moderation | **Azure Content Safety** | `moderation/queue.py` (sync on chat I/O, comments, uploads) | Borderline routes to admin queue alongside Rekognition flags |
| Topic Discovery + SEO Manager | **Azure AI Language** | `seo/enrich.py` (key phrases, entities, summaries, PII) | Falls back to last cached enrichment; PII redaction falls back to regex |
| RAG retrieval | **Azure AI Search** (hybrid keyword + vector) | `rag/retriever.py` under `rag.retriever` flag | `pinecone` (default) / `azure-search` / `shadow` |
| Watchdog | **Azure Anomaly Detector** | Cron job + `ai_anomaly_detected` App Insights metric → ops Slack action group | Parallels existing throttle + Sentry watchdogs |
| Recommendations A/B | **Azure Personalizer** | `reco/next_quiz.py` under `recs.next_quiz_provider` flag | `deterministic` (default) / `personalizer` / `shadow` |

Per-feature toggles, throttle indicators, latency, and spend are
visible in `AdminAzureAiPanel` (Infrastructure tab of `AdminHealth`).
Detailed wiring, identity scopes, rotation flow, and out-of-scope
items live in [`../features/azure-native.md`](../features/azure-native.md).

### 2c. Invariants

- **Azure Blob Storage is not used** anywhere — including for
  artefacts these AI services produce. S3 is the sole object store.
- **Managed-identity-only auth** — every Azure AI account runs with
  `local_auth_enabled = false`; the data plane refuses API-key auth.
- **Endpoint URLs in Key Vault, no API keys.** Wrappers resolve
  endpoints via `services/backend/azure_ai/_resolver.py` which
  caches per-process and uses `DefaultAzureCredential`.
- ~~**Azure OpenAI does not displace Bedrock for Cohere roles.**~~
  *(Superseded by Task #347, 2026-05-05: AWS Bedrock direct integration
  was decommissioned. Cohere is no longer reached via Bedrock; it is
  retained only as the embed-failover specialist via the Cloudflare AI
  Gateway BYOK slug `cohere/v1` — see V4 §1 / replit.md.)*

## 3. Cross-pillar identity boundaries

- The Azure cron-tier managed identity (`syrabit-cron-jobs-runtime`)
  has zero IAM on AWS / GCP / Cloudflare. When an Azure AI service
  needs to read S3 (Document Intelligence layout analysis, AI Search
  ingest, Custom Neural Voice training), the Azure-side cron job
  mints a presigned S3 URL using the AWS-side runtime credentials
  and passes that URL to the Azure service. The Azure service
  itself never holds AWS credentials.
- The AWS deploy SP and the Azure deploy SP are separate; neither
  can touch the other pillar's runtime resources.
- Cloudflare API tokens are scoped per-zone and are never minted into
  the cron-tier image.

## 4. Where new providers go

Adding a new provider follows the same template as Task #338:

1. Pick the chain it joins (LLM, TTS, OCR, retriever, etc.).
2. Add the wrapper under `services/backend/<provider>/`.
3. Register it in the matching router; **do not** wire selection
   into the wrapper.
4. Surface throttle + latency + spend in `AdminHealth`.
5. Document in `docs/features/<provider>.md` with identity scope,
   failure mode, and admin toggle.
6. Update §1 of this doc.
