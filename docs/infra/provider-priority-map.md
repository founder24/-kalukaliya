# PROVIDER_PRIORITY (canonical)

Mirrors the attached snippet format. Excludes Cerebras + Groq.
Sarvam scoped to Assamese chat / content / translate only — removed
from voice / TTS / STT / vision. Delegation across the 4 strategic
clouds (AWS / Azure / Cloudflare / GCP) annotated inline.

```python
PROVIDER_PRIORITY = {

    # Conversational RAG chat — English
    "english_rag_chat": [
        "vertex",          # Gemini 2.5 Flash (Google Cloud for Startups, $2k)
        "azure_openai",    # GPT‑4.1‑mini via Microsoft Founders Hub ($2.5k)
        "workers_ai",      # Cloudflare gpt-oss-20b / llama-3.3-70b (CF for Startups, $5k)
        "bedrock",         # AWS Bedrock Claude Haiku — Tier‑4 reserve, default off (AWS Activate, $1k)
    ],

    # Conversational RAG chat — Assamese
    # Sarvam scoped to this chain only. Vertex Gemini is current primary.
    "assamese_rag_chat": [
        "vertex",          # Gemini 2.5 Flash, Indic‑tuned prompt (Google Cloud)
        "sarvam",          # Sarvam‑M Indic‑native (Sarvam credit, PENDING)
        "azure_openai",    # GPT‑4.1‑mini translate‑then‑answer (Founders Hub)
        "workers_ai",      # Cloudflare IndicTrans2 + gpt-oss-20b (CF)
    ],

    # Long-form content generation (MCQ, notes, admin pipelines)
    "content": [
        "azure_openai",    # GPT‑4.1‑mini deployment "syrabit-quiz" / "syrabit-cards" (Founders Hub)
        "vertex",          # Gemini 2.5 Flash (Google Cloud)
        "workers_ai",      # Cloudflare gpt-oss-20b (CF)
    ],

    # Assamese content (translated/adapted from English)
    "assamese_content": [
        "vertex",          # Gemini 2.5 Flash translate+adapt (Google Cloud)
        "sarvam",          # Sarvam‑M Indic‑native (Sarvam credit, PENDING)
        "workers_ai",      # Cloudflare IndicTrans2 (CF)
    ],

    # Text-to-speech  (Sarvam REMOVED — Assamese chat only)
    "tts": [
        "elevenlabs",      # eleven_multilingual_v2 (ElevenLabs $4k credit, PENDING)
        "vertex",          # Google Cloud TTS Neural2, en/as/hi/bn voices (Google Cloud)
        "cartesia",        # Cartesia Sonic (free credit standby)
        "workers_ai",      # Cloudflare MeloTTS (CF)
        "polly",           # AWS Polly Neural — post‑#337 fallback (AWS Activate)
    ],

    # Speech-to-text
    "stt": [
        "deepgram",        # nova‑3‑general (Deepgram $1k credit, PENDING)
        "assemblyai",      # best model, dual_channel + punctuate (LANDED $50)
        "workers_ai",      # Cloudflare whisper-large-v3-turbo (CF)
        "vertex",          # Google Cloud Speech Chirp asia‑south1 (Google Cloud)
    ],

    # Real-time voice pipeline (streaming STT → LLM → TTS)  (Sarvam REMOVED)
    "voice": [
        "deepgram",        # STT leg primary (Deepgram credit, PENDING)
        "assemblyai",      # STT leg fallback (LANDED)
        "elevenlabs",      # TTS leg primary (ElevenLabs $4k, PENDING)
        "vertex",          # TTS leg fallback (Google Cloud TTS Neural2)
        "workers_ai",      # Cloudflare full‑edge fallback (Whisper + MeloTTS)
        "polly",           # AWS Polly Neural last resort (AWS Activate)
    ],

    # Text embeddings (RAG ingestion + query)
    "embed": [
        "bedrock_cohere",  # cohere.embed-multilingual-v3 via Bedrock us‑west‑2 (AWS Activate)
        "voyage_ai",       # voyage‑3‑multilingual (free trial, LANDED)
        "vertex",          # text-embedding-004 (Google Cloud) — 768‑dim, requires re‑index
        "workers_ai",      # Cloudflare bge-m3 edge (CF)
    ],

    # Semantic reranking (post-retrieval scoring)
    "rerank": [
        "bedrock_cohere",  # cohere.rerank-multilingual-v3 via Bedrock (AWS Activate)
        "voyage_ai",       # rerank‑2 multilingual (free trial)
        "workers_ai",      # Cloudflare bge-reranker-base (CF)
        # graceful degrade: skip rerank, return Pinecone topK as-is
    ],

    # Translation (English ↔ Assamese / Indic)  (Sarvam allowed here)
    "translate": [
        "workers_ai",      # Cloudflare indictrans2-en-indic-1b (CF)
        "vertex",          # Cloud Translation v3 + Gemini polish (Google Cloud)
        "sarvam",          # Sarvam‑Translate Indic‑first (Sarvam credit, PENDING)
        "azure_openai",    # GPT‑4.1‑mini translate prompt (Founders Hub)
    ],

    # Vision / OCR / image analysis
    "vision": [
        "vertex",          # Gemini 2.5 Flash multimodal asia‑south1 (Google Cloud)
        "vertex_vision",   # Cloud Vision API DOCUMENT_TEXT_DETECTION (Google Cloud)
        "workers_ai",      # Cloudflare llava-1.5-7b-hf (CF)
    ],

    # Prompt safety / content moderation
    "safety": [
        "vertex",          # Gemini built‑in safety + RAI categories (Google Cloud)
        "workers_ai",      # Cloudflare llama-guard-3-8b (CF)
    ],

    # RAG-grounded web search (answers with citations)
    "search_rag": [
        "vertex_discovery",# Vertex Discovery Engine over CMS+web corpus (Google Cloud)
        "perplexity",      # Citation‑rich RAG answers (free tier)
        "workers_ai",      # Cloudflare gpt-oss-20b with retrieved context (CF)
    ],

    # Live / real-time web search (freshness‑critical queries)
    "live_search": [
        "exa_ai",          # Neural search (free tier)
        "tavily",          # Structured results (free tier)
        "workers_ai",      # Cloudflare Browser Rendering + edge fetch (CF)
    ],

    # Vector retrieval
    "vector_retrieve": [
        "pinecone",        # Starter free → Pinecone Startup ($5k reserved)
        "vertex_vector",   # Vertex Vector Search / Matching Engine (Google Cloud)
        "cf_vectorize",    # Cloudflare Vectorize edge index (CF)
        "vertex_discovery",# Vertex Discovery Engine semantic search (Google Cloud)
    ],

    # Cache (sessions, rate limit, JWT blacklist, prompt cache)
    "cache": [
        "azure_redis",     # Azure Cache for Redis Basic C0 centralindia (Founders Hub, $16/mo)
        "momento",         # Momento Cache HTTP API (free 5 GB / 5M req)
        "cf_kv",           # Cloudflare Workers KV / Durable Objects (CF)
        "mongo_atomic",    # Mongo find_and_modify atomic ops only (last resort)
    ],

    # Object storage (sole-store; no multi-cloud writes)
    "blob": [
        "s3",              # AWS S3 syrabit-prod-* — SOLE OBJECT STORE (AWS Activate)
        "cf_r2",           # Cloudflare R2 cold archive only (CF)
    ],

    # Cron / scheduled jobs (CANONICAL: Azure Container Apps Jobs)
    "cron": [
        "azure_container_apps_jobs",  # KEDA cron triggers, scale‑to‑zero (Founders Hub)
    ],

    # Async / queued workloads
    "async_queue": [
        "aws_sqs_lambda",  # AWS SQS standard queue + Lambda consumers (AWS Activate)
    ],

    # APM / observability (CANONICAL: Azure App Insights)
    "apm": [
        "azure_app_insights", # Central APM, distributed tracing (Founders Hub)
        "axiom",              # Long‑term log retention (free 0.5 TB/mo)
        "sentry",             # Error tracking (free tier)
    ],
}
```

---

## Cloud delegation summary

| Cloud | Owns | $/mo at 10k DAU |
|---|---|---:|
| **GCP** (`vertex`, `vertex_vision`, `vertex_vector`, `vertex_discovery`) | Tier‑1 chat (en+as), vision, safety, grounded search; Tier‑2 TTS/STT/translate | ~$150 |
| **Azure** (`azure_openai`, `azure_redis`, `azure_container_apps_jobs`, `azure_app_insights`) | Tier‑1 content, cache primary, cron (all jobs), APM central | ~$87 |
| **Cloudflare** (`workers_ai`, `cf_kv`, `cf_r2`, `cf_vectorize`) | Tier‑1 translate; edge fallback for everything else | ~$31 |
| **AWS** (`bedrock_cohere`, `s3`, `aws_sqs_lambda`, `polly`, `bedrock`) | Tier‑1 embed/rerank, blob (sole), async queue, backend host | ~$62 |
| **Auxiliary** (`sarvam`, `elevenlabs`, `deepgram`, `assemblyai`, `cartesia`, `voyage_ai`, `pinecone`, `perplexity`, `exa_ai`, `tavily`, `momento`, `axiom`, `sentry`) | own credit pools, $0 against 4‑cloud math | $0 |
| **Total** | — | **$339 / $0 cash** |

## Excluded providers

- `cerebras` — removed from all chat chains
- `groq` — removed from all chat chains
- `sarvam` in `tts`, `voice`, `stt`, `vision` — removed; Sarvam scope = Assamese chat / content / translate only
