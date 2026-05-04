# Per-Cloud Feature Delegation — v3 Canonical Spec

> **This is the single source of truth.** All later infra changes must
> point at this doc instead of negotiating between competing markdowns.
> Companion docs:
> - `infra/provider-priority-map.md` — machine-friendly per-feature table.
> - `infra/credit-burn-runbook.md` — flag mechanics, meters, escalation.
> - `infra/capacity-roadmap-363.md` — capacity tier (Mongo sharded,
>   Redis multi-shard, Pinecone scale-out, Vertex co-primary,
>   async-batch isolation, 500k–1M DAU load test). See Task #363.
> - `infra/perf-roadmap-361.md` — perf tier (RAG/embed cache,
>   fast-mode 1b vs 3b A/B, p99 instrumentation, ROI gate +
>   kill-switch). See Task #361.
> - `infra/features-roadmap-362.md` — features tier (deep recall via
>   summary-vector embedding gated by recall-intent detector,
>   mixed-language en↔as UX metrics, per-session sticky model
>   fallback with anti-thundering-herd guard, friendlier moderation
>   UX with safe/default/challenge modes + non-negotiable safety
>   floors). See Task #362.
>
> **Provider removals (OpenAI, Anthropic, Bedrock, Stripe, Quge5,
> Resend, Grok, Railway, DigitalOcean) are tracked in Task #347.**

**Status:** locked v3 — 2026-05-04
**Owner:** founder@syrabit.ai

---

## §0 — Four-cloud delegation map (canonical opening, verbatim)

- **Cloudflare — edge, routing, and RAG-primary.** All Workers-AI
  exam-Q&A and fast-mode RAG (including Assamese Indic translation via
  `@cf/ai4bharat/indictrans2-en-indic-1b`). Cloudflare Vectorize as the
  edge vector cache, with Pinecone as the main vector store. Cloudflare
  Workers + R2 / KV for edge state, A/B flags, and routing. Cloudflare
  AI Gateway BYOK paths to Google AI Studio / Gemini and Azure OpenAI
  (the production path for Gemini; direct Vertex SDK is rollback-only).

- **Azure — backend, auth, AI safety.** Python FastAPI and Rust core on
  Azure Container Apps (two separate apps); Python owns the live-chat
  hot path; Rust owns async-batch (see Latency Rule 7). Fronted by
  Cloudflare. Azure AI Content Safety as moderation-secondary (after
  Llama Guard). Azure Translator as fallback for Indic→English-only
  translation. SendGrid (provisioned at launch via **Azure Marketplace
  on the Pro 100k tier** — zero out-of-pocket against the $200 Azure
  signup credit and then Microsoft for Startups Azure credits — with
  **Essentials Free, 100 emails/day** as the always-available fallback
  tier; mandatory migration to a paid Pro tier funded outside the credit
  pool before the Microsoft for Startups credit window lapses) as
  primary transactional email; AWS SES is the volume backstop per
  `EMAIL_FALLBACK`.

- **Google Cloud / Vertex — validation-only and safety-only in v3.**
  Vertex Gemini 2.5 Flash is the default content-validation model
  (independent family from the primary chain; **not** primary for RAG /
  MCQ / notes / translation). It runs on a **10% sample** of completed
  turns post-response (off the critical path) — never per-turn
  synchronous on the live chat path (see Latency Rule 12). Gemini RAI
  is opt-in only for `content_type = "exam_model_paper"` and runs
  synchronously inside that flow's async batch only. Web Risk API for
  malicious-URL checks. Cloud Billing API feeds daily-budget / cost-cap
  alerts. **Vertex Vector Search (Matching Engine) and Vertex Discovery
  Engine are retired** from primary RAG / grounded-search chains; kept
  only as rollback. Cloud Vision / TTS / STT / Cloud Translation remain
  callable but are not primary — those roles route through Cohere-direct,
  Workers-AI, Sarvam, ElevenLabs, Deepgram, AssemblyAI, and Azure
  Translator.

- **AWS — serverless workers, storage, eventing (free-tier-friendly at
  100k DAU).** S3, SQS, Lambda, EventBridge, DynamoDB, SNS, Step
  Functions, CloudWatch. Event-driven backend workers run on Lambda /
  Step Functions (not the main HTTP face); Cloudflare fronts all
  user-facing traffic.

---

## §1 — Top-line policy

1. **One spec.** v3 is the only authoritative provider/RAG/infra map.
   Older docs are archived or carry a back-pointer to this file.
2. **Memory + RAG separation** (full text in §2).
3. **Per-turn order is fixed** (see §2.4).
4. **Day-0 paid tiers everywhere** funded by per-provider startup credits
   (see §11). No production hot-path component silently falls back to a
   shared / free tier when credits lapse.
5. **Three independent fallback rules** — Rules A and B auto-flip
   `CHAT_FALLBACK=1`; Rule C is notify-only (see §10).
6. **Razorpay scope = INR-only** (matches #347's India-first launch).
   International cards are explicitly out of scope until a future task
   revisits global payments.
7. **Fast-mode primary = `@cf/meta-llama/Llama-3.2-3B-Instruct`** (matches #347).
8. **Deployment topology** = two separate Azure Container Apps (Python
   FastAPI and Rust core), see Latency Rule 7.

---

## §2 — Memory + RAG separation policy

### 2.1 MongoDB Atlas = memory-brain

Before any assistant response is generated, the backend retrieves the
current chat-session history (last N turns from `conversations`, N
bounded by token budget) and the compact `user_profile` document
(preferences, weak subjects, language pair, exam-mode flag) from
MongoDB and injects them into the prompt. After the model replies, the
new user + assistant messages are written back to `conversations`, and
an optional `user_profile` summary update may be queued.

### 2.2 `user_profile` document schema (canonical)

The executor implements this as-is. **Additive fields allowed; renames
forbidden without a follow-up spec change.**

- `_id: ObjectId`
- `user_id: string` — unique, indexed (`{user_id: 1}` unique). Canonical
  shard key for #363 Step 2.
- `created_at: ISODate`, `updated_at: ISODate`
- `preferred_language: string` — BCP-47 (`en`, `as`, `hi`, …); seeds the
  Indic chain selection.
- `language_pair: { input: string, output: string }` — defaults to
  `{input: preferred_language, output: preferred_language}`;
  `{input:"en", output:"as"}` triggers the #362 mixed-language path.
- `exam_mode: { active: bool, board: string|null, level: string|null }` —
  `board` ∈ {`AHSEC`, `CBSE`, `degree`, …}; `level` ∈ {`class_11`,
  `class_12`, `degree_year_1`, …}.
- `weak_subjects: string[]` — subject slugs the user has scored low on;
  injected into RAG retrieval as a soft bias.
- `preferences: { fast_mode_default: bool, response_length: "short"|"medium"|"long" }` —
  UI-driven, defaults filled at signup.
- `moderation_mode: "safe" | "default" | "challenge"` — the per-user
  moderation knob from #362 Step 4; defaults to `default`. Treated as a
  hard floor: any change is logged with timestamp + previous value,
  audit log retained 90 days.
- `summary: { text: string, last_updated: ISODate, source_turn_count: int } | null` —
  the rolling profile summary updated by the off-critical-path
  summarizer (#360 Step 1); read on every turn, never blocks the
  response.
- `flags: { early_adopter: bool, beta_features_opt_in: bool, ... }` —
  append-only feature-flag bag for product experiments.

**Size budget:** the entire document must stay under **4 KB** so the
per-turn read is cheap (single document, single shard, `find` by
`user_id` returns one IXSCAN row). Long-form data (full conversation
history, RAG embeddings) lives in `conversations` / Pinecone, never in
`user_profile`.

### 2.3 Pinecone = knowledge-brain

Pinecone is reserved for semantic search over external content (syllabus,
notes, model papers); it is the RAG layer and never stores conversation
turns.

### 2.4 Per-turn order (fixed)

```
(1) load chat_history + user_profile from Mongo
(2) RAG-retrieve from Pinecone
(3) moderate input
(4) dispatch to LLM with both contexts in the prompt
(5) moderate output  ← runs server-side concurrently with token streaming (Rule 11)
(6) write turn back to Mongo  ← only if stream completes without moderation veto
```

The implementation task wires this exactly; the spec is the contract.

### 2.5 Token budget rule

If the combined Mongo history + RAG context would exceed the active
model's context window, the history is summarized (oldest-first) before
truncation; raw chunks are never silently dropped. The summarizer model
is the same fast-mode model (`@cf/meta-llama/Llama-3.2-3B-Instruct`) used
elsewhere.

---

## §3 — RAG chains

### 3.1 Default chat chain (English exam-Q&A)

| Tier | Provider slug | Model | Notes |
|---|---|---|---|
| primary | `workers_ai` | `@cf/mistral/mistral-7b-instruct-v0.3` | Cloudflare Workers-AI, edge-anchored |
| secondary | `azure_openai` | `gpt-4.1-mini` | Auto-target on `CHAT_FALLBACK=1` |
| tertiary | `workers_ai` | `@cf/openai/gpt-oss-20b` | Edge fallback |

### 3.2 Fast-mode chain

| Tier | Provider slug | Model | Notes |
|---|---|---|---|
| primary | `workers_ai` | `@cf/meta-llama/Llama-3.2-3B-Instruct` | Locked per #347 |
| secondary | `azure_openai` | `gpt-4.1-mini` | |

### 3.3 Async chain (long-running content gen, off the critical path)

Owned by the Rust ACA app (see Rule 7). Routes:

| Tier | Provider slug | Model |
|---|---|---|
| primary | `workers_ai` | `@cf/openai/gpt-oss-20b` |
| secondary | `azure_openai` | `gpt-4.1-mini` |

---

## §4 — Embeddings + rerank + vector stack

### 4.1 Embeddings

- **Hot path:** `embed_hotpath` resolves to `@cf/baai/bge-m3` (edge,
  ~20–40 ms). The dispatcher must issue `embed(user_msg)` immediately
  and run `gather(mongo_history_load, embed.then(pinecone_query))` so
  embed + history-load + retrieval all overlap (Rule 2).
- **Async batch:** `embed_batch` resolves to Cohere-direct
  (`embed-multilingual-v3.0`).
- Vertex `text-embedding-004` is **retired** (rollback-only).

### 4.2 Rerank

| Tier | Provider | Model |
|---|---|---|
| primary | `cohere` | `rerank-multilingual-v3.0` |
| secondary | `voyage_ai` | `rerank-2` |
| tertiary | `workers_ai` | `@cf/baai/bge-reranker-base` |

Graceful degrade: skip rerank if all fail; return Pinecone topK as-is.

### 4.3 Vector stack

- **Live primary:** Pinecone (`syrabit-rag`, 1024-dim cosine,
  `aws-us-west-2`).
- **Edge cache:** Cloudflare Vectorize.
- **Batch / rollback:** Vertex Vector Search retained as rollback only;
  not in primary chain.

---

## §5 — Moderation chain + failure modes

### 5.1 Default moderation chain

| Position | Provider | Model | Failure mode |
|---|---|---|---|
| primary | `workers_ai` | `@cf/meta/llama-guard-3-8b` | fail-open if both primary + secondary error (live chat path) |
| secondary | `azure_openai` | `azure-ai-content-safety` | |

### 5.2 `exam_model_paper` moderation

Synchronous inside the async-batch flow only (Rule 4). Adds Vertex
Gemini RAI as a third tier; **fail-closed** — if any tier errors, the
content is held for human review.

### 5.3 Output moderation = streaming-compatible (Rule 11)

Tokens stream to the client over SSE while Llama Guard + Azure AI
Content Safety run server-side concurrently. On a moderation veto the
stream is aborted mid-flight and the turn is **not** committed to Mongo.
Buffer-then-moderate is forbidden on the live chat path. First-token
TTFB SLO < 1 s.

---

## §6 — Validation chain (Vertex Gemini, sampled)

- Default: Vertex Gemini 2.5 Flash runs on a **10% sample** of completed
  turns post-response (off the critical path). Configurable via
  `VALIDATION_SAMPLE_RATE`.
- `content_type = "exam_model_paper"`: validated synchronously inside
  its async batch flow only.
- Per-turn synchronous Vertex validation on the live chat path is
  **forbidden** — adds 200–500 ms cross-cloud per turn (Rule 12).

---

## §7 — AWS free-tier plane

Serverless workers, storage, eventing — sized to fit AWS free tier at
100k DAU.

| Service | Role |
|---|---|
| S3 | Object store (PDFs, audio, backups). Sole blob store. |
| SQS | Async work queue feeding Lambda consumers. |
| Lambda | PDF parse, email worker, content batch jobs. ARM64. |
| EventBridge | Cron + event fan-out. |
| DynamoDB | Credit-burn meter backstop (Rule 9 backstop tier). |
| SNS | Alerting fan-out for CloudWatch alarms. |
| Step Functions | Multi-stage async batch orchestration. |
| CloudWatch | AWS-native alarms only (APM canonical = Azure App Insights). |

---

## §8 — Azure plane

| Service | Role |
|---|---|
| Container Apps (Python FastAPI) | **Live-chat hot path.** Owns per-turn dispatch. `minReplicas: 1` in prod (Rule 8). |
| Container Apps (Rust core) | Async-batch only (Rule 7). `minReplicas: 1` in prod (Rule 8). |
| Azure OpenAI | `gpt-4.1-mini` for `CHAT_FALLBACK=1` exam-Q&A. |
| Azure AI Content Safety | Moderation-secondary. |
| Azure Translator | Fallback for Indic→English-only. |
| Container Apps Jobs | KEDA-cron for scheduled jobs. |
| Application Insights | Central APM sink (all three hosting clouds). |
| Key Vault | Runtime secrets. |

---

## §9 — Cloudflare plane

| Service | Role |
|---|---|
| Pages | Frontend SPA. |
| Workers (edge proxy) | mTLS to Azure origin, WAF, Turnstile, rate-limit. |
| Workers AI | RAG-primary (default + fast-mode); IndicTrans2 translate. |
| AI Gateway | BYOK to Google AI Studio (Gemini) + Azure OpenAI. |
| Vectorize | Edge vector cache. |
| R2 | Cold blob archive. |
| KV | Session shards, hot flags. |
| Workers Browser Rendering | `live_search` headless fetch. |

---

## §10 — Fallback policy (three independent rules)

The runbook in `infra/credit-burn-runbook.md` carries the operational
detail; the three rules are restated here as the contract.

### Rule A — daily-call ceiling (auto-flip)

> *"When the count of RAG API calls in a single UTC day exceeds 10,000,
> flip `CHAT_FALLBACK=1`. Secondary exam-Q&A traffic shifts to Azure
> GPT-4.1-mini until the next UTC day rollover (00:00 UTC), unless
> on-call has pinned the flag."*

### Rule B — RPM-headroom guard (auto-flip)

> *"When Workers-AI RAG-call rate reaches 70% of the published platform
> RPM limit for the currently-active Workers-AI model (measured over a
> rolling 1-minute window), flip `CHAT_FALLBACK=1`. Auto-clear when
> usage drops below 50% of the RPM limit for 5 consecutive minutes,
> unless on-call has pinned the flag."*

The exact RPM limit per model lives in the runbook and must be refreshed
whenever the active model changes (e.g., `mistral-7b-instruct-v0.3` →
`gpt-oss-20b`).

**Tunable note (in the runbook, not in code):** Defaults are 70% trip /
50% auto-clear / 5-min sustain / 1-min sliding window. If traffic
patterns change — short bursty spikes are normal but sustained load
stays low — relax to e.g. 75% trip / 45% auto-clear; if you want more
headroom safety, tighten to e.g. 65% trip / 55% auto-clear. Any change
must be backed by a short load-test that validates the new thresholds
against the active model's published RPM and must be recorded in the
runbook with the date, the operator, and the reason. Do **not** convert
Meter B into a notify-only or long-window cost-style rule — those roles
belong to Meters A/C respectively.

### Rule C — cumulative cost alert (notify-only)

> *"When cumulative Workers-AI RAG cost exceeds 70% of $5k over a
> 365-day rolling window, post a high-priority alert to the on-call
> channel."*

This rule **does not** flip any flag on its own; on-call decides whether
to flip `CHAT_FALLBACK=1` manually based on remaining runway and
traffic shape.

### Rule interaction

Rules A, B, and C operate independently and may fire at different times.
If either auto-flip rule (A or B) is active, `CHAT_FALLBACK=1` stays set
even after the other clears, until both clear or on-call resets.

### Flag mechanism (sub-ms propagation)

`CHAT_FALLBACK` and `CHAT_FALLBACK_PIN` are **Redis hot-flags**
(`chat:fallback`, `chat:fallback:pin`) read by the dispatcher on every
turn. The Azure Container Apps env vars are the durable cold-start
default only; flip-propagation must not depend on an ACA revision
rollout (which is ~30–60 s and would let the limited model keep taking
traffic during a flip).

---

## §11 — All-provider credit policy (day-0 paid tiers everywhere)

Syrabit holds **base-tier startup credits from every provider in the
stack**:

- Microsoft for Startups → Azure / SendGrid via Marketplace
- AWS Activate → AWS
- Cloudflare for Startups → Workers-AI Paid + Pinecone-equivalent /
  Vectorize / R2 / KV
- MongoDB Atlas for Startups → M10+ dedicated cluster
- Pinecone for Startups → Standard tier
- Upstash for Startups → Pro / paid Redis
- Google for Startups → Vertex / GCP
- Cohere for Startups → paid embed/rerank
- Razorpay startup program → live mode

All hot-path components are therefore provisioned on **paid / dedicated
tiers from day-0** — not free-tier. The runbook
(`infra/credit-burn-runbook.md`) carries a per-provider credit-window
table with: provider, credit balance, expected exhaustion date, the
**mandatory migration-to-direct-paid-billing checkpoint** (set 25–30
days before exhaustion per provider), the migration owner, and the
documented rollback path if direct paid billing is not in place by the
deadline.

**No production hot-path component is allowed to silently fall back to
a shared / free tier when credits lapse** — every provider has either
an explicit paid-billing handoff or a documented degraded-mode fallback
that ships an alert.

---

## §12 — SendGrid (locked decision)

- **Day-0 tier:** SendGrid Pro 100k via Azure Marketplace (zero
  out-of-pocket against the $200 Azure signup credit, then carried by
  Microsoft for Startups Azure credits).
- **Always-available fallback tier:** SendGrid Essentials Free
  (100 emails/day).
- **Day-1 capacity:** 100,000 emails/month.
- **Mandatory clause:** before the Microsoft for Startups Azure credit
  window lapses, the SendGrid plan **must be migrated onto a paid Pro
  tier** funded outside the credit pool. Migration owner, renewal-window
  calendar checkpoint, and rollback-to-Essentials-Free path live in
  `infra/credit-burn-runbook.md`.
- **Volume backstop:** AWS SES (62k/mo free from EC2, or $0.10/1k
  pay-as-you-go) via `EMAIL_FALLBACK`.

**MAU × email-frequency math behind the 100k/month sizing:**
~50k MAU × ~2 transactional emails/MAU/month (signup confirmation,
password reset, weekly digest, billing receipts) ≈ 100k/month at peak.
SendGrid Pro 100k tier covers exactly this with headroom for retries.

---

## §13 — Indic / Assamese chains

### 13.1 Translation

| Tier | Provider | Model |
|---|---|---|
| primary | `workers_ai` | `@cf/ai4bharat/indictrans2-en-indic-1b` |
| secondary | `vertex` | Cloud Translation v3 + Gemini polish |
| tertiary | `sarvam` | Sarvam-Translate Indic-first |
| rollback | `azure_openai` | Azure Translator (Indic→English-only) |

### 13.2 Assamese chat

| Tier | Provider | Model |
|---|---|---|
| primary | `vertex` | Gemini 2.5 Flash with Indic-tuned prompt |
| secondary | `sarvam` | Sarvam-M Indic-native |
| tertiary | `azure_openai` | `gpt-4.1-mini` translate-then-answer |
| edge | `workers_ai` | IndicTrans2 → `gpt-oss-20b` |

### 13.3 Assamese content

| Tier | Provider | Model |
|---|---|---|
| primary | `vertex` | Gemini 2.5 Flash translate+adapt |
| secondary | `sarvam` | Sarvam-M Indic-native |
| edge | `workers_ai` | IndicTrans2 |

---

## §14 — Email + payments

### 14.1 Email

Locked in §12. `EMAIL_FALLBACK` env flag (Redis hot-flag
`email:fallback`) routes between SendGrid Pro 100k → SendGrid Essentials
Free → AWS SES.

### 14.2 Payments

- **Razorpay = INR-only**, locked. Live mode via Razorpay startup
  program credit.
- **International cards** explicitly out of scope until a future task
  revisits global payments.
- Stripe is removed (#347).

---

## §15 — SLOs + error budget

| Metric | SLO |
|---|---|
| Live-chat first-token TTFB | p95 < 1.0 s, p99 < 2.0 s |
| Live-chat full response | p95 < 6.0 s |
| `embed_hotpath` latency | p95 < 60 ms |
| Pinecone `vector_retrieve` | p95 < 80 ms |
| Mongo `user_profile` load | p95 < 25 ms (single-doc IXSCAN) |
| Output-moderation veto rate | < 0.5% (excludes `exam_model_paper` flow) |
| Validation sample completion | within 5 min of source turn |

**Error budget:** 0.1% per 30-day rolling window for full-response SLO.
When the budget is exhausted, freeze risky deploys; when the budget is
restored for 7 consecutive days, resume.

---

## §16 — Latency budget & hot-path rules (the 12-rule list)

1. **Region anchor.** Pinecone, Mongo, ACA, AI Gateway origin co-located.
   Pin one side that moves: **Mongo + ACA → Pinecone's anchor
   (`aws-us-west-2`)** because the Pinecone index is the more expensive
   side to relocate (re-embed of the entire `syrabit-rag` corpus).

2. **Concurrent embed + Mongo + Pinecone reads.** Embed pinned to
   `@cf/baai/bge-m3`; `gather(mongo_load, embed.then(pinecone_query))`.

3. **Fire-and-forget assistant write.** Step (6) of §2.4 is enqueued
   off the response path; user sees the assistant turn before the Mongo
   write commits.

4. **`exam_model_paper` moderation batch-only.** Never on the live
   chat path.

5. **Off-critical-path summarizer.** `user_profile.summary` updates
   are queued (Rule 4-style); never block the response.

6. **Cloudflare AI Gateway latency trade-off.** Accepted for Meter-C
   telemetry — AI Gateway adds ~10–20 ms but gives us the cumulative-cost
   visibility Rule C depends on.

7. **Python ↔ Rust division of labor (hard rule).** Two separate ACA
   apps. **Python FastAPI** owns the live-chat hot path: per-turn
   dispatch, Mongo read, Pinecone query, LLM call, streaming output.
   **Rust core** owns async-batch only: PDF ingest → embed → upsert,
   `exam_model_paper` synchronous moderation, summarizer, validation
   sampling. The Python app never blocks on a Rust call on the live
   chat path; communication is always through SQS/EventBridge enqueues.

8. **ACA `minReplicas: 1` in prod.** Both Python and Rust apps. Cold
   starts on the live chat path are unacceptable.

9. **Meter A is Redis-on-hot-path / Dynamo-backstop.** The daily-call
   counter increments in Redis on every RAG call (sub-ms); a periodic
   batch syncs to DynamoDB for durable cross-day rollover and audit.

10. **Hot-path HTTP clients are pooled and keep-alive.** Process-singleton
    clients (Mongo, Pinecone, Cohere, AI Gateway, Azure OpenAI direct,
    Vertex direct rollback, Razorpay, SendGrid) attached to FastAPI
    lifespan with explicit `max_connections` /
    `max_keepalive_connections`. Mongo `maxPoolSize ≥ 50`. Without
    this, fresh TCP+TLS handshake per call adds 50–200 ms each; at 5
    outbound calls per turn that's a hidden 250 ms–1 s.

11. **Output moderation is streaming-compatible.** Tokens stream to the
    client over SSE while Llama Guard + Azure AI Content Safety run
    server-side concurrently. On a moderation veto the stream is
    aborted mid-flight and the turn is **not** committed to Mongo.
    Buffer-then-moderate is forbidden on the live chat path. First-token
    TTFB SLO < 1 s.

12. **Vertex Gemini validation is sampled, not per-turn.** Default
    content validation runs on a 10% sample of completed turns
    post-response (off the critical path). `content_type =
    "exam_model_paper"` is validated synchronously inside its async
    batch flow only. Per-turn synchronous Vertex validation on the live
    chat path is forbidden — it adds 200–500 ms cross-cloud per turn.
    Sampling rate is configurable via `VALIDATION_SAMPLE_RATE` and
    recorded in the runbook.

13. **Two-stage cache lookup before pipeline (post-#361).** On every
    turn: (a) compute the content-normalized query hash; (b) read
    `cache:rag_enabled` and the RAG-result cache by key
    (`rag:syllabus:<curriculum_version>:query_hash:<hash>`); (c) on
    hit, serve the cached `(rag_chunks, llm_answer)` and skip the
    entire pipeline (Pinecone query + LLM call). On miss, proceed to
    the standard concurrent-read path (Rule 2). The embed cache
    (`embed:question:<hash>`) is consulted inside the embed step
    (between user-message hash and `bge-m3` call). Both caches are
    populated **only by stable queries** (no per-user attachments,
    no ephemeral hints, syllabus version matches). Both lookups are
    sub-5 ms; their cost on miss is negligible compared to the
    pipeline they short-circuit on hit. Cache served only after the
    #361 §6.2 promotion gate clears; default `cache:rag_enabled=0`.
    See `infra/perf-roadmap-361.md` for the full spec.

14. **Recall-intent gate is mandatory before any summary-vector
    Pinecone query (post-#362).** The summary-embedding lookup adds
    ~30–60 ms; running it on every turn would add that to the p50
    envelope. The two-tier detector (Tier 1 phrase / `@recall`
    prefix; Tier 2 anaphoric-token trigger plus a 1-token
    Llama-3.2-3B classifier) keeps the unconditional-cost addition
    at zero on the ~90% of turns that don't pass either tier,
    ~50 ms on the ~5–10% of turns that hit Tier 2's classifier
    (without then querying Pinecone), and the full ~30–60 ms only
    on turns where recall-intent is actually detected. Per-turn
    synchronous summary-vector queries on every chat turn are
    forbidden. The phrase + token lists live in Redis
    (`recall_intent:tier1_phrases`, `recall_intent:tier2_tokens`)
    so on-call can edit them without a code deploy. See
    `infra/features-roadmap-362.md` §1 for the full spec.

---

## §17 — Out of scope for this spec

- Code changes to dispatcher / workers / env flags (next task).
- Provider removals (handled by #347).
- Building the Lambda + DynamoDB credit-burn meter itself (only the
  *spec* of the meter lives here).
- New SLO dashboards in Grafana / Cloudflare Analytics (spec only).
