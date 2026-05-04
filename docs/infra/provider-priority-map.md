# PROVIDER_PRIORITY Map (canonical)

**Date:** 2026-05-04
**Format:** matches the `PROVIDER_PRIORITY` dict shape in
`artifacts/syrabit-backend/config.py`.
**Excludes:** Cerebras, Groq (removed from all chat chains). Sarvam is
scoped to `assamese_rag_chat` / `assamese_content` / `translate` only —
**not in TTS / voice / STT / vision**.
**Delegation:** every provider lives inside exactly one of the 4
strategic clouds (AWS / Azure / Cloudflare / GCP). The credit pool that
absorbs each call is annotated inline.

---

## §1 The dict (drop-in replacement for the attached snippet)

```python
PROVIDER_PRIORITY = {

    # =========================================================
    # Conversational RAG chat — English
    # =========================================================
    "english_rag_chat": [
        "vertex",            # GCP   — Gemini 2.5 Flash (Google Cloud for Startups, $2k)
        "azure_openai",      # AZURE — GPT‑4.1‑mini deployment "syrabit-chat" (Microsoft Founders Hub, $2.5k)
        "workers_ai",        # CF    — @cf/openai/gpt-oss-20b → @cf/meta/llama-3.3-70b-instruct-fp8-fast (CF for Startups, $5k)
        "bedrock",           # AWS   — Claude Haiku via Bedrock (AWS Activate, $1k) — Tier‑4 reserve only; default off
    ],

    # =========================================================
    # Conversational RAG chat — Assamese
    # Sarvam scoped to this chain only (Indic‑native). Vertex is
    # the strategic primary today; Sarvam activates if its credit
    # lands and only inside this chain.
    # =========================================================
    "assamese_rag_chat": [
        "vertex",            # GCP   — Gemini 2.5 Flash, Indic‑tuned prompt (Google Cloud, $2k)
        "sarvam",            # AUX   — Sarvam‑M (Sarvam credit, PENDING) — only fires if credit lands
        "azure_openai",      # AZURE — GPT‑4.1‑mini with translate‑then‑answer fallback (Founders Hub)
        "workers_ai",        # CF    — IndicTrans2 → gpt‑oss‑20b answer leg (CF for Startups)
    ],

    # =========================================================
    # Long‑form content generation (MCQ pools, notes, admin pipelines)
    # =========================================================
    "content": [
        "azure_openai",      # AZURE — GPT‑4.1‑mini deployment "syrabit-quiz" / "syrabit-cards" (Founders Hub, $2.5k)
        "vertex",            # GCP   — Gemini 2.5 Flash (Google Cloud, $2k)
        "workers_ai",        # CF    — @cf/openai/gpt-oss-20b (CF for Startups, $5k)
    ],

    # =========================================================
    # Assamese content (translated/adapted from English)
    # =========================================================
    "assamese_content": [
        "vertex",            # GCP   — Gemini 2.5 Flash translate+adapt (Google Cloud, $2k)
        "sarvam",            # AUX   — Sarvam‑M Indic‑native (Sarvam credit, PENDING)
        "workers_ai",        # CF    — IndicTrans2 (CF for Startups, $5k)
    ],

    # =========================================================
    # Text-to-Speech
    # Sarvam REMOVED — voice/TTS is no longer Sarvam‑routed.
    # =========================================================
    "tts": [
        "elevenlabs",        # AUX   — eleven_multilingual_v2 (ElevenLabs $4k credit, PENDING)
        "vertex",            # GCP   — Cloud TTS Neural2 (en‑US‑Neural2‑F / as‑IN‑Wavenet‑A / hi‑IN‑Neural2‑A / bn‑IN‑Wavenet‑A) (Google Cloud, $2k)
        "cartesia",          # AUX   — Cartesia Sonic (free credit standby)
        "workers_ai",        # CF    — @cf/myshell-ai/melotts (CF for Startups, $5k)
        "polly",             # AWS   — AWS Polly Neural (AWS Activate, $1k) — post‑#337 fallback
    ],

    # =========================================================
    # Speech-to-Text
    # =========================================================
    "stt": [
        "deepgram",          # AUX   — nova‑3‑general (Deepgram $1k credit, PENDING)
        "assemblyai",        # AUX   — best model, dual_channel + punctuate
        "workers_ai",        # CF    — @cf/openai/whisper-large-v3-turbo (CF for Startups, $5k)
        "vertex",            # GCP   — Cloud Speech Chirp (asia‑south1) (Google Cloud, $2k)
    ],

    # =========================================================
    # Real‑time voice pipeline (streaming STT → LLM → TTS)
    # Sarvam REMOVED. Sequential gather: STT chain || chat chain || TTS chain.
    # =========================================================
    "voice": [
        "deepgram",          # AUX   — STT leg primary
        "assemblyai",        # AUX   — STT leg fallback
        "elevenlabs",        # AUX   — TTS leg primary (when $4k credit lands)
        "vertex",            # GCP   — TTS leg fallback (Cloud TTS Neural2) (Google Cloud)
        "workers_ai",        # CF    — STT (Whisper) + TTS (MeloTTS) full‑edge fallback (CF for Startups)
        "polly",             # AWS   — TTS Neural last‑resort (AWS Activate)
    ],

    # =========================================================
    # Text embeddings (RAG ingestion + query)
    # =========================================================
    "embed": [
        "bedrock_cohere",    # AWS   — cohere.embed-multilingual-v3 via Bedrock, us‑west‑2 (AWS Activate $1k — funds Cohere)
        "voyage_ai",         # AUX   — voyage‑3‑multilingual (free trial standby)
        "vertex",            # GCP   — text-embedding-004 (768‑dim, requires re‑index) (Google Cloud, $2k)
        "workers_ai",        # CF    — @cf/baai/bge-m3 (1024‑dim, edge) (CF for Startups, $5k)
    ],

    # =========================================================
    # Semantic reranking (post‑retrieval scoring)
    # =========================================================
    "rerank": [
        "bedrock_cohere",    # AWS   — cohere.rerank-multilingual-v3 via Bedrock (AWS Activate)
        "voyage_ai",         # AUX   — rerank‑2 multilingual (free trial standby)
        "workers_ai",        # CF    — @cf/baai/bge-reranker-base (CF for Startups)
        # graceful degrade: skip rerank, return Pinecone topK as-is
    ],

    # =========================================================
    # Translation (English ↔ Assamese / Indic)
    # Sarvam allowed here (Indic‑first translation is its strength).
    # =========================================================
    "translate": [
        "workers_ai",        # CF    — @cf/google/indictrans2-en-indic-1b (CF for Startups, $5k)
        "vertex",            # GCP   — Cloud Translation v3 + Gemini polish (Google Cloud, $2k)
        "sarvam",            # AUX   — Sarvam‑Translate (Sarvam credit, PENDING)
        "azure_openai",      # AZURE — GPT‑4.1‑mini translate prompt (Founders Hub)
    ],

    # =========================================================
    # Vision / OCR / image analysis
    # =========================================================
    "vision": [
        "vertex",            # GCP   — Gemini 2.5 Flash multimodal (asia‑south1) (Google Cloud, $2k)
        "vertex_vision",     # GCP   — Cloud Vision API DOCUMENT_TEXT_DETECTION (Google Cloud)
        "workers_ai",        # CF    — @cf/llava-hf/llava-1.5-7b-hf (CF for Startups, $5k)
    ],

    # =========================================================
    # Prompt safety / content moderation
    # =========================================================
    "safety": [
        "vertex",            # GCP   — Gemini built‑in safety + RAI categories (Google Cloud, $2k)
        "workers_ai",        # CF    — @cf/meta/llama-guard-3-8b (CF for Startups, $5k)
    ],

    # =========================================================
    # RAG‑grounded web search (answers with citations)
    # =========================================================
    "search_rag": [
        "vertex_discovery",  # GCP   — Vertex Discovery Engine over CMS+web corpus (Google Cloud, $2k)
        "perplexity",        # AUX   — citation‑rich RAG answers (free tier)
        "workers_ai",        # CF    — gpt‑oss‑20b with retrieved context (CF for Startups, $5k)
    ],

    # =========================================================
    # Live / real‑time web search (freshness‑critical queries)
    # =========================================================
    "live_search": [
        "exa_ai",            # AUX   — neural search (free tier)
        "tavily",            # AUX   — structured results (free tier)
        "workers_ai",        # CF    — Workers Browser Rendering + edge fetch (CF for Startups, $5k)
    ],

    # =========================================================
    # Vector retrieval (Pinecone-primary; cloud fallbacks)
    # =========================================================
    "vector_retrieve": [
        "pinecone",          # AUX   — Starter free tier → Pinecone Startup ($5k reserved)
        "vertex_vector",     # GCP   — Vertex Vector Search / Matching Engine (Google Cloud, $2k)
        "cf_vectorize",      # CF    — Cloudflare Vectorize edge index (CF for Startups, $5k)
        "vertex_discovery",  # GCP   — Vertex Discovery Engine (semantic search over raw CMS)
    ],

    # =========================================================
    # Cache (sessions, rate limit, JWT blacklist, prompt cache)
    # =========================================================
    "cache": [
        "azure_redis",       # AZURE — Azure Cache for Redis Basic C0, centralindia (Founders Hub, $2.5k → $16/mo)
        "momento",           # AUX   — Momento Cache HTTP API (free 5 GB / 5M req)
        "cf_kv",             # CF    — Workers KV / Durable Objects (CF for Startups)
        "mongo_atomic",      # AUX   — Mongo find_and_modify (atomic ops only, last resort)
    ],

    # =========================================================
    # Object storage (sole-store; no multi-cloud writes)
    # =========================================================
    "blob": [
        "s3",                # AWS   — s3://syrabit-prod-* (AWS Activate, $1k) — SOLE OBJECT STORE
        "cf_r2",             # CF    — cold archive only (CF for Startups, $5k)
    ],

    # =========================================================
    # Cron / scheduled jobs
    # =========================================================
    "cron": [
        "azure_container_apps_jobs",  # AZURE — KEDA cron triggers, scale-to-zero (Founders Hub) — CANONICAL
    ],

    # =========================================================
    # Async / queued workloads
    # =========================================================
    "async_queue": [
        "aws_sqs_lambda",    # AWS   — SQS standard queue + Lambda consumers (AWS Activate)
    ],

    # =========================================================
    # APM / observability
    # =========================================================
    "apm": [
        "azure_app_insights", # AZURE — central APM, distributed tracing (Founders Hub) — CANONICAL
        "axiom",             # AUX   — long‑term log retention (free 0.5 TB/mo)
        "sentry",            # AUX   — error tracking (free tier)
    ],
}
```

---

## §2 Per-cloud delegation (which features each cloud absorbs)

Every key in §1 is annotated with the strategic cloud (`GCP`, `AZURE`,
`CF`, `AWS`, or `AUX`). The roll-up below answers
**"what does each cloud actually own?"**

### 2.1 GCP / Vertex ($2k Google Cloud for Startups)

| Role | Features delegated |
|---|---|
| **Tier-1 chat** | `english_rag_chat[0]`, `assamese_rag_chat[0]`, `assamese_content[0]` |
| **Tier-2 content** | `content[1]` |
| **Tier-1 vision** | `vision[0]`, `vision[1]`, `assamese_rag_chat polish` |
| **Tier-1 safety** | `safety[0]` |
| **Tier-1 grounded search** | `search_rag[0]`, `vector_retrieve[1]`, `vector_retrieve[3]` |
| **Tier-2 TTS / STT / translate** | `tts[1]`, `stt[3]`, `translate[1]` |
| **Tier-2 voice** | `voice[3]` |
| **Total at 10k DAU** | **~$150/mo (90% draw, mitigated)** |

### 2.2 Azure ($2.5k Microsoft Founders Hub)

| Role | Features delegated |
|---|---|
| **Tier-1 content** | `content[0]` (quiz, notes, flashcards) |
| **Tier-2 chat** | `english_rag_chat[1]`, `assamese_rag_chat[2]`, `translate[3]` |
| **Tier-1 cache primary** | `cache[0]` (Azure Cache for Redis Basic C0) |
| **Tier-1 cron** | `cron[0]` (Container Apps Jobs, all 18 KEDA-triggered jobs) |
| **Tier-1 APM** | `apm[0]` (App Insights central) |
| **Standby failover backend** | Container Apps secondary region for App Runner |
| **Total at 10k DAU** | **~$87/mo (~38% draw)** |

### 2.3 Cloudflare ($5k CF for Startups)

| Role | Features delegated |
|---|---|
| **Tier-3 chat / content** | `english_rag_chat[2]`, `content[2]`, `assamese_rag_chat[3]` |
| **Tier-1 translate** | `translate[0]` (IndicTrans2 edge) |
| **Tier-3 TTS / STT / voice** | `tts[3]`, `stt[2]`, `voice[4]` |
| **Tier-2/3 embed + rerank** | `embed[3]`, `rerank[2]` |
| **Tier-2 vision / safety** | `vision[2]`, `safety[1]` |
| **Tier-2 search** | `search_rag[2]`, `live_search[2]` |
| **Tier-2 vector** | `vector_retrieve[2]` (Vectorize) |
| **Tier-2 cache** | `cache[2]` (KV / Durable Objects) |
| **Tier-2 blob** | `blob[1]` (R2 cold archive) |
| **Edge concerns** | Pages (frontend), Workers, AI Gateway BYOK, Cache Reserve, Email Routing |
| **Total at 10k DAU** | **~$31/mo (~7% draw)** |

### 2.4 AWS ($1k AWS Activate)

| Role | Features delegated |
|---|---|
| **Tier-1 embed + rerank** | `embed[0]`, `rerank[0]` (Bedrock Cohere — sole Bedrock use) |
| **Tier-1 blob (sole)** | `blob[0]` (S3 = sole object store) |
| **Tier-1 async queue** | `async_queue[0]` (SQS + Lambda) |
| **Tier-4 chat reserve** | `english_rag_chat[3]` (Claude Haiku via Bedrock — default off; activate only if 3 prior tiers all degrade) |
| **Tier-5 TTS** | `tts[4]`, `voice[5]` (Polly Neural, post-#337 if Indic ElevenLabs falls through) |
| **Backend host** | App Runner us-west-2 (canonical backend), SES |
| **Total at 10k DAU** | **~$62/mo (~75% draw, mitigated)** |

### 2.5 Auxiliary providers (live OUTSIDE the 4 clouds, own credit pools)

These are not part of the 4-cloud delegation but are routed by the
dispatcher. Each has its own credit/free pool — **none consumes
AWS/Azure/CF/GCP credit**.

| Provider | Used in chains | Credit pool | Status |
|---|---|---|---|
| `sarvam` | `assamese_rag_chat`, `assamese_content`, `translate` ONLY | Sarvam Startup credit | PENDING |
| `elevenlabs` | `tts`, `voice` | $4k startup credit | PENDING |
| `deepgram` | `stt`, `voice` | $1k startup credit | PENDING |
| `assemblyai` | `stt`, `voice` | $50 instant signup | LANDED |
| `cartesia` | `tts` | free credit | LANDED |
| `voyage_ai` | `embed`, `rerank` | free trial | LANDED |
| `pinecone` | `vector_retrieve` | Starter free → $5k Startup reserved | LANDED |
| `perplexity` | `search_rag` | free tier | LANDED |
| `exa_ai` | `live_search` | free tier | LANDED |
| `tavily` | `live_search` | free tier | LANDED |
| `momento` | `cache` | free 5 GB / 5M req | LANDED |
| `axiom` / `sentry` | `apm` | free tiers | LANDED |
| **Total cash exposure today** | — | **$0** (all PENDING credits have free-tier coverage) |

> `bedrock_cohere` lives inside AWS Activate, so it counts as AWS, not auxiliary.

---

## §3 What changed vs the attached snippet

| Change | Where | Why |
|---|---|---|
| `bedrock` reserved as Tier-4 chat only (default off) | `english_rag_chat` | Bedrock spend is reserved for Cohere embed+rerank (the high-value AWS use); Claude/Titan chat duplicates Vertex+Azure capacity |
| Sarvam **removed** from `tts`, `voice`, `stt`, `vision` | snippet had `sarvam` only in chat already; this doc enforces the rule | Sarvam scoped to Indic chat + translate only (per user directive) |
| `assemblyai` promoted, `deepgram` added back to `stt[0]` | `stt`, `voice` | Strategic chain has Deepgram primary (PENDING $1k credit); AssemblyAI is the LANDED Tier-2 |
| `polly` added as Tier-5 of `tts` / `voice` | `tts`, `voice` | Post-#337 fallback per AWS Activate plan |
| `vertex_vision` split out from `vertex` | `vision` | Cloud Vision API is a distinct GCP product from Gemini multimodal |
| `vertex_discovery` added | `search_rag`, `vector_retrieve` | Already in the strategic plan; missing from snippet |
| `bedrock_cohere` keyed separately from `bedrock` | `embed`, `rerank` | Distinguishes "Bedrock-via-Cohere (in scope)" from "Bedrock-via-Claude (Tier-4 reserve only)" |
| `vector_retrieve`, `cache`, `blob`, `cron`, `async_queue`, `apm` keys added | new top-level keys | Snippet was inference-only; these are platform-level dispatch decisions that also belong in PROVIDER_PRIORITY |
| Cerebras + Groq absent | (already absent in snippet) | Confirmed — no chat chain references them |

---

## §4 Editing policy (binding)

Same gate as `feature-deep-dive.md` §7.2:

1. Credit grant LANDED (status in `credit-applications.md` → `landed`).
2. ≥ 7 days of staging soak.
3. Code review approval from architect.
4. Atomic deploy; revert plan tested.
5. Axiom audit log `provider.priority.edit` with diff.

> **NEVER** edit on aspirational credit grants. The §1 dict is the
> *target* state; the live `artifacts/syrabit-backend/config.py`
> currently carries legacy `openai`/`anthropic`/`mistral_azure`
> entries that are flagged in `feature-deep-dive.md` §7.3 drift
> register and must be removed via the §7.4 migration plan.

---

## §5 Reconciliation against companion docs

| Doc | What this doc must reconcile with |
|---|---|
| `cloud-allocation-plan.md` | §2.1–§2.4 per-cloud roles must match the 4-cloud allocation |
| `cloud-service-breakdown.md` | every provider key in §1 must appear in the per-cloud service inventory |
| `auxiliary-providers-delegation.md` | §2.5 auxiliary table must match the auxiliary-doc roles |
| `feature-to-provider-mapping-detailed.md` | per-feature chains in that doc must map onto §1 keys |
| `feature-deep-dive.md` §7.3 drift register | every drift row must point to a §1 entry that's the target state |
| `feature-to-provider-audit.md` | per-pool $ totals (§2.1–§2.4) must reconcile with audit §2 |
| `credit-applications.md` | every PENDING credit in §2.5 must have a tracked application |

---

## §6 Re-running this map

```bash
# 1. Diff §1 against live config.py
rg -n "PROVIDER_PRIORITY" artifacts/syrabit-backend/config.py -A 200

# 2. Confirm Cerebras/Groq absence
rg -n "cerebras|groq" artifacts/syrabit-backend/config.py
# expect: 0 matches in PROVIDER_PRIORITY block

# 3. Confirm Sarvam scope
rg -n "sarvam" artifacts/syrabit-backend/config.py
# expect: only in assamese_rag_chat, assamese_content, translate

# 4. Per-pool reconciliation
# diff §2.1–§2.4 line-item totals against feature-to-provider-audit.md §2
```

> Re-run any time a provider is added/removed or a chain is re-ordered.
