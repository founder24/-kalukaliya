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

# Per-Feature Deep Dive

**Date:** 2026-05-04
**Companion to:** [`feature-to-provider-mapping-detailed.md`](./feature-to-provider-mapping-detailed.md) (the per-handler chain table)
**Purpose:** Go one level deeper than the mapping table — for each shipped feature, specify the exact request/response shape, env vars consumed, IAM scopes, retry/timeout/circuit-breaker config, prompt skeleton, pricing math, monitoring metric, alert threshold, failure modes, and runbook entry.

> **How to read this doc.** Each feature gets a 9-section template:
> §A request shape, §B response shape, §C env vars, §D IAM/RBAC,
> §E retry + timeout + circuit-breaker, §F prompt skeleton, §G pricing
> math, §H monitoring + alert, §I failure modes + runbook.
>
> Where a section is identical across all features (e.g., common
> middleware env), it's lifted to §0.

---

## §0 Common cross-cutting

### 0.1 Common middleware env (every authenticated route)

| Env var | Purpose | Owner secret |
|---|---|---|
| `JWT_SECRET` | HS256 signing for student JWTs | `JWT_SECRET` |
| `ADMIN_JWT_SECRET` | separate HS256 secret for admin JWTs | `ADMIN_JWT_SECRET` |
| `MONGO_URL` | Atlas SRV connection string | `MONGO_URL` |
| `AZURE_REDIS_HOST` / `AZURE_REDIS_PORT` / `AZURE_REDIS_KEY` | cache primary | (Azure-managed) |
| `MOMENTO_API_KEY` | cache Tier-2 | (Momento-managed) |
| `CF_KV_NAMESPACE_ID` | cache Tier-3 | (CF-managed) |
| `AWS_REGION` | App Runner / S3 / Bedrock region pin | `AWS_REGION` |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Vertex SA key (JSON) | `GOOGLE_APPLICATION_CREDENTIALS_JSON` |

### 0.2 Common dispatcher behavior (every Tier-1 → Tier-2 demote)

```python
# dispatcher.py — pseudo-spec
@retry(
    retries=3,
    backoff=ExponentialBackoff(base_ms=200, cap_ms=2000, jitter=True),
    retry_on=(httpx.ReadTimeout, httpx.ConnectError, RateLimitedError),
)
@circuit_breaker(
    failure_threshold=5,         # open after 5 failures in 60s window
    open_duration_s=30,          # stay open for 30s, then half-open probe
    half_open_max_calls=1,
)
async def call_provider(provider: str, request: dict) -> dict: ...
```

* Tier-1 → Tier-2 demote on: HTTP 5xx (any), HTTP 429 with `Retry-After` > per-feature SLA, `httpx.ReadTimeout` past per-feature SLA, circuit breaker OPEN.
* Promotion back to Tier-1: 5 consecutive successful health probes from background `health-prober` worker (probes every 60s).
* All provider calls emit:
  * Axiom log `dispatcher.call` with fields `(feature, provider, tier, latency_ms, status, retry_count, circuit_state)`.
  * Prometheus counter `syrabit_provider_calls_total{feature,provider,tier,status}`.
  * Histogram `syrabit_provider_latency_ms{feature,provider,tier}`.

### 0.3 Common monitoring stack

| Layer | Tool | Retention |
|---|---|---|
| Metrics | Azure App Insights (central) + Prometheus exporter on App Runner | 90 days |
| Logs | Axiom `syrabit-prod` dataset | 30 days |
| Traces | Azure App Insights distributed tracing (W3C trace context) | 90 days |
| Errors | Sentry `syrabit-backend` + `syrabit-web` projects | 90 days |
| Alerts | Telegram bot `@SyrabitOpsBot` + email `founder@syrabit.ai` | — |

### 0.4 Common runbook conventions

Every runbook entry follows: **(1) Detect** → **(2) Triage** → **(3) Mitigate** → **(4) Resolve** → **(5) Postmortem**. Triage SLA: 5 min (P0), 30 min (P1), 4 hr (P2). Mitigation SLA: 30 min (P0), 2 hr (P1), 1 day (P2).

---

## §1 AI Learning & Chat — Deep Dive

### 1.1 Streaming AI chat with RAG — `POST /api/ai/chat/stream`

**§A Request shape**
```json
{
  "session_id": "01HX5...ULID",
  "namespace": "as|en|hi|bn",
  "subject": "physics|chemistry|math|...",
  "chapter_id": "ahsec-phy-12-ch3",
  "history": [{"role":"user|assistant","content":"...","ts":1714896000}],
  "question": "Why is the kinetic energy of...?",
  "stream": true,
  "client": {"build":"web@2.4.1","locale":"as-IN"}
}
```

**§B Response shape (SSE)**
```
event: token
data: {"delta":"কাইনেটিক "}

event: citations
data: [{"chapter_id":"ahsec-phy-12-ch3","span":[1024,1280],"score":0.87}]

event: done
data: {"finish_reason":"stop","tokens":{"in":4128,"out":487},"provider":"vertex","tier":1,"latency_ms":2814}
```

**§C Env vars consumed**

| Env var | Used for |
|---|---|
| `GEMINI_API_KEY` | rollback path only (strategic = SA-keyed Vertex client) |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Vertex Tier-1 |
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT` / `AZURE_OPENAI_MODEL` / `AZURE_OPENAI_API_KEY` | Tier-2 |
| `CF_ACCOUNT_ID` / `CF_API_TOKEN` | Tier-3 (Workers AI) |
| `AZURE_REDIS_*` | prompt cache + rate limiter |

**§D IAM / RBAC per provider**

| Provider | Principal | Permissions |
|---|---|---|
| Vertex | SA `syrabit-backend@<project>.iam.gserviceaccount.com` | `aiplatform.endpoints.predict`, `aiplatform.endpoints.streamGenerateContent` |
| Azure OpenAI | RBAC role `Cognitive Services OpenAI User` on resource `syrabit-aoai-eus2` | scope: deployment `syrabit-chat` |
| CF Workers AI | API token `syrabit-prod-workers-ai` | permissions: `Workers AI Read`, `Workers AI Write` |

**§E Retry + timeout + circuit-breaker**

| Setting | Tier-1 (Vertex) | Tier-2 (Azure) | Tier-3 (CF) |
|---|---|---|---|
| connect timeout | 2s | 2s | 1s (edge) |
| read timeout | 30s (streaming) | 30s | 30s |
| retries | 2 (backoff 200/400ms) | 2 | 1 |
| 429 handling | honor `Retry-After` capped at 3s, else demote | same | demote immediately |
| circuit threshold | 5 fails / 60s window | 5 fails / 60s | 3 fails / 60s |

**§F Prompt skeleton**

```
[SYSTEM ~ 800 tok]
You are Syrabit, an AHSEC/SEBA tutor. Always cite chapter spans...
Locale: {locale}. Subject: {subject}. Style: friendly, brief.
Refuse off-syllabus topics with a soft-redirect.

[RAG CONTEXT ~ 2.5k tok]
<<<chapter:{chapter_id}#{span_a}-{span_b}>>>
...
<<<end>>>
[Optional: 2–3 more retrieved spans, dedup'd]

[USER ~ 200 tok]
{question}

[ASSISTANT — model generates ~500 tok]
```

**§G Pricing math (10k DAU steady state)**

| Term | Value |
|---|---:|
| chats/mo | 720,000 |
| avg tokens/call (in+out) | 4,000 |
| total tokens/mo | 2.88B |
| prompt-cache hit rate | 30% |
| effective tokens billed | 2.02B |
| Vertex Gemini 2.5 Flash list price (in/out blended ~$0.05/M) | **~$95/mo** |
| Azure GPT-4.1-mini share (15% spillover @ ~$0.15/M blended) | **~$13/mo** |
| CF Workers AI Llama-3.3-70B share (5% @ ~$0.20/M) | **~$2/mo** |
| **Subtotal Feature 1.1** | **~$110/mo** |

**§H Monitoring + alert**

| Metric | SLO | Alert |
|---|---|---|
| `syrabit_provider_latency_ms{feature="chat",tier="1"}` p95 | < 8s | Telegram if > 12s for 5 min |
| `syrabit_provider_calls_total{feature="chat",status="error"}` rate | < 1% | Telegram if > 5% for 5 min |
| `syrabit_chat_safety_blocks_total` rate | < 0.5% | review if > 2% |
| Vertex daily $ | < $8 | Telegram if budget exceeded (Cloud Billing budget alert) |

**§I Failure modes + runbook**

| Failure | Detect | Mitigate | Resolve |
|---|---|---|---|
| Vertex regional outage `asia-south1` | health-prober FAIL | dispatcher auto-demotes to Azure | wait for region recovery, re-promote on 5 successful probes |
| Vertex quota exhausted (RAI) | 429 with `RESOURCE_EXHAUSTED` | dispatcher demotes to Azure; raise quota request via Cloud Console | next quota cycle |
| Citation validator fails > 10% | metric `chat_citation_fail_rate` | demote to Azure (different cite formatting); check RAG retrieval quality | re-tune retrieval threshold |

---

### 1.2 Assamese chat mode (translate-then-embed) — `ensure_question_in_assamese()`

**§A Internal call (no HTTP)** — invoked by 1.1 when `namespace == "as"` and detected script ≠ Bengali-Assamese.

**§B Returns** translated string + `was_translated: bool`.

**§C Env vars** — `CF_ACCOUNT_ID`, `CF_API_TOKEN` (Workers AI), plus 1.1's Vertex vars for polish.

**§D IAM** — CF Workers AI Read; Vertex `aiplatform.endpoints.predict`.

**§E Retry + timeout** — 5s read timeout; 1 retry; on fail use original (degrade silently — RAG retrieval still works on transliterated text).

**§F Prompt skeleton (Vertex polish step)**
```
[SYSTEM ~ 200 tok]
Polish the following Assamese sentence for grammatical correctness
without changing meaning. Output the corrected sentence ONLY.

[USER]
{translated_text}
```

**§G Pricing**

| Term | Value |
|---|---:|
| translations/mo | 80,000 |
| polish calls/mo | 80,000 |
| CF Workers AI IndicTrans2 (160k × 150 tok = 24M tok) | **~$3/mo** |
| Vertex polish (80k × 180 tok = 14M tok) | **~$2/mo** |
| **Subtotal** | **~$5/mo** |

**§H Monitoring** — counter `syrabit_assamese_translate_total{was_translated}`; latency histogram. Alert: degrade rate > 5% in 30 min.

**§I Failure modes** — IndicTrans2 returns garbled text → polish step doesn't fix → log + Sentry breadcrumb + fall through with original. No user-visible error.

---

### 1.3 Grounded answer (strict-RAG) — `POST /api/edu/grounded-answer`

**§A Request**
```json
{
  "question": "...",
  "context_url": "https://en.wikipedia.org/wiki/...",
  "page_text": "(server-fetched, capped at 10000 chars)",
  "lang": "en|as|hi|bn"
}
```

**§B Response**
```json
{
  "answer": "...",
  "citations": [{"start": 124, "end": 384, "quote": "..."}],
  "confidence": 0.83,
  "provider": "vertex",
  "tier": 1
}
```

**§C Env vars** — same as 1.1 + `READER_CACHE_TTL` (default 86400).

**§D IAM** — same as 1.1.

**§E Retry + timeout** — 15s read timeout (longer because grounded validation is strict); 2 retries.

**§F Prompt skeleton**
```
[SYSTEM ~ 600 tok]
Answer ONLY using the provided page text. If the answer is not in
the page, say "I cannot answer from this source" — do NOT use prior
knowledge. Cite character offsets as JSON: [{"start":N,"end":M}].

[CONTEXT — page_text capped 10k chars]
{page_text}

[USER]
{question}

[ASSISTANT — must include "citations" array; validator checks
that every claim sentence has at least one citation]
```

**§G Pricing**

| Term | Value |
|---|---:|
| calls/mo | 80,000 |
| avg tokens/call | 3,700 |
| total tokens/mo | 296M |
| Vertex Gemini 2.5 Flash | **~$12/mo** |
| Azure spillover | **~$2/mo** |
| CF Workers AI gpt-oss-20b spillover | **~$1/mo** |
| **Subtotal** | **~$15/mo** |

**§H Monitoring** — `syrabit_grounded_citation_present_rate` SLO ≥ 95%; alert < 90% for 30 min.

**§I Failure modes** — citation validator rejects → demote one tier and retry; if all 3 tiers fail validation, return `confidence: 0.0` and refuse to answer (UX shows "Try a different source").

---

### 1.4 PDF → MCQ ingest + RAG indexing — `POST /api/admin/content/cms-documents/{doc_id}/process-rag`

**§A Request** — multipart form with PDF (up to 50 MB) + JSON metadata `{subject, chapter_id, version}`.

**§B Response** (sync ack, async work):
```json
{"job_id":"01HX5...ULID","status":"queued","estimated_complete_at":"2026-05-04T11:32:00Z"}
```

Then job emits webhook to admin UI on complete.

**§C Env vars** — `AWS_REGION`, `S3_BUCKET=syrabit-prod-cms`, `BEDROCK_MODEL=cohere.embed-multilingual-v3`, `PINECONE_API_KEY`, `PINECONE_INDEX=syrabit-rag`.

**§D IAM**

| Resource | Principal | Permissions |
|---|---|---|
| S3 | App Runner SA | `s3:PutObject`, `s3:GetObject` on `arn:aws:s3:::syrabit-prod-cms/*` |
| Bedrock | App Runner SA | `bedrock:InvokeModel` on `cohere.embed-multilingual-v3` |
| Lambda `syrabit-pdf-parse` | Lambda execution role | `s3:GetObject`, `logs:*` |
| Pinecone | API key `syrabit-prod` | namespace write |

**§E Retry + timeout** — Lambda timeout 30s; SQS DLQ after 3 attempts; Bedrock retry 3× with backoff; Pinecone upsert retry 5× (idempotent on doc_id).

**§F Prompt skeleton** — N/A (embedding-only; no LLM in ingest path).

**§G Pricing**

| Term | Value |
|---|---:|
| PDFs/mo | 300 |
| avg chunks/PDF | 120 |
| total chunks/mo | 36,000 |
| total embed tokens/mo | 18M |
| Bedrock Cohere embed @ ~$0.10/M | **~$2/mo** |
| Lambda runtime | $0 (within 1M-req free tier) |
| Pinecone Starter | $0 |
| **Subtotal** | **~$2/mo** |

**§H Monitoring** — `syrabit_rag_ingest_chunks_total{subject}`, `syrabit_rag_ingest_lambda_duration_ms` p95 < 8s, `syrabit_rag_ingest_pinecone_upsert_errors` should be 0.

**§I Failure modes** — Lambda timeout on > 100-page PDFs → split-and-retry queue (SQS) → admin UI shows "processing in background"; permanent fail → DLQ + Telegram.

---

### 1.5 Vision OCR for chat input — `POST /api/ai/ocr-image`

**§A Request** — multipart with `image` (≤ 20 MB JPEG/PNG/WebP) + optional `prompt_hint`.

**§B Response**
```json
{"text":"...extracted text...","language":"as","confidence":0.91,"provider":"vertex","tier":1}
```

**§C Env vars** — Vertex SA + `VISION_API_PROJECT` for Tier-2.

**§D IAM** — Vertex `aiplatform.endpoints.predict` (multimodal); Vision API `serviceusage.services.use` on Vision API.

**§E Retry + timeout** — 8s read timeout; 1 retry; HTTP 413 on > 20 MB (no fallback — client must compress).

**§F Prompt skeleton**
```
[SYSTEM]
Extract the text from this image verbatim. Return ONLY JSON:
{"text": "...", "language": "ISO-639-1", "confidence": 0–1}
Detect handwritten Assamese/Bengali script.

[USER]
<image_part>
{prompt_hint}
```

**§G Pricing** — 36k OCR/mo × ~3k tok-equiv = 108M tok-equiv/mo. **Vertex ~$3/mo + Cloud Vision ~$0.5/mo.**

**§H Monitoring** — p95 latency < 5s; error rate < 2%; `syrabit_ocr_image_too_large_total` (413s).

**§I Failure modes** — Vertex multimodal rejects > 20 MB inline image → 413; user re-uploads compressed.

---

### 1.6 Conversation history persistence

**§A Internal** — every chat handler writes 2 docs per turn (user msg + assistant msg) to `mongo.conversations`.

**§B Schema**
```js
{
  _id: ObjectId,
  session_id: "ULID",
  user_id: "ULID",
  role: "user"|"assistant",
  content: "...",
  ts: ISODate,
  provider_used: {provider:"vertex", tier:1, latency_ms:2814, tokens:{in,out}},
}
```

**§C Env** — `MONGO_URL`.

**§D IAM** — Atlas user `syrabit-app` with `readWrite` on db `syrabit_prod`.

**§E Retry** — Mongo driver default (3 retries via `retryWrites=true`).

**§G Pricing** — 7.2 GB writes/mo on M0 (free) → migrate to M2 ($9/mo) once collection > 500 MB. Atlas $500 startup credit covers M2 for ~55 mo.

**§H Monitoring** — `mongo_writes_per_sec` < 200, alert if connection-pool saturation > 80%.

**§I Failure modes** — Mongo write failure → in-memory ring buffer (LRU 1000) + retry on next request; if buffer full → drop oldest with WARN log.

---

### 1.7 Session + rate limit + JWT blacklist — `cache.py`

**§A Internal call** — every authed request triggers 3 cache ops in middleware:
1. session lookup `GET sess:{jti}` (TTL 7 days)
2. rate limit `INCR ratelimit:{user_id}:{minute}` + `EXPIRE 60`
3. JWT blacklist `EXISTS jwt_bl:{jti}`

**§C Env** — `AZURE_REDIS_HOST`, `AZURE_REDIS_PORT`, `AZURE_REDIS_KEY`, `MOMENTO_API_KEY`, `CF_KV_NAMESPACE_ID`.

**§D IAM** — Azure Cache: access key auth (rotated quarterly via Key Vault); Momento: API key per env; CF KV: API token with `Workers KV Storage Edit` scope.

**§E Retry + timeout** — Redis: 50ms connect, 100ms command; 1 retry then demote to Momento. Momento: HTTP timeout 200ms, 1 retry then demote to CF KV.

**§F** N/A (no LLM).

**§G Pricing**

| Term | Value |
|---|---:|
| Authed req/mo at 10k DAU | 30M (~100 req/user/day) |
| Cache ops/req | 3 |
| Total ops/mo | 90M |
| Azure Cache for Redis Basic C0 (250 MB, 1k req/s) | **$16/mo** (within Azure pool) |
| Momento (free 5GB / 5M ops) | **$0** |
| CF KV (free 1k req/sec) | **$0** |
| **Subtotal** | **$16/mo** |

**§H Monitoring** — Azure App Insights `redis.connections`, `redis.commands_per_sec`, `redis.cache_hit_rate` (target > 95%). Alert: hit rate < 80% for 30 min.

**§I Failure modes** — Redis CLUSTER-DOWN → auto-demote to Momento; rate-limit semantics weakened (eventually consistent) but app stays up. Postmortem if event lasts > 30 min.

---

## §2 Educational Study Tools — Deep Dive

### 2.1 MCQ / Quiz generator — `POST /api/edu/quiz/generate`

**§A Request** — `{chapter_id, count: 1..10, difficulty?: "easy|medium|hard"}`.

**§B Response**
```json
{"quiz_id":"ULID","questions":[{"q":"...","choices":["A","B","C","D"],"answer":"B","explanation":"..."}],"from_cache":true,"pool_size":24}
```

**§C Env** — `AZURE_OPENAI_DEPLOYMENT=syrabit-quiz`.

**§D IAM** — Cognitive Services OpenAI User on Azure resource.

**§E Retry + timeout** — 15s timeout (long generation); 1 retry to Vertex; cache miss penalized once per chapter.

**§F Prompt skeleton**
```
[SYSTEM ~ 600 tok]
Generate {N=24} MCQs for AHSEC chapter {chapter_id}.
Return STRICT JSON: [{"q":"...","choices":["A","B","C","D"],"answer":"B","explanation":"..."}]
Difficulty distribution: 8 easy, 12 medium, 4 hard. Avoid trick questions.

[CONTEXT ~ 1.5k tok]
{chapter_summary}

[ASSISTANT — ~6k tok JSON]
```

**§G Pricing** — 5k unique generations × 8k tok = 40M tok. **Azure ~$5/mo + Vertex ~$1/mo = ~$6/mo.**

**§H Monitoring** — JSON-parse-fail rate < 1% (alert if > 3%); `quiz_pool_cache_hit_rate` > 95%.

**§I Failure modes** — JSON parse fail → 1 retry with stricter prompt; if both fail → Tier-2 Vertex; final fall: serve last-good cached pool with `from_cache: true, stale: true`.

---

### 2.2 Notebook + AI summaries — `POST /api/edu/notes`

**§A Request** — `{note_id?, title, body_md}` (auto-summarize if `len(body_md) > 800`).

**§B Response** — `{note_id, summary?, last_modified}`.

**§F Prompt skeleton**
```
[SYSTEM]
Summarize this student note in {locale}. 3 bullet points max.
Preserve technical terms verbatim.
[USER]
{body_md}
```

**§G Pricing** — 120k summaries × 1k tok = 120M tok. **Vertex ~$4/mo.**

**§H/I** — same shape as 1.1 deep-dive (degrade gracefully — note saves even if summary fails; summary appears later via background retry queue).

---

### 2.3 Flashcards + spaced repetition — `POST /api/edu/flashcards/build`

**§A Request** — `{chapter_id, count?: 20}`.

**§B Response** — `{cards:[{front, back, hint?}], schedule_state}`.

**§E Retry + timeout** — 12s timeout; 1 retry to Vertex.

**§F Prompt skeleton**
```
[SYSTEM]
Build {N=20} flashcards for {chapter_id}. Each card: front (question or
term), back (concise answer), hint (optional 1-line nudge).
Return STRICT JSON.
[CONTEXT]
{chapter_summary}
```

**§G Pricing** — 10k builds × 7k tok = 70M tok. **Azure ~$8/mo.**

**§H Monitoring** — `flashcard_review_due_count{user}` daily; `flashcard_sm2_lapse_rate`; alert if > 30% lapses (curriculum quality issue).

**§I** — SM-2 state in Mongo; review handler is pure DB + Redis ZSET, no LLM, never fails.

---

### 2.4 Edu reader + URL allowlist — `POST /api/edu/reader/fetch`

**§A Request** — `{url}`.

**§B Response** — `{title, content_html, reading_time_min, safety:{score, flags:[]}, cached_at}`.

**§C Env** — `WEB_RISK_API_KEY` (GCP), `READER_CACHE_TTL=86400`.

**§D IAM** — Web Risk API: `webrisk.uris.search`; CF Worker: deployed under route `reader.syrabit.ai/*`.

**§E Retry + timeout** — Web Risk 2s; HTML fetch 8s; cache TTL 24h.

**§G Pricing** — 50k fetches/mo, 80% cache hit ⇒ 10k Web Risk API + 10k CF Worker invocations. **$0 (within free quotas).**

**§H Monitoring** — `reader_cache_hit_rate` > 75%; `web_risk_block_total` (track for safety insights).

**§I Failure modes** — URL flagged unsafe → return `safety.score < 0.5` and refuse to render content. No silent fallback.

---

### 2.5 Streaks + leaderboard — `GET /api/edu/flashcards/streak`

**§A Request** — query `?cohort=class12_assam`.

**§B Response** — `{my_rank, my_streak, top_100:[{user_id, display_name, streak}]}`.

**§E Retry + timeout** — 100ms Redis ZRANGE; fallback Mongo aggregation (300ms p95).

**§G Pricing** — within Azure Cache $16/mo.

**§H Monitoring** — `leaderboard_zset_size{cohort}`; cache miss rate < 5%.

**§I Failure modes** — Redis down → Mongo aggregation fallback (slower but correct); UI shows banner "Live ranks delayed" if Mongo fallback used > 5 min.

---

### 2.6 Vector retrieval — `retrievers/pinecone.py`

**§A Internal call** — `retrieve(query, namespace, topK=5)`.

**§B Returns** — `[{chunk_id, score, text, metadata}]`.

**§C Env** — `PINECONE_API_KEY`, `PINECONE_INDEX=syrabit-rag`.

**§D IAM** — Pinecone API key scoped to project `syrabit-prod`.

**§E Retry + timeout** — 200ms connect, 500ms read; 1 retry then demote to Vertex Vector Search.

**§G Pricing** — 800k retrievals/mo within Pinecone Starter free tier. **$0.**

**§H Monitoring** — `pinecone_query_latency_ms` p99 < 200ms; `pinecone_index_size_vectors` (alert at 80% of free tier 100k).

**§I Failure modes** — Pinecone 5xx → Vertex Vector Search (slower, ~500ms); if both down → Vertex Discovery Engine (semantic search over raw CMS, lower precision).

---

### 2.7 Embed pipeline — `embeddings/cohere.py`

**§A Internal call** — `embed_batch(texts: List[str], model="embed-multilingual-v3")`.

**§B Returns** — `List[List[float]]` (1024-dim).

**§E Retry + timeout** — Bedrock 15s; 3 retries with exponential backoff; on Bedrock outage → Voyage `voyage-3-multilingual` (768-dim, requires re-index of affected chunks).

**§G Pricing** — 28M tok/mo embed. **~$3/mo on Bedrock.**

**§H Monitoring** — `bedrock_embed_latency_ms` p95 < 2s; `bedrock_throttle_total` (alert if > 10/min).

**§I Failure modes** — Bedrock throttled → SQS replay queue; UI shows "Indexing..." for affected docs.

---

### 2.8 Rerank pipeline — `embeddings/rerank.py`

**§A Internal call** — `rerank(query, candidates: List[Doc], top_n=5)`.

**§E Retry + timeout** — 3s; 1 retry; on fail → Voyage rerank-2; on both fail → return Pinecone topK as-is (graceful degrade).

**§G Pricing** — 330k rerank calls/mo × 10k tok = 3.3B tok. **~$18/mo on Bedrock.**

**§H Monitoring** — `rerank_skip_total` (graceful-degrade count); alert if > 5% of total.

**§I** — graceful degrade is acceptable; quality drops marginally.

---

## §3 Voice & Accessibility — Deep Dive

### 3.1 Read-Aloud (TTS) — English

**§A Request** — `{text, lang="en", voice?="rachel", speed?=1.0}`.

**§B Response** — binary `audio/mpeg` (MP3 64kbps mono) + headers `X-Provider: elevenlabs`, `X-Tier: 1`, `X-Latency-Ms: 1240`.

**§C Env** — `ELEVENLABS_API_KEY`, GCP SA, `CARTESIA_API_KEY`, CF Workers AI, `AWS_REGION`.

**§D IAM**

| Provider | Scope |
|---|---|
| ElevenLabs | API key, monthly char cap (per pricing tier) |
| GCP TTS | SA `aiplatform.endpoints.predict` + `texttospeech.synthesize` |
| AWS Polly | App Runner SA, `polly:SynthesizeSpeech` (Neural voices only) |

**§E Retry + timeout** — 5s; 1 retry then demote.

**§G Pricing**

| Tier | Volume | Cost |
|---|---:|---:|
| ElevenLabs (if $4k credit lands) | 144M chars | $0 (within credit) |
| GCP TTS Neural2 fallback (worst case) | 144M chars × $16/M − 4M free | ~$24/mo (within Vertex pool) |
| **Subtotal worst-case** | — | **~$24/mo** |

**§H Monitoring** — `tts_provider_used_total{provider}`; `tts_latency_ms` p95 < 3s; `elevenlabs_chars_consumed_total` (track against credit).

**§I Failure modes** — ElevenLabs 429 → demote to GCP TTS Neural2; UI shows no degradation. Quality drop is < 5% MOS.

---

### 3.2 Read-Aloud (TTS) — Indic (as/hi/bn)

**§A Request** — `{text, lang ∈ {as,hi,bn}, voice?, speed?=1.0}`.

**§B Response** — binary audio + provider headers.

**§C Env** — GCP SA + CF Workers AI + AWS Polly.

**§E Retry + timeout** — 5s; 1 retry.

**§F** — voice selection by lang:
* `as` → `as-IN-Wavenet-A`
* `hi` → `hi-IN-Neural2-A`
* `bn` → `bn-IN-Wavenet-A`

**§G Pricing** — 30M chars/mo. GCP free 4M + 26M × $16/M = **~$6/mo within Vertex pool.**

**§H Monitoring** — `tts_indic_voice_unsupported_total` (alert > 1% — model regression).

**§I Failure modes** — Indic voice unavailable → CF Workers AI MeloTTS (Indic models) → AWS Polly Neural Indic.

---

### 3.3 STT (audio → text) — `POST /api/voice/stt`

**§A Request** — multipart `audio` (≤ 5 MB, ≤ 60s, WebM/Opus/PCM) + `lang?`.

**§B Response** — `{text, lang_detected, words:[{w,start_ms,end_ms,conf}], provider, tier}`.

**§C Env** — `DEEPGRAM_API_KEY`, `ASSEMBLYAI_API_KEY`, CF Workers AI, GCP SA.

**§E Retry + timeout** — 5s connect, 10s read; demote to AssemblyAI on Deepgram > 5s; AssemblyAI uses `dual_channel: true, punctuate: true`.

**§G Pricing** — 12 hr audio/mo × $0.0043/min Deepgram = **~$3/mo** (within $200 free or $1k credit if it lands).

**§H Monitoring** — `stt_latency_ms` p95 < 4s; `stt_word_confidence_avg` > 0.85; alert if avg < 0.7 for 30 min (audio quality issue).

**§I Failure modes** — Deepgram 5xx → AssemblyAI; both fail → CF Workers AI Whisper-large-v3-turbo (edge, slower); final fall → GCP Chirp.

---

### 3.4 Two-leg voice pipeline — `POST /api/voice/voice`

**§A Request** — multipart audio + `session_id, namespace, chapter_id`.

**§B Response** — SSE: `event:transcript`, `event:answer_token`, `event:audio_chunk` (binary), `event:done`.

**§E Concurrency** — `VOICE_CONCURRENCY_LIMIT=50` semaphore; per-request `asyncio.gather(stt, partial_chat_stream → tts_stream)`.

**§G Pricing** — sum of 1.1, 3.1, 3.3 (no new $).

**§H Monitoring** — `voice_e2e_latency_ms` p95 < 6s (STT 1s + first chat token 2s + first TTS chunk 1s + render).

**§I Failure modes** — semaphore full → 503 with `Retry-After: 5`; UI shows "Servers busy, queueing your request".

---

## §4 SEO Automation & Content Engine — Deep Dive

### 4.1 Topic discovery + clustering — `POST /api/topics`

**§A Request** — admin-only; body `{seed_keywords:[...], max_topics:500}`.

**§B Response** — `{job_id, estimated_complete_at}` (async via SQS).

**§C Env** — `BING_WEBMASTER_API_KEY`, Vertex SA, Pinecone.

**§D IAM** — Bing API key on `syrabit.ai` site verification; Vertex SA.

**§E Retry** — Bing 3 retries; cluster Vertex 2 retries.

**§F Prompt skeleton**
```
[SYSTEM]
Cluster these {N=500} candidate keywords into {K=20–40} topics
relevant to AHSEC/SEBA curriculum. Return JSON:
[{"topic":"...","keywords":[...],"intent":"informational|navigational"}]
```

**§G Pricing** — 30 runs × 100k tok = 3M tok/mo. **Vertex ~$0.10/mo.**

**§H Monitoring** — `topic_discovery_runs_total`; `topic_dedup_rejection_rate` (Pinecone-detected dups).

**§I** — Bing API outage → use cached top-1k keywords; quality drops slightly.

---

### 4.2 Internal linker — `POST /api/admin/seo/internal-links/trigger`

**§A Request** — `{since_iso?: "2026-05-03T00:00Z"}` (default last 24h).

**§B Response** — `{pages_processed, links_inserted, top_anchors:[...]}`.

**§E Retry + timeout** — Bedrock rerank 3s; Vertex anchor 5s; both 1 retry.

**§F Prompt skeleton (anchor-text gen)**
```
[SYSTEM]
Generate a natural anchor text linking to {target_url} from
context: "{source_paragraph}". One anchor only. Avoid keyword stuffing.
```

**§G Pricing** — rolled into 2.8 + Vertex ~$1/mo.

**§H Monitoring** — `internal_links_inserted_total{site}`; `anchor_repetition_rate` < 10%.

**§I** — On rerank fail, skip that page (don't insert random links).

---

### 4.3 SEO auto-publish loop — `_seo_auto_publish_loop`

**§A** — runs as ACA Job `seo-auto-publish` (`*/15 * * * *`). Per-topic gate: only fires if `topic.next_publish_at <= now()`.

**§E Retry** — per-topic atomic (idempotent on `topic_id + version`); SQS DLQ on permanent fail.

**§F Prompt skeleton**
```
[SYSTEM]
Generate an AHSEC-aligned blog post in {locale} for topic {topic}.
~800 words, H1, 3 H2 sections, conclusion, FAQ section.
Cite chapter spans inline as [ch:N#span].
```

**§G Pricing** — ~36M tok/mo + ~10M embed tokens for re-index = **~$8/mo total** (Vertex + AWS Bedrock + S3 + CF).

**§H Monitoring** — `seo_pages_published_total{site,locale}`; `seo_publish_lag_p95_min` < 30 min from `next_publish_at`.

**§I** — Permanent fail → DLQ + Telegram with topic_id; admin retries via UI.

---

### 4.4 IndexNow / sitemap ping — `POST /api/admin/indexnow/ping`

**§A** — body `{urls:[...]}` (≤ 10k per call).

**§E Retry** — 3 retries per endpoint; partial-success acceptable.

**§G** — **$0** (free APIs).

**§H Monitoring** — `indexnow_ping_total{provider,status}`.

**§I** — Bing/Yandex 5xx → next hourly cron retries (idempotent).

---

## §5 Admin & Infrastructure Tools — Deep Dive

### 5.1 Unified admin dashboard — `GET /api/admin/dashboard/metrics`

**§A** — query `?range=24h|7d|30d`.

**§B Response** — `{revenue, dau, latency_p95, cf_req_per_s, cost_today_per_cloud, ...}`.

**§E** — `asyncio.gather` of 12 sources with 3s timeout each; partial response acceptable (degraded sources marked `null`).

**§G** — $0 within all read quotas.

**§H Monitoring** — `admin_dashboard_partial_response_total` (alert if any source consistently null).

---

### 5.2 Vertex routing panel — `AdminVertexPanel.jsx`

**§A** — read `GET /api/admin/ai/routing-config`; write `POST /api/admin/ai/routing-config` (admin-JWT-required, audit-logged).

**§D IAM** — admin role `routing_admin`; every write emits Axiom audit log + Sentry breadcrumb.

**§I** — Bad routing config (missing required key) → backend rejects with 422 + diff; never persists.

---

### 5.3 Revenue / billing hub — `GET /api/admin/analytics/revenue`

**§E** — Stripe + Razorpay parallel via `asyncio.gather`; 5s timeout each.

**§I** — Stripe 5xx → show last-known cached value with `stale_since` timestamp.

---

### 5.4 R2 watchdog

**§A** — cron-triggered every 2 min; round-trip HEAD/PUT/GET/DELETE on `r2://syrabit-prod-cold/_probe` with random payload.

**§I** — On probe-fail → Telegram + email immediately; no retry within probe window (avoids alert storms).

---

### 5.5 Cost alerts

**§A** — per-cloud daily $ caps configured in:
* AWS: CloudWatch alarm on `EstimatedCharges` per service.
* Azure: Cost Mgmt budgets per resource group.
* GCP: Cloud Billing budget per project.
* ElevenLabs / Deepgram: synthetic poll of provider dashboards via Azure Logic App.

**§I** — Breach → Telegram + email + freeze auto-deploy of new cost-heavy features.

---

## §6 Background Jobs — Deep Dive (KEDA cron triggers on Azure Container Apps Jobs)

For each job, the per-job spec follows the same template. Below: the operationally critical jobs get full §A–§I; routine jobs get a compact row. All jobs run on Azure Container Apps Jobs with `replicaTimeout=3600`, `replicaRetryLimit=3`, and KEDA cron trigger.

### 6.1 `seo-auto-publish` — `*/15 * * * *`

| Section | Detail |
|---|---|
| §A | KEDA cron fires container; container reads pending topics from Mongo. |
| §C | All env from §0.1 + Bing/IndexNow/CF API keys. |
| §D | Mongo readWrite + S3 PutObject + CF zone purge token. |
| §E | per-topic 30s timeout, 3 retries, DLQ via SQS `seo-publish-dlq`. |
| §G | rolled into 4.3 — ~$8/mo total. |
| §H | `seo_publish_total{status}`, alert if `failed > succeeded` for 1 hr. |
| §I | DLQ replay via admin endpoint `POST /api/admin/seo/replay-dlq`. |

### 6.2 `internal-linker` — `0 2 * * *`

§A–I — see 4.2. Job idempotent, dry-run mode available via env `LINKER_DRY_RUN=true`.

### 6.3 `topic-discovery` — `0 2 * * *`

§A–I — see 4.1.

### 6.4 `bing-submit-daily` — `0 4 * * *`

| Section | Detail |
|---|---|
| §A | Submit `https://syrabit.ai/sitemap.xml` to Bing Webmaster `/SubmitSitemap`. |
| §G | $0. |
| §H | `bing_sitemap_submit_status` last-success-ts; alert if no success in 48h. |
| §I | Failures auto-retry next day; sitemap updates eventually consistent. |

### 6.5 `seo-weekly-digest` — `30 3 * * 1`

| Section | Detail |
|---|---|
| §A | Aggregate per-cloud cost + traffic + ranking + top-10 publish wins → SES email to founder. |
| §G | $0 (within SES free 62k emails/mo). |

### 6.6 `exam-reminder` — `*/5 * * * *`

| Section | Detail |
|---|---|
| §A | Mongo query: `{notify_at: {$lte: now+5min, $gte: now-5min}}`; CF Worker dispatches via FCM/APNs. |
| §G | $0 (free tiers FCM/APNs + Mongo). |
| §H | `exam_reminders_sent_total{channel}`, `exam_reminder_send_failure_rate`. |
| §I | FCM 5xx → SQS retry queue; APNs 410 (token gone) → DB cleanup via 6.7. |

### 6.7 `push-prune` — `0 3 * * *`

§A — Mongo `push_tokens` delete where `last_seen < now - 30 days OR status=revoked`.

### 6.8 `sitemap-indexnow-diff` — `0 * * * *`

§A — Compare sitemap.xml (CF R2) vs prior hour snapshot; POST diff to Bing + Yandex IndexNow.

### 6.9 `grounded-recall-nightly` — `0 2 * * *`

| Section | Detail |
|---|---|
| §A | Run 200 canned questions per locale (en/as/hi/bn), measure citation-presence + answer-correctness. Persist to `grounded_recall_results` collection. |
| §G | ~$2/mo (small Vertex spend). |
| §H | `grounded_recall_score{locale}` SLO ≥ 0.85; alert if any locale drops below 0.75. |
| §I | Drop > 0.10 in any locale → P1 incident, RAG-quality investigation. |

### 6.10 `alerting` — `*/2 * * * *`

§A — Health probes for App Runner, Azure Container Apps, R2, Vertex, ElevenLabs, Deepgram → Telegram on `down`.

### 6.11 `unified-logs-cf-pull` — `*/15 * * * *`

§A — CF Logs GraphQL → Mongo `logs_cf_15min`. Enables unified search alongside Axiom (which holds backend logs only).

### 6.12 `trustpilot-feed-alert` — hourly

§A — Trustpilot API `/v1/business-units/{id}/reviews` → Mongo persist → sentiment delta vs 30-day rolling avg → Telegram if drop > 0.5 stars.

### 6.13 `cf-waf-drift-cron-alert` — hourly

§A — `GET /zones/{id}/firewall/rules` vs committed `infra/cf-waf-rules.json`; Telegram on drift (security-sensitive).

### 6.14 `bing-keyword-refresh` — daily

§A — Bing Webmaster keyword research API → Pinecone upsert into `seo_keywords` namespace.

### 6.15 `daily-mongo-s3-backup` — `0 3 * * *`

| Section | Detail |
|---|---|
| §A | `mongodump --gzip` → S3 `s3://syrabit-prod-backups/mongo/{yyyy-mm-dd}.tar.gz`. |
| §D | App Runner SA: `s3:PutObject` on backups bucket; lifecycle policy: Glacier after 30 days, expire after 365. |
| §G | ~$1/mo (S3 storage; Glacier transitions free). |
| §I | Backup fail → Telegram P1; founder runs manual `mongodump` if cron fails 3 nights in a row. |

### 6.16 `weekly-pinecone-reindex` — Sun 02:00 IST

§A — Mongo cursor over docs with `reindex_pending=true` → Bedrock embed → Pinecone upsert (idempotent on doc_id).

### 6.17 `weekly-cost-report-email` — Mon 09:00 IST

§A — CloudWatch + Azure Cost Mgmt + Vertex Billing → SES email (recipient `founder@syrabit.ai`).

### 6.18 `dead-endpoint-pruner` — weekly

§A — Hit all known routes from OpenAPI spec; Sentry alert on > 1% 4xx/5xx (suggests removal).

---

## §7 PROVIDER_PRIORITY + POOL_WEIGHTS — Deep Dive

### 7.1 Why these dicts exist

The dispatcher uses `PROVIDER_PRIORITY` to choose Tier-1 → Tier-2 → Tier-3 order at runtime, and `POOL_WEIGHTS` to share load across providers within a tier (when configured). Editing these dicts has live blast-radius — every chat, OCR, voice, and SEO-publish call routes through them.

### 7.2 Editing policy (binding)

* **NEVER** edit on aspirational credit grants. Edits are gated on:
  1. Credit grant LANDED (status in `credit-applications.md` → `landed`).
  2. ≥ 7 days of staging soak.
  3. Code review approval from architect.
  4. Atomic deploy; revert plan tested.
* Editing is logged as Axiom audit event `provider.priority.edit` with diff.
* Admin UI panel (`AdminVertexPanel.jsx`) provides a read-only view of the live config + a "propose change" workflow that creates a PR.

### 7.3 Drift register (current state, target state)

| Key | Current | Target | Reason for drift | Action |
|---|---|---|---|---|
| `openai` priority | 1 | 2 | Legacy from pre-Vertex era | Demote in next refactor PR |
| `anthropic` priority | 2 | (removed) | Bedrock is Cohere-only — no Anthropic in strategic chain | Remove |
| `google_vertex` priority | 3 | 1 | Should be primary for chat | Promote |
| `azure_openai` priority | 4 | 2 (or 3 after openai demoted) | Tier-2 chat | Promote |
| `mistral_azure` priority | 5 | (removed) | Not in strategic plan | Remove |
| `pool_weight: gpt-4o` | 0.70 | (removed; replaced by gemini-2.5-flash 0.70) | Model-id rename + provider re-prioritization | Replace |
| `pool_weight: claude-3-5-sonnet` | 0.20 | (removed) | Anthropic not in chain | Remove |
| `pool_weight: gpt-4.1-mini` | (absent) | 0.25 | Strategic Tier-2 | Add |
| `pool_weight: @cf/meta/llama-3.3-70b-instruct-fp8-fast` | (absent) | 0.05 | Strategic Tier-3 | Add |

### 7.4 Migration plan (when triggered)

```
Step 1: Land Vertex Gemini 2.5 Flash quota raise → confirm 100 RPS sustained.
Step 2: Deploy refactor PR — single-line edits to PROVIDER_PRIORITY + POOL_WEIGHTS.
Step 3: Stage 7-day soak — measure p95 latency parity vs current openai-primary.
Step 4: Cut over via 10% → 50% → 100% canary using AI Gateway weighted routing.
Step 5: Remove anthropic + mistral_azure providers from dispatcher.py.
Step 6: Update auxiliary-providers-delegation.md + this doc's §7.3 drift register.
```

---

## §8 Per-pool 10k DAU draw — line-by-line totals

Identical math to `feature-to-provider-mapping-detailed.md` §8. Repeated here for completeness so this doc is self-contained when read in isolation. **If figures diverge, the mapping doc is canonical** — open a PR to reconcile.

(See companion doc for the 5-pool table; combined headline: **~$339/mo** at **$0 cash** with **~$917/mo headroom**.)

---

## §9 How this doc relates to its companions

| Companion | What it answers | Relation to this doc |
|---|---|---|
| `feature-to-provider-mapping-detailed.md` | "What's each feature's provider chain?" | **This doc adds: req/resp shape, env, IAM, retry, prompt, pricing math, monitoring, runbook.** |
| `feature-to-provider-audit.md` | "Are we zero-cash at 10k DAU?" | This doc's §G blocks must reconcile with audit's §1 line items. |
| `cloud-allocation-plan.md` | "Which cloud owns which workload?" | This doc operates within that allocation. |
| `cloud-service-breakdown.md` | "What does each AWS/Azure/CF/GCP service do?" | This doc cites concrete services. |
| `auxiliary-providers-delegation.md` | "Why each non-cloud provider exists?" | This doc cites the role; do not duplicate. |
| `credit-applications.md` | "PENDING vs LANDED credits?" | §7.2 editing-policy gate references this. |

---

## §10 Re-running this deep-dive

```bash
# 1. For a feature, dump req/resp shape from OpenAPI spec
rg -n "operationId.*chat_stream" artifacts/syrabit-backend/openapi.yaml -A 50

# 2. Dump env-var consumption
rg -n "os\\.getenv\\(|os\\.environ\\[" artifacts/syrabit-backend/api/<handler>.py

# 3. Dump retry + timeout config
rg -n "@retry|@circuit_breaker|httpx\\.Timeout" artifacts/syrabit-backend/

# 4. Dump prompt skeleton
rg -n "SYSTEM|prompt_template" artifacts/syrabit-backend/prompts/

# 5. Dump monitoring metric names + alert rules
rg -n "Counter\\(|Histogram\\(|Gauge\\(" artifacts/syrabit-backend/
rg -n "alert_threshold|severity" artifacts/syrabit-backend/infra/alerts/

# 6. Reconcile pricing math
# diff §G of every feature here against §8 of the mapping doc
```

> Re-run any time a feature ships, a provider is added, a credit grant lands, or `PROVIDER_PRIORITY` / `POOL_WEIGHTS` is edited.
