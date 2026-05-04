> **v3 SUPERSEDES (2026-05-04):** the canonical infra spec is
> [`infra/per-cloud-feature-delegation.md`](../../infra/per-cloud-feature-delegation.md)
> + [`infra/provider-priority-map.md`](../../infra/provider-priority-map.md)
> + [`infra/credit-burn-runbook.md`](../../infra/credit-burn-runbook.md).
> Provider removals (OpenAI, Anthropic, Bedrock, Stripe, Quge5, Resend,
> Grok, Railway, DigitalOcean) are tracked in Task #347. If anything
> below disagrees with v3, the v3 docs win.
>
> ---
>
> **Authority sync (2026-05-04):** `docs/infra/provider-priority-map.md`
> is the canonical PROVIDER_PRIORITY map. Binding constraints carried
> across every plan in this folder:
> 1. **Cerebras + Groq** — absent from every chain.
> 2. **Sarvam** — only in `assamese_rag_chat`, `assamese_content`, `translate` (not in tts/voice/stt/vision).
> 3. **Bedrock direct (Claude / Titan / Jamba)** — removed from chat; **Bedrock is Cohere‑only** (embed + rerank, keyed `bedrock_cohere`).
> 4. **`embed`** — Cohere via Bedrock → Voyage → CF Workers AI bge-m3 (Vertex `text-embedding-004` removed).
> 5. **`rerank`** — Cohere via Bedrock → Voyage → CF bge-reranker-base.
> 6. **Pinecone** — THE RAG vector store of record (`syrabit-rag`, 1024-dim cosine, aws-us-west-2). Vertex Vector / CF Vectorize are Tier-2/3 fallback only.
> 7. **MongoDB Atlas** — canonical chat history (`conversations` collection) + canonical analytics + all application state (notes, flashcards, streaks, leaderboards, quizzes, CMS, SEO topics, push tokens, audit logs). Redis/Momento/CF KV are TTL cache only.
> 8. **AWS S3** — sole object store. CF R2 is cold archive only.
> 9. **Cron** — Azure Container Apps Jobs canonical (Founders Hub credit). DO cron used for backend-resident jobs after Task #333 observability rewire — see `feature-deep-dive.md` §7.3 drift register.
> 10. **APM** — Azure App Insights canonical sink; Axiom parallel for long-retention logs; CloudWatch for AWS-native alarms only.

# Detailed Feature → Provider Mapping

**Date:** 2026-05-04
**Companion to:** [`feature-to-provider-audit.md`](./feature-to-provider-audit.md) (the summary roll-up)
**Source of truth for chains:** the canonical strategic plan in
[`cloud-allocation-plan.md`](./cloud-allocation-plan.md) and
[`cloud-service-breakdown.md`](./cloud-service-breakdown.md). Where the
codebase currently routes to a different concrete model id than the
strategic plan, the **strategic** chain is shown here (it is the target
state); operational drift is flagged in §5.

---

## 0. Reading guide

Each feature row carries:

| Column | Meaning |
|---|---|
| **Handler** | File + route/loop name in `artifacts/syrabit-backend/` |
| **Trigger** | What initiates the call (HTTP, cron, webhook) |
| **Per-call sizing** | Tokens / chars / bytes / req-shape per invocation |
| **Volume at 10k DAU** | Calls per month |
| **Provider chain** | Tier-1 primary → Tier-2 → Tier-3 (model id per tier) |
| **Region per provider** | Concrete region string (matters for egress) |
| **Fallback trigger** | What demotes Tier-1 → Tier-2 in the dispatcher |
| **Credit pool draw** | $/mo at 10k DAU and which Tier-1 startup credit absorbs it |

> **Dispatcher rules:** every chain demotes on (a) any 5xx, (b) HTTP
> 429 rate-limit, (c) timeout > the per-feature SLA, or (d) provider
> health probe last-failure within rolling 60s window. Promotion back
> to Tier-1 happens after 5 consecutive successful health probes.
> Hard-coded in `artifacts/syrabit-backend/dispatcher.py`.

---

## 1. AI Learning & Chat

### 1.1 Streaming AI chat with RAG — `POST /api/ai/chat/stream`

| Column | Detail |
|---|---|
| Handler | `api/ai_chat.py` :: `chat_stream()` |
| Trigger | HTTP — student types a question in the chat UI |
| Per-call sizing | system 800 tok + RAG context 2.5k tok + user 200 tok + response 500 tok = **~4k tok / call** |
| Volume at 10k DAU | 10k DAU × 0.6 active × 4 chats × 30 days = **~720k chats/mo** |
| Tier-1 (PRIMARY) | **Vertex Gemini 2.5 Flash** (`gemini-2.5-flash`) — region `asia-south1`, SLA 8s p95 |
| Tier-2 (fallback) | **Azure OpenAI GPT-4.1-mini** (deployment `syrabit-chat`) — region `eastus2`, SLA 10s p95 |
| Tier-3 (last resort) | **CF Workers AI Llama-3.3-70B** (`@cf/meta/llama-3.3-70b-instruct-fp8-fast`) — region edge, SLA 12s p95 |
| Fallback triggers | Tier-1→2: 5xx / 429 / >12s / safety-block. Tier-2→3: same. |
| Credit pool draw | 720k × 4k tok = 2.88B tok/mo. Split 80/15/5 across tiers. **Vertex ~$95/mo, Azure ~$13/mo, CF ~$2/mo.** |
| Cache | Prompt-cache hash → **Azure Cache for Redis Basic C0** (5-min TTL on identical (chapter, question) pairs); ~30% hit rate ⇒ effective tokens ~2B/mo. |

### 1.2 Assamese chat mode (translate-then-embed) — `ensure_question_in_assamese()`

| Column | Detail |
|---|---|
| Handler | `api/ai_chat.py` :: `ensure_question_in_assamese()` (called inline before retrieval) |
| Trigger | Internal — fires when user query script ≠ Assamese but the namespace target is `as` |
| Per-call sizing | 50 tok in (English) → 80 tok out (Assamese) per translation; 80 tok in → 100 tok out per polish |
| Volume at 10k DAU | ~30% of Assamese-namespace chats = ~80k/mo translations + 80k polish |
| Tier-1 (PRIMARY) | **CF Workers AI IndicTrans2** (`@cf/google/indictrans2-en-indic-1b`) — region edge, SLA 2s p95 |
| Tier-2 | **Vertex Gemini 2.5 Flash** in translate mode — same region as 1.1 |
| Tier-3 | **Azure OpenAI GPT-4.1-mini** with translate system prompt |
| Tier-2 (Assamese chat answer fallback, distinct from translation) | **Sarvam-M** (Indic-tuned, **only used in Assamese chat chain**) — activates only if Sarvam credit lands; otherwise Vertex Gemini handles |
| Fallback triggers | edge AI 5xx / >5s timeout |
| Credit pool draw | 160k × ~150 tok avg = 24M tok/mo at edge. **CF ~$3/mo + Vertex ~$2/mo.** |

### 1.3 Grounded answer (strict-RAG) — `POST /api/edu/grounded-answer`

| Column | Detail |
|---|---|
| Handler | `api/edu_browser.py` :: `grounded_answer()` |
| Trigger | HTTP — student selects "answer from this article only" mode in reader |
| Per-call sizing | system 600 tok + page context capped at **10,000 chars (~2.5k tok)** + question 200 tok + response 400 tok = **~3.7k tok / call** |
| Volume at 10k DAU | ~80k calls/mo (only when student is in reader mode) |
| Tier-1 (PRIMARY) | **Vertex Gemini 2.5 Flash** with strict-grounding system prompt, `tool_config=ANY` for citation enforcement |
| Tier-2 | **Azure OpenAI GPT-4.1-mini** with parallel system prompt |
| Tier-3 | **CF Workers AI gpt-oss-20b** (`@cf/openai/gpt-oss-20b`) |
| Fallback triggers | same as 1.1; additionally Tier-1→2 if response fails citation-presence check (downstream validator in `grounded_answer.py`) |
| Credit pool draw | 80k × 3.7k tok = 296M tok/mo. **Vertex ~$12/mo, Azure ~$2/mo, CF ~$1/mo.** |

### 1.4 PDF → MCQ ingest + RAG indexing — `POST /api/admin/content/cms-documents/{doc_id}/process-rag`

| Column | Detail |
|---|---|
| Handler | `api/cms_sarvam_health.py` :: `process_rag()` (admin-only, async via `bg_tasks`) |
| Trigger | HTTP from admin CMS — fires when admin uploads a PDF lecture note |
| Per-call sizing | typical 30-page PDF = ~120 chunks of 500 words (100-word overlap) = ~60k tok embed input |
| Volume at 10k DAU | ~10 admin uploads/day × 30 days = **~300 PDF ingests/mo** |
| Tier-1 (PRIMARY: PDF parse) | **AWS Lambda** (`syrabit-pdf-parse` ARM64) — region `us-west-2`, 1024 MB / 30s timeout |
| Tier-1 (PRIMARY: embed) | **Cohere `embed-multilingual-v3` via AWS Bedrock** — region `us-west-2`, batch size 100 chunks |
| Tier-1 (PRIMARY: vector store) | **Pinecone Starter** index `syrabit-rag` (1024-dim, cosine) — region `aws-us-west-2` |
| Tier-2 (embed) | **Voyage `voyage-3-multilingual`** (free trial credit) |
| Tier-3 (embed) | **CF Workers AI bge-m3** (`@cf/baai/bge-m3`) |
| Fallback triggers | Bedrock 5xx / 429 / >15s; Pinecone upsert 5xx → retry 3× then queue to SQS for manual replay |
| Credit pool draw | 300 PDFs × 60k tok = 18M tok/mo embed. **AWS Bedrock ~$2/mo + Lambda $0 (within free) + Pinecone $0.** |

### 1.5 Vision OCR for chat input — `POST /api/ai/ocr-image`

| Column | Detail |
|---|---|
| Handler | `api/ai_chat.py` :: `ocr_image()` |
| Trigger | HTTP — student attaches an image to the chat (e.g., handwritten problem) |
| Per-call sizing | image up to **20 MB** (`MAX_IMAGE_SIZE`); typical 1–4 MB; ~1k tok response |
| Volume at 10k DAU | ~5% of chats include image = **~36k OCR calls/mo** |
| Tier-1 (PRIMARY) | **Vertex Gemini 2.5 Flash multimodal** (`gemini-2.5-flash` with image part) — region `asia-south1` |
| Tier-2 | **GCP Cloud Vision API** `DOCUMENT_TEXT_DETECTION` — region `global` |
| Tier-3 | **CF Workers AI llava** (`@cf/llava-hf/llava-1.5-7b-hf`) — region edge |
| Fallback triggers | same as 1.1 + image-too-large (>20MB) returns 413 with no fallback |
| Credit pool draw | 36k × ~3k tok-equivalent = ~108M tok/mo. **Vertex ~$3/mo + Cloud Vision ~$0.5/mo.** |

### 1.6 Conversation history persistence

| Column | Detail |
|---|---|
| Handler | rolling — every chat handler calls `mongo.conversations.insert_one()` on response complete |
| Trigger | implicit at end of every chat stream |
| Per-call sizing | ~5 KB per message × 2 messages per turn = 10 KB / chat |
| Volume at 10k DAU | 720k chats × 10 KB = **~7.2 GB writes/mo** (cumulative DB ~50 GB after 1 yr) |
| Tier-1 (PRIMARY) | **MongoDB Atlas M0** (free, 512 MB) → migrate to **M2** (~$9/mo) once size > 512 MB |
| Tier-2 | None (DB is single-source-of-truth); read replicas via M10 dedicated when traffic > 100k DAU |
| Credit pool draw | **Mongo $0 (M0) → $9/mo (M2)** — Atlas $500 startup credit covers 55+ months at M2 |

### 1.7 Session + rate limit + JWT blacklist — `cache.py` (used by every authenticated route)

| Column | Detail |
|---|---|
| Handler | `cache.py` (called by `auth.py` middleware on every request) |
| Trigger | every authenticated HTTP request (~100 req/user/day) |
| Per-call sizing | 3 cache ops/req (session lookup + rate INCR + JWT blacklist check), 200 bytes each |
| Volume at 10k DAU | 10k DAU × 100 req × 3 ops × 30 days = **~90M ops/mo** |
| Tier-1 (PRIMARY) | **Azure Cache for Redis Basic C0** — region `centralindia` (matches Azure Container Apps); 250 MB cache, full Redis protocol |
| Tier-2 | **Momento Cache** (HTTP API) — region `us-east-1` |
| Tier-3 | **CF KV / Durable Objects** — edge, eventually consistent |
| Tier-4 (graceful degrade) | **Mongo `find_and_modify`** — atomic ops only |
| Fallback triggers | Azure Redis connection-pool exhaustion / Redis CLUSTER-DOWN / >100ms p99 latency for 60s |
| Credit pool draw | **Azure $16/mo (within Azure pool, drawn 37% → 45%); Momento $0 (free 5GB/5M req); CF $0.** |

---

## 2. Educational Study Tools

### 2.1 MCQ / Quiz generator — `POST /api/edu/quiz/generate`

| Column | Detail |
|---|---|
| Handler | `api/edu_study.py` :: `generate_quiz()` |
| Trigger | HTTP — student opens a chapter quiz |
| Per-call sizing | system 600 tok + chapter summary 1.5k tok + 24-question generation 6k tok response = **~8k tok one-shot** then **0 tok for next 23 fetches** (sampled from cached pool) |
| Volume at 10k DAU | 10k DAU × 0.4 × 1 quiz × 30 days = **~120k quiz fetches/mo**, but only ~5k unique chapters ⇒ **~5k generations/mo** (24× cache amplification) |
| Tier-1 (PRIMARY) | **Azure OpenAI GPT-4.1-mini** (deployment `syrabit-quiz`) — region `eastus2` |
| Tier-2 | **Vertex Gemini 2.5 Flash** with same prompt — region `asia-south1` |
| Tier-3 | **CF Workers AI gpt-oss-20b** |
| Cache | Mongo `quiz_pools` collection keyed `(chapter_id, version)`, 30-day TTL |
| Fallback triggers | same as 1.1 + JSON-parse-fail on response |
| Credit pool draw | 5k × 8k tok = 40M tok/mo. **Azure ~$5/mo + Vertex ~$1/mo.** |

### 2.2 Notebook + AI summaries — `GET/POST /api/edu/notes`

| Column | Detail |
|---|---|
| Handler | `api/edu_study.py` :: `notes_crud()` and `notes_summarize()` |
| Trigger | HTTP — student saves a note (auto-summary on save if note > 200 words) |
| Per-call sizing | summary: 800 tok in + 200 tok out per save |
| Volume at 10k DAU | 10k × 0.2 × 2 notes × 30 = **~120k summaries/mo** |
| Tier-1 (PRIMARY: storage) | **MongoDB Atlas** `notes` collection |
| Tier-1 (PRIMARY: summary) | **Vertex Gemini 2.5 Flash** in summarize mode |
| Tier-2 | **Azure OpenAI GPT-4.1-mini** |
| Credit pool draw | 120k × 1k tok = 120M tok/mo. **Vertex ~$4/mo + Mongo $0.** |

### 2.3 Flashcards + spaced repetition — `POST /api/edu/flashcards/build`

| Column | Detail |
|---|---|
| Handler | `api/edu_study.py` :: `build_flashcards()` (build) + `flashcard_review()` (SM-2 schedule) |
| Trigger | HTTP — student requests flashcards from a chapter; review handler runs daily |
| Per-call sizing | build: ~3k tok in + ~4k tok out per chapter; review: 0 LLM tokens (pure DB + cache) |
| Volume at 10k DAU | builds: ~8k chapters × 1 build/student × 30 = **~10k builds/mo** (deduped); reviews: ~600k/mo |
| Tier-1 (PRIMARY: build) | **Azure OpenAI GPT-4.1-mini** (deployment `syrabit-cards`) |
| Tier-2 | **Vertex Gemini 2.5 Flash** |
| Tier-1 (review state) | **Azure Cache for Redis** (review-due ZSET keyed by `user_id`) + Mongo `flashcards` (canonical) |
| Cache key | `flashcard:{chapter_id}:{content_hash}:{user_id}` |
| Credit pool draw | 10k × 7k tok = 70M tok/mo. **Azure ~$8/mo + Vertex $0 (cold) + Mongo/Redis $0 (within above pools).** |

### 2.4 Edu reader + URL allowlist — `POST /api/edu/reader/fetch`

| Column | Detail |
|---|---|
| Handler | `api/edu_browser.py` :: `reader_fetch()` |
| Trigger | HTTP — student pastes an external URL to read in distraction-free reader |
| Per-call sizing | URL safety check (200 byte req) + HTML fetch (avg 50 KB) + clean-extract (in-process) |
| Volume at 10k DAU | ~50k reader fetches/mo |
| Tier-1 (PRIMARY: safety) | **GCP Web Risk API** lookup types `SOCIAL_ENGINEERING, MALWARE, UNWANTED_SOFTWARE` |
| Tier-1 (PRIMARY: fetch) | **CF Worker** (`reader-fetch-proxy`) — fetches HTML server-side, strips JS/iframes |
| Tier-2 (safety) | local hash blocklist (Mongo) |
| Cache | Mongo `reader_cache` collection, 24h TTL (`READER_CACHE_TTL`) |
| Credit pool draw | Web Risk free 10k/mo; cache hit ~80% ⇒ ~10k API calls/mo within free quota. **$0.** |

### 2.5 Streaks + leaderboard — `GET /api/edu/flashcards/streak`

| Column | Detail |
|---|---|
| Handler | `api/edu_study.py` :: `streak_get()` + `streak_increment()` |
| Trigger | HTTP — read on dashboard load; write on study-session-end |
| Per-call sizing | ZRANGEBYSCORE for top-100, ~200 byte response |
| Volume at 10k DAU | reads ~300k/mo + writes ~600k/mo |
| Tier-1 (PRIMARY) | **Azure Cache for Redis** ZSET `leaderboard:{cohort}` + Mongo `streaks` (canonical) |
| Tier-2 | Mongo aggregation `$sortByCount` (slower but no cache dependency) |
| Credit pool draw | within Azure Redis $16/mo (already counted in 1.7) |

### 2.6 Vector retrieval (per study query) — `retrievers/pinecone.py`, `retrievers/vertex.py`

| Column | Detail |
|---|---|
| Handler | called inline by `ai_chat.py`, `grounded_answer.py`, `seo_engine.py` |
| Trigger | every RAG-using call (chat, grounded-answer, internal-linker) |
| Per-call sizing | embed 200 tok query → 1024-dim vector; Pinecone topK=5 (~50ms p95) |
| Volume at 10k DAU | ~720k chats + ~80k grounded + ~2k SEO = **~800k retrievals/mo** |
| Tier-1 (PRIMARY) | **Pinecone Starter** index `syrabit-rag` (1024-dim, cosine) — region `aws-us-west-2` |
| Tier-2 | **Vertex Vector Search / Matching Engine** (`projects/.../indexEndpoints/syrabit-rag`) — `asia-south1`, cold by default |
| Tier-3 | **CF Vectorize** index `syrabit-rag` — edge |
| Tier-4 | **Vertex Discovery Engine** data store (semantic search over CMS) |
| Fallback triggers | Pinecone 5xx / >200ms p99 / index-readiness flag false |
| Credit pool draw | Pinecone Starter free tier covers 100k vectors at 10k DAU. **$0 today; Pinecone Startup $5k reserved for Standard upgrade.** |

### 2.7 Embed pipeline — `embeddings/cohere.py`

| Column | Detail |
|---|---|
| Handler | called by `process-rag` (1.4) and `seo_engine.py` (1.4 + 1.4 reindex) |
| Trigger | PDF ingest, weekly reindex cron, SEO topic clustering |
| Per-call sizing | batch of 100 chunks × 500 tok = 50k tok per batch call |
| Volume at 10k DAU | ~18M tok/mo (PDFs) + ~10M tok/mo (weekly reindex) = **~28M tok/mo** |
| Tier-1 (PRIMARY) | **Cohere `embed-multilingual-v3` via AWS Bedrock** (`cohere.embed-multilingual-v3`) — region `us-west-2`, IAM action `bedrock:InvokeModel` |
| Tier-2 | **Voyage `voyage-3-multilingual`** — region `us-east-1` (free trial credit) |
| Tier-3 | **CF Workers AI bge-m3** — edge |
| Credit pool draw | 28M tok × $0.0001/1k = ~$3/mo embed. Combined with rerank below: **~$21/mo on AWS Activate ($1k pool).** |

### 2.8 Rerank pipeline — `embeddings/rerank.py`

| Column | Detail |
|---|---|
| Handler | called by RAG retrievers post-Pinecone for re-ranking topK=20 → top-5 |
| Trigger | every grounded-answer + ~30% of chat (when retrieval confidence < 0.7) |
| Per-call sizing | rerank 20 candidates × 500 tok each = 10k tok per call |
| Volume at 10k DAU | ~80k grounded + 250k chat-with-rerank = **~330k rerank calls/mo** |
| Tier-1 (PRIMARY) | **Cohere `rerank-multilingual-v3.0` via Bedrock** — region `us-west-2` |
| Tier-2 | **Voyage rerank-2** — region `us-east-1` (free trial) |
| Tier-3 | graceful degrade — return Pinecone topK=5 directly without rerank |
| Credit pool draw | rerank ~$18/mo (rolled into the $21 Bedrock-Cohere AWS Activate line above) |

---

## 3. Voice & Accessibility

### 3.1 Read-Aloud (TTS) — English — `POST /api/voice/tts`

| Column | Detail |
|---|---|
| Handler | `api/voice.py` :: `tts()` (English branch when `lang=en`) |
| Trigger | HTTP — student taps "🔊 Read aloud" on a passage |
| Per-call sizing | avg 800 chars / passage; output ~10s audio at 22kHz mono |
| Volume at 10k DAU | 10k × 0.3 × 2 reads × 30 = **~180k TTS calls/mo English** = ~144M chars/mo |
| Tier-1 (PRIMARY) | **ElevenLabs** `eleven_multilingual_v2` voice `Rachel` (PENDING $4k credit) — region multi |
| Tier-2 | **GCP Cloud TTS Neural2** voice `en-US-Neural2-F` — region `global` |
| Tier-3 | **Cartesia Sonic** (free credit standby) |
| Tier-4 | **CF Workers AI MeloTTS** (`@cf/myshell-ai/melotts`) |
| Tier-5 | **AWS Polly Neural** (post-#337, paid) |
| Fallback triggers | ElevenLabs 5xx / 429 / >5s timeout / monthly char-cap reached |
| Credit pool draw | 144M chars/mo. **$0 today** (ElevenLabs free 10k chars + GCP free 4M chars × Neural2 $16/M = $24/mo within Vertex pool if ElevenLabs credit lands). **Worst case (no ElevenLabs credit, GCP-only): $24/mo within Vertex pool.** |

### 3.2 Read-Aloud (TTS) — Indic (as/hi/bn) — `POST /api/voice/tts` (Indic branch)

| Column | Detail |
|---|---|
| Handler | `api/voice.py` :: `tts()` (Indic branch when `lang ∈ {as, hi, bn}`) |
| Trigger | HTTP — Indic text passage Read-Aloud |
| Per-call sizing | avg 500 chars / passage (Indic scripts denser per character) |
| Volume at 10k DAU | ~60k Indic TTS calls/mo = ~30M chars/mo |
| Tier-1 (PRIMARY) | **GCP Cloud TTS Neural2** voices: `as-IN-Wavenet-A`, `hi-IN-Neural2-A`, `bn-IN-Wavenet-A` — region `asia-south1` |
| Tier-2 | **CF Workers AI MeloTTS** (Indic models) |
| Tier-3 | **AWS Polly Neural** Indic voices (post-#337, paid) |
| Fallback triggers | GCP TTS 5xx / Indic voice not-found / >5s timeout |
| Credit pool draw | 30M chars/mo. GCP free 4M + Neural2 $16/M × 26M = **~$6/mo within Vertex pool** (Sarvam removed from this chain — Sarvam reserved for Assamese chat only). |

### 3.3 STT (audio → text) — `POST /api/voice/stt`

| Column | Detail |
|---|---|
| Handler | `api/voice.py` :: `stt()` |
| Trigger | HTTP — student records voice query |
| Per-call sizing | avg 8s audio @ 16kHz mono = ~256 KB; response ~120 chars |
| Volume at 10k DAU | 10k × 0.1 × 3 voice queries × 30 = **~90k STT calls/mo** = ~12 hours audio/mo |
| Tier-1 (PRIMARY) | **Deepgram `nova-3-general`** (PENDING $1k credit) — region `us-east-1` |
| Tier-2 | **AssemblyAI** (`best` model, dual-channel + punctuate) — region `us-west-2` |
| Tier-3 | **CF Workers AI Whisper** (`@cf/openai/whisper-large-v3-turbo`) — edge |
| Tier-4 | **GCP Cloud Speech Chirp** (`asia-south1`) |
| Fallback triggers | Deepgram >5s timeout / 5xx / 429 |
| Credit pool draw | 12 hr audio × $0.0043/min = ~$3/mo on Deepgram (within $1k credit if it lands; **$0 today** on free $200 starter). |

### 3.4 Two-leg voice pipeline (STT → LLM → TTS) — `POST /api/voice/voice`

| Column | Detail |
|---|---|
| Handler | `api/voice.py` :: `voice_pipeline()` |
| Trigger | HTTP — full voice conversational turn (mic press → spoken answer) |
| Concurrency | semaphore-gated `VOICE_CONCURRENCY_LIMIT = 50` with `asyncio.gather` for parallel STT+LLM streaming |
| Per-call sizing | sums of 1.1 + 3.1 + 3.3 |
| Volume at 10k DAU | ~30k full voice turns/mo (subset of STT volume that triggers LLM round-trip) |
| Provider chain | concurrent STT (3.3) + chat (1.1) + TTS (3.1) chains as above |
| Credit pool draw | (rolled into 1.1 + 3.1 + 3.3) — no new line |

---

## 4. SEO Automation & Content Engine

### 4.1 Topic discovery + clustering — `POST /api/topics`

| Column | Detail |
|---|---|
| Handler | `api/seo_engine.py` :: `discover_topics()` |
| Trigger | HTTP from admin OR daily cron `topic-discovery` (`0 2 * * *`) |
| Per-call sizing | per run: 500 candidate topics × 200 tok cluster prompt + Pinecone topK lookups |
| Volume at 10k DAU | 1 run/day × 30 days = 30 runs/mo |
| Tier-1 (PRIMARY: keyword source) | **Bing Webmaster Keyword API** (`/v7.0/keywords`) — free quota 10k req/mo |
| Tier-1 (PRIMARY: cluster) | **Vertex Gemini 2.5 Flash** with cluster prompt — `asia-south1` |
| Tier-1 (PRIMARY: dedup) | **Pinecone** semantic dedup against existing topic index |
| Tier-2 (cluster) | **Azure OpenAI GPT-4.1-mini** |
| Credit pool draw | 30 × 100k tok = 3M tok/mo. **Vertex ~$0.10/mo + Bing $0 + Pinecone $0.** Headline ~$4/mo includes upstream Trends data scrape. |

### 4.2 Internal linker (semantic) — `POST /api/admin/seo/internal-links/trigger`

| Column | Detail |
|---|---|
| Handler | `api/admin_seo_internal_linker.py` :: `trigger_internal_linker()` |
| Trigger | HTTP from admin OR daily cron `internal-linker` (`0 2 * * *`) |
| Per-call sizing | per page: rerank 20 candidate target pages × 500 tok + 1 Gemini call for anchor-text gen (200 tok) |
| Volume at 10k DAU | 1 run/day × 200 pages × 5 anchors = 1k anchor decisions/day |
| Tier-1 (PRIMARY: rerank) | **Cohere `rerank-multilingual-v3.0` via Bedrock** — `us-west-2` |
| Tier-1 (PRIMARY: anchor gen) | **Vertex Gemini 2.5 Flash** — `asia-south1` |
| Tier-1 (storage) | Mongo `internal_links` collection |
| Tier-2 (rerank) | Voyage rerank-2 |
| Credit pool draw | rolled into the §2.8 rerank pool (~$18/mo of the $21/mo AWS Bedrock-Cohere line) + Vertex ~$1/mo |

### 4.3 SEO auto-publish background loop — `_seo_auto_publish_loop`

| Column | Detail |
|---|---|
| Handler | `api/seo_engine.py` :: `_seo_auto_publish_loop()` (long-running coroutine) |
| Trigger | poll every **5 min** (`_SEO_AUTO_PUBLISH_LOOP_SLEEP_S = 300`); fires on each topic's per-frequency window |
| Per full publish run | ~500 topics × 3 page types × 800 tok = **~1.2M tok / run**, plus Cohere embed for re-index, plus S3 PUT of HTML, plus CF Pages cache invalidate |
| Volume at 10k DAU | ~1 full run/day = ~36M tok/mo + S3 writes |
| Where it runs | **Azure Container Apps Jobs** (`seo-auto-publish` job, `*/15 * * * *` cron — supersedes the coroutine in production) |
| Tier-1 (chat) | Vertex Gemini 2.5 Flash |
| Tier-1 (embed) | Cohere via Bedrock |
| Tier-1 (HTML store) | S3 `s3://syrabit-prod-public` |
| Tier-1 (cache flush) | CF API `/zones/.../purge_cache` |
| Credit pool draw | **Vertex ~$5/mo + Bedrock ~$2/mo + S3 <$1 + CF $0 = ~$8/mo total** across 4 Tier-1 pools. |

### 4.4 IndexNow / sitemap ping — `POST /api/admin/indexnow/ping`

| Column | Detail |
|---|---|
| Handler | `api/admin_advanced.py` :: `indexnow_ping()` + cron `sitemap-indexnow-diff` (`0 * * * *`) |
| Trigger | HTTP from admin + hourly cron diffs sitemap and pings new/changed URLs |
| Per-call sizing | batched up to 10k URLs per IndexNow POST |
| Volume at 10k DAU | hourly × 30 days = ~720 batches/mo, ~100 URLs each |
| Tier-1 (PRIMARY) | **Bing IndexNow API** (`https://api.indexnow.org/indexnow`) — free |
| Tier-1 (PRIMARY) | **Yandex IndexNow** (`https://yandex.com/indexnow`) — free |
| Tier-1 (dispatch) | **CF Worker** `indexnow-dispatcher` — edge |
| Credit pool draw | **$0** (free APIs + CF credit) |

---

## 5. Admin & Infrastructure Tools

### 5.1 Unified admin dashboard — `GET /api/admin/dashboard/metrics`

| Column | Detail |
|---|---|
| Handler | `api/cms_sarvam_health.py` :: `dashboard_metrics()` |
| Trigger | HTTP — admin opens dashboard (poll every 30s while open) |
| Per-call sizing | ~12 parallel data sources via `asyncio.gather` |
| Volume | low — ~3 admin sessions × 100 polls/session = 300 calls/day |
| Data sources | **Mongo aggregations** (revenue, DAU) + **Azure App Insights** Kusto query (latency p95/p99) + **AWS CloudWatch** GetMetricStatistics (App Runner CPU/RAM) + **CF Analytics GraphQL** (edge req/s) |
| Credit pool draw | **$0** — within all 4 pools' read quotas |

### 5.2 Vertex routing panel — `AdminVertexPanel.jsx` + `/api/admin/ai/routing-config`

| Column | Detail |
|---|---|
| Handler | `api/admin_ai_routing.py` :: `get/set_routing_config()` |
| Trigger | HTTP from admin UI when toggling provider priority or model id |
| Provider chain | reads/writes `routing_config` doc in Mongo; live status pulled from Vertex `models.list`, Azure deployment list, CF AI Gateway logs API |
| Credit pool draw | **$0** |

### 5.3 Revenue / billing hub — `GET /api/admin/analytics/revenue`

| Column | Detail |
|---|---|
| Handler | `api/cms_sarvam_health.py` :: `analytics_revenue()` |
| Trigger | HTTP — admin views revenue tab (poll 5min) |
| Provider chain | Stripe `BalanceTransactions.list` + Razorpay `payments.all` → Mongo `revenue_events` persist → Axiom dataset `revenue` |
| Credit pool draw | free APIs + Mongo $0 + Axiom $0 |

### 5.4 R2 watchdog — `POST /api/admin/r2-storage-health/reset-watchdog`

| Column | Detail |
|---|---|
| Handler | `api/admin_r2_storage_health.py` :: `reset_watchdog()` + background prober |
| Trigger | cron every 2 min (`*/2 * * * *` → `alerting` ACA job) issues a HEAD/PUT/GET/DELETE round-trip against `r2://syrabit-prod-cold/_probe` |
| Provider chain | CF R2 list + put + get + delete → on probe-fail → Azure Logic Apps webhook → Telegram + email |
| Credit pool draw | **$0** within CF + Azure pools |

### 5.5 Cost alerts (per-cloud daily caps)

| Column | Detail |
|---|---|
| Handler | Azure Logic Apps + AWS CloudWatch alarms + Vertex Cloud Billing budgets |
| Trigger | breach of per-cloud daily $ cap |
| Thresholds | AWS daily > $5, Azure daily > $10, Vertex daily > $8, ElevenLabs daily > $15, Deepgram daily > $5 |
| Provider chain | breach → Telegram bot `@SyrabitOpsBot` + email `founder@syrabit.ai` |
| Credit pool draw | **$0** |

---

## 6. Background Workers & Scheduled Jobs (Azure Container Apps Jobs, KEDA cron triggers)

| Job name | Cron expression | What it does | Provider calls | Tier-1 pool(s) | Monthly $ at 10k DAU |
|---|---|---|---|---|---:|
| `seo-auto-publish` | `*/15 * * * *` | one full publish run/day; intermediate runs are no-ops if window not hit | Vertex Gemini + Cohere via Bedrock + S3 PUT + CF Pages purge | Vertex + AWS + CF | ~$8/mo (rolled into 4.3) |
| `internal-linker` | `0 2 * * *` | nightly scan of last-24h published pages → suggest+inject internal links | Cohere rerank via Bedrock + Vertex Gemini | AWS + Vertex | ~$1/mo (rolled into 2.8) |
| `topic-discovery` | `0 2 * * *` | extract+cluster new educational topics from CF Logs + Bing Trends | Vertex Gemini + Bing Keyword API + Pinecone dedup | Vertex + Pinecone | ~$0.10/mo |
| `bing-submit-daily` | `0 4 * * *` | daily submit of new sitemap to Bing Webmaster | Bing Webmaster API | (free) | $0 |
| `seo-weekly-digest` | `30 3 * * 1` | weekly cost + traffic + ranking digest → SES email to founder | CloudWatch + Azure Cost Mgmt + Vertex Billing → SES | AWS + Azure + Vertex | $0 |
| `exam-reminder` | `*/5 * * * *` | reminder push notification 24h/1h before each user's saved exam dates | Mongo read + CF Worker push dispatch | Mongo + CF | $0 |
| `push-prune` | `0 3 * * *` | nightly prune of expired push tokens | Mongo update | Mongo | $0 |
| `sitemap-indexnow-diff` | `0 * * * *` | hourly sitemap diff → IndexNow batch ping | Bing IndexNow + Yandex IndexNow + CF Worker | CF | $0 |
| `grounded-recall-nightly` | `0 2 * * *` | RAG accuracy benchmark for en/as/hi/bn (200 questions per lang) | Pinecone retrieve + Vertex Gemini eval + Mongo persist | Vertex + Pinecone + Mongo | ~$2/mo |
| `alerting` | `*/2 * * * *` | health probes for App Runner, Azure Container Apps, R2, Vertex, ElevenLabs, Deepgram | HEAD/GET probes + Telegram alerts | (within all pools) | $0 |
| `unified-logs-cf-pull` | `*/15 * * * *` | pull last-15-min CF Logs via GraphQL into Mongo for unified search | CF Logs GraphQL + Mongo write | CF + Mongo | $0 |
| `trustpilot-feed-alert` | hourly | poll Trustpilot reviews → Mongo persist → Telegram on sentiment shift | Trustpilot API + Mongo + Telegram | (free) + Mongo | $0 |
| `cf-waf-drift-cron-alert` | hourly | diff CF WAF rules against committed config → Telegram on drift | CF API + Telegram | CF | $0 |
| `bing-keyword-refresh` | daily | refresh SEO keyword corpus from Bing Webmaster | Bing Webmaster API + Pinecone upsert | (free) + Pinecone | $0 |
| `daily-mongo-s3-backup` | `0 3 * * *` | nightly mongodump → S3 (90-day Glacier lifecycle) | Azure cron → AWS Lambda → S3 PUT | Azure + AWS | ~$1/mo |
| `weekly-pinecone-reindex` | weekly Sun 02:00 IST | Mongo → Cohere embed via Bedrock → Pinecone upsert (full re-embed of changed docs) | Mongo read + Bedrock + Pinecone | AWS + Pinecone | (rolled into 2.7) |
| `weekly-cost-report-email` | weekly Mon 09:00 IST | per-cloud cost report → SES email | CloudWatch + Cost Mgmt + Vertex Billing → SES | (within pools) | $0 |
| `dead-endpoint-pruner` | weekly | hit all known routes → Sentry alert on > 1% 4xx/5xx | Sentry | (free) | $0 |

> **All cron jobs run on Azure Container Apps Jobs with KEDA triggers,
> scale-to-zero between runs.** Per-job container CPU/RAM costs are in
> the Azure pool $40/mo Container Apps line in the audit doc §2.3.

---

## 7. PROVIDER_PRIORITY + POOL_WEIGHTS in `artifacts/syrabit-backend/config.py`

> The runtime ordering of providers is owned by these two dicts. The
> tables in §1–§6 above describe the **target strategic chain**; if
> the runtime ordering disagrees, the runtime always wins until the
> next deploy. **Editing these dicts based on aspirational credit
> grants is gated by `credit-applications.md` §"When a grant is
> approved" — do not edit live.**

**Snapshot at audit time (as observed in the explore subagent's read):**

| Provider key | Priority | Pool weight | Strategic mapping |
|---|---:|---:|---|
| `openai` | 1 | 100 | Currently routes via Azure OpenAI (`AZURE_OPENAI_DEPLOYMENT` env). Strategic: keep as Tier-2 chat; consider demotion to Tier-2 once Vertex Gemini hits target latency SLA. |
| `anthropic` | 2 | 80 | **Drift vs strategic plan** — Anthropic is NOT in the strategic chat chain (Bedrock is Cohere-only; no Anthropic provider). To remove on next refactor pass. |
| `google_vertex` | 3 | 60 | Strategic Tier-1 chat. Should be promoted to priority 1, weight 100 in next refactor. |
| `azure_openai` | 4 | 40 | Strategic Tier-2 chat. |
| `mistral_azure` | 5 | 20 | **Drift** — Mistral is not in the strategic plan. Demote/remove. |

**Pool weights observed (`gpt-4o` 0.70 / `claude-3-5-sonnet` 0.20 /
`gemini-1.5-pro` 0.10):** these reflect a previous `openai`-primary
era. Strategic target weights after refactor:
`gemini-2.5-flash` 0.70 / `gpt-4.1-mini` 0.25 /
`@cf/meta/llama-3.3-70b-instruct-fp8-fast` 0.05.

> ⚠️ **Operational drift:** the runtime config has not yet been
> refactored to match the strategic chain. The migration is gated and
> tracked separately — see `credit-applications.md` §"When a grant is
> approved" for the trigger conditions.

---

## 8. Per-pool 10k DAU draw — line-by-line totals

Cross-check against the audit doc's pool roll-ups.

### 8.1 Vertex / GCP $2k pool

| Line | $/mo |
|---:|---:|
| 1.1 chat (Tier-1 80% share) | $95 |
| 1.2 Assamese polish (Tier-2) | $2 |
| 1.3 grounded answer | $12 |
| 1.5 vision OCR | $3 |
| 2.1 quiz fallback (Tier-2) | $1 |
| 2.2 notes summary | $4 |
| 2.6 Vertex Vector Search (cold) | $0 |
| 3.2 Indic TTS Neural2 | $6 |
| 4.1 topic discovery cluster | $0.10 |
| 4.2 anchor text gen | $1 |
| 4.3 SEO auto-publish | $5 |
| 6.x grounded-recall-nightly | $2 |
| Cloud Vision API | $0.5 |
| Cloud Logging | $5 |
| Cloud TTS Neural2 (English fallback share) | $13 |
| **Subtotal** | **~$150/mo** |

### 8.2 Azure $2.5k pool

| Line | $/mo |
|---:|---:|
| 1.1 chat Tier-2 (15% share) | $13 |
| 1.7 Azure Cache for Redis Basic C0 | $16 |
| 2.1 quiz Tier-1 | $5 |
| 2.3 flashcard build Tier-1 | $8 |
| 2.5 leaderboard ZSET (within Redis) | $0 |
| Container Apps (workers + rust-core + cron) | $40 |
| App Insights (free 5GB) | $0 |
| Logic Apps | $0 |
| Container Registry | $5 |
| Standby failover backend | $0 |
| **Subtotal** | **~$87/mo (~38% draw of $228/mo annualized headroom)** |

### 8.3 AWS Activate $1k pool

| Line | $/mo |
|---:|---:|
| App Runner (1vCPU/2GB autoscale) | $35 |
| S3 (sole object store) | $3 |
| SES + SQS + Lambda (free tier) | $0 |
| 2.7 + 2.8 Bedrock Cohere embed + rerank | $21 |
| Secrets Manager | $2 |
| CloudWatch (within free) | $0 |
| 6.x daily-mongo-s3-backup Lambda | $1 |
| **Subtotal** | **~$62/mo (~75% draw — mitigated)** |

### 8.4 Cloudflare for Startups $5k pool

| Line | $/mo |
|---:|---:|
| Pages (frontend) | $0 |
| Workers (60M req/mo) | $5 |
| R2 (cold blob archive) | $1 |
| Workers AI (translate + Whisper + MeloTTS fallback) | $15 |
| AI Gateway BYOK | $0 |
| KV / Durable Objects | $5 |
| Vectorize (fallback) | $0 |
| Email Routing | $0 |
| Cache Reserve | $5 |
| **Subtotal** | **~$31/mo (~7% draw)** |

### 8.5 MongoDB Atlas + Pinecone + free-tier providers

| Pool | $/mo |
|---:|---:|
| Mongo Atlas M0 → M2 | $9 |
| Pinecone Starter (free) | $0 |
| Momento Cache (free) | $0 |
| Axiom (free) | $0 |
| Sentry (free) | $0 |
| Resend (free) | $0 |
| GitHub (free) | $0 |
| **Subtotal** | **~$9/mo** |

### 8.6 Combined headline

| Pool | Draw | Headroom | % drawn |
|---|---:|---:|---:|
| Vertex | $150 | $167 | 90% (mitigated) |
| Azure | $87 | $208 | 38% |
| AWS Activate | $62 | $83 | 75% (mitigated) |
| Cloudflare | $31 | $416 | 7% |
| Mongo Atlas | $9 | $42 | 21% |
| **Total** | **~$339/mo** | **~$917/mo** | — |
| **Cash exposure** | **$0** | — | — |

---

## 9. How this doc relates to its companions

| Companion | What it answers | Relation to this doc |
|---|---|---|
| `feature-to-provider-audit.md` | "Are we zero cash at 10k DAU?" | Summary roll-up + risk register; this doc is the per-feature backing detail |
| `cloud-allocation-plan.md` | "Which cloud owns which workload?" | This doc operates within that allocation; do not contradict |
| `cloud-service-breakdown.md` | "What does each AWS/Azure/CF/GCP service do?" | This doc cites concrete services from there |
| `cost-per-feature-comparison.md` | "How much would each feature cost on alternative cloud splits?" | This doc uses the chosen split (4-cloud); do not re-litigate alternatives here |
| `10k-dau-cost-audit.md` | "Per-cloud headroom at 10k DAU?" | This doc's §8 must reconcile with that doc's §2 |
| `auxiliary-providers-delegation.md` | "Why each non-cloud provider exists?" | This doc cites the role; do not duplicate the rationale |
| `credit-applications.md` | "Which credits are PENDING vs LANDED?" | This doc cites the status; the gating policy lives there |

---

## 10. Re-running this audit

```bash
# 1. Enumerate routes
rg -n "^@router\\.(get|post|put|delete)" artifacts/syrabit-backend/api/

# 2. Enumerate scheduled jobs
rg -n "schedule\\(" artifacts/syrabit-backend/workers/
rg -n "cron|KEDA" artifacts/syrabit-backend/infra/container-apps-jobs.tf

# 3. Trace provider calls per handler
rg -l "vertex|azure_openai|workers_ai|cohere|pinecone|elevenlabs|deepgram|mongo|redis|momento|cartesia|voyage|sarvam" \
   artifacts/syrabit-backend/api/<handler>.py

# 4. Snapshot dispatcher config
rg -n "PROVIDER_PRIORITY|POOL_WEIGHTS" artifacts/syrabit-backend/config.py

# 5. Reconcile per-pool draw
# diff §8 of this doc against §2 of 10k-dau-cost-audit.md
```

> Re-run this audit any time a new feature ships, a new provider is
> introduced, a credit pool's draw shifts ≥ 10%, or
> `PROVIDER_PRIORITY` / `POOL_WEIGHTS` is edited.
