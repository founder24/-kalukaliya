# Provider Priority Map — v3 Canonical (machine-friendly)

> **Provider removals (OpenAI, Anthropic, Bedrock, Stripe, Quge5,
> Resend, Grok, Railway, DigitalOcean) are tracked in Task #347.**
>
> Companion docs:
> - `infra/per-cloud-feature-delegation.md` — full v3 spec.
> - `infra/credit-burn-runbook.md` — flag mechanics + meters.
> - `infra/capacity-roadmap-363.md` — capacity-tier table extensions
>   (sharded Mongo / Redis multi-shard / Pinecone namespace-shard +
>   separate batch index / Vertex chat co-primary). See Task #363 §A.
> - `infra/perf-roadmap-361.md` — perf-tier (RAG/embed cache,
>   fast-mode 1b vs 3b A/B, p99 instrumentation). See Task #361.
> - `infra/features-roadmap-362.md` — features-tier (deep recall
>   via summary-vector embedding gated by recall-intent detector,
>   mixed-language en↔as UX metrics, per-session sticky model
>   fallback with anti-thundering-herd guard, friendlier moderation
>   UX with safe/default/challenge modes + non-negotiable safety
>   floors). See Task #362.

**Status:** locked v3 — 2026-05-04

---

## Table schema (binding)

Every per-feature table below uses **these exact columns, in this order**:

- `tier` — one of `primary` / `secondary` / `tertiary` / `rollback_only`.
- `provider_slug` — canonical short slug used in `_dispatch_llm_for_feature`.
- `model_id` — exact identifier the SDK / API call uses. Empty string
  for non-model providers (vector DBs, email, payments).
- `region` — anchor region for the call. Required for hot-path entries;
  informational for batch.
- `notes` — free-text caveats.

Each table ends with a `--- removed ---` divider listing slugs explicitly
removed by #347 so future PRs can't silently re-add them.

The dispatcher's resolver code reads the same column names; a CI check
parses this markdown and asserts every `primary` row has a corresponding
implementation.

---

## chat_default

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | workers_ai | @cf/mistral/mistral-7b-instruct-v0.3 | cf-edge | RAG-primary, edge-anchored |
| secondary | azure_openai | gpt-4.1-mini | eastus2 | Auto-target on `CHAT_FALLBACK=1` |
| tertiary | workers_ai | @cf/openai/gpt-oss-20b | cf-edge | Edge fallback |

--- removed ---
- openai (#347)
- anthropic (#347)
- bedrock (#347)
- grok / xai (#347)
- cerebras
- groq

## chat_fast_mode

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | workers_ai | @cf/meta-llama/Llama-3.2-3B-Instruct | cf-edge | Locked per #347 |
| secondary | azure_openai | gpt-4.1-mini | eastus2 | |

--- removed ---
- llama-3.2-1b-instruct (never primary; replaced by 3B-Instruct)
- openai (#347)
- bedrock (#347)

## chat_async

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | workers_ai | @cf/openai/gpt-oss-20b | cf-edge | Async batch only |
| secondary | azure_openai | gpt-4.1-mini | eastus2 | |

--- removed ---
- openai (#347)
- anthropic (#347)
- bedrock (#347)

## embed_hotpath

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | workers_ai | @cf/baai/bge-m3 | cf-edge | Pinned for hot path; ~20–40 ms |

--- removed ---
- vertex_embed / text-embedding-004 (rollback-only, removed from chain)
- cohere on hot path (Cohere-direct is async batch only)

## embed_batch

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | cohere | embed-multilingual-v3.0 | aws-us-west-2 | Direct API; async batch only |
| secondary | voyage_ai | voyage-3-large | aws-us-east-1 | |
| tertiary | workers_ai | @cf/baai/bge-m3 | cf-edge | |

--- removed ---
- bedrock_cohere (#347 — Bedrock removed; use Cohere direct)
- vertex_embed / text-embedding-004

## rerank

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | cohere | rerank-multilingual-v3.0 | aws-us-west-2 | |
| secondary | voyage_ai | rerank-2 | aws-us-east-1 | |
| tertiary | workers_ai | @cf/baai/bge-reranker-base | cf-edge | Graceful degrade: skip if all fail |

--- removed ---
- bedrock_cohere (#347)

## vector_db_live

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | pinecone |  | aws-us-west-2 | `syrabit-rag`, 1024-dim cosine |
| secondary | cf_vectorize |  | cf-edge | Edge cache |
| rollback_only | vertex_vector |  | us-central1 | Matching Engine; not in chain |

--- removed ---
- vertex_discovery (separate surface, not RAG fallback)

## vector_db_batch

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | pinecone |  | aws-us-west-2 | Same index as live |
| secondary | cf_vectorize |  | cf-edge | |

--- removed ---
- vertex_vector (rollback-only)

## moderation_default

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | workers_ai | @cf/meta/llama-guard-3-8b | cf-edge | Streaming-compatible (Rule 11) |
| secondary | azure_openai | azure-ai-content-safety | eastus2 | Concurrent with primary |

--- removed ---
- vertex (sampled-validation only, not moderation)
- openai-moderation (#347)

## moderation_exam_paper

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | workers_ai | @cf/meta/llama-guard-3-8b | cf-edge | Sync inside async batch |
| secondary | azure_openai | azure-ai-content-safety | eastus2 | |
| tertiary | vertex | gemini-2.5-flash (RAI) | us-central1 | fail-closed for exam_model_paper |

--- removed ---
- (none)

## validation_sampled

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | vertex | gemini-2.5-flash | us-central1 | 10% sample post-response, off critical path; rate via `VALIDATION_SAMPLE_RATE` |

--- removed ---
- per-turn synchronous validation (forbidden by Rule 12)

## validation_exam_paper

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | vertex | gemini-2.5-flash | us-central1 | Sync inside async batch only |

--- removed ---
- (none)

## translate_en_indic

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | workers_ai | @cf/ai4bharat/indictrans2-en-indic-1b | cf-edge | Indic primary |
| secondary | vertex | translate-v3 + gemini-2.5-flash polish | us-central1 | |
| tertiary | sarvam | sarvam-translate-v1 | aws-ap-south-1 | |
| rollback_only | azure_openai | azure-translator | eastus2 | Indic→English-only |

--- removed ---
- (none)

## translate_assamese

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | workers_ai | @cf/ai4bharat/indictrans2-en-indic-1b | cf-edge | as-IN target |
| secondary | sarvam | sarvam-translate-v1 | aws-ap-south-1 | Sarvam Indic-first |
| tertiary | vertex | translate-v3 | us-central1 | |

--- removed ---
- (none)

## translate_other

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | vertex | translate-v3 | us-central1 | Non-Indic pairs |
| secondary | azure_openai | azure-translator | eastus2 | |

--- removed ---
- (none)

## tts

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | elevenlabs | eleven_multilingual_v2 | aws-us-east-1 | |
| secondary | vertex | tts-neural2 | asia-south1 | en/as/hi/bn voices |
| tertiary | cartesia | sonic | aws-us-east-1 | |
| rollback_only | workers_ai | @cf/myshell-ai/melotts | cf-edge | |
| rollback_only | polly | neural | aws-us-west-2 | Last resort |

--- removed ---
- sarvam (Assamese chat / content / translate only — never tts)

## stt

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | deepgram | nova-3-general | aws-us-east-1 | |
| secondary | assemblyai | best | aws-us-east-1 | dual_channel + punctuate |
| tertiary | workers_ai | @cf/openai/whisper-large-v3-turbo | cf-edge | |
| rollback_only | vertex | speech-chirp | asia-south1 | |

--- removed ---
- sarvam (scope = Assamese chat / content / translate only)

## vision

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | vertex | gemini-2.5-flash (multimodal) | asia-south1 | |
| secondary | vertex_vision |  | asia-south1 | DOCUMENT_TEXT_DETECTION |
| tertiary | workers_ai | @cf/llava-hf/llava-1.5-7b-hf | cf-edge | |

--- removed ---
- sarvam
- openai-vision (#347)

## safety

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | workers_ai | @cf/meta/llama-guard-3-8b | cf-edge | |
| secondary | azure_openai | azure-ai-content-safety | eastus2 | |
| tertiary | vertex | gemini-2.5-flash (RAI) | us-central1 | opt-in for exam_model_paper only |

--- removed ---
- (none)

## email

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | sendgrid |  | (Azure Marketplace) | Pro 100k tier; 100,000 emails/month day-1 |
| secondary | sendgrid |  | (Azure Marketplace) | Essentials Free 100/day fallback tier |
| tertiary | aws_ses |  | aws-us-west-2 | Volume backstop via `EMAIL_FALLBACK` |

--- removed ---
- resend (#347)
- mailgun

## payments

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | razorpay |  | aws-ap-south-1 | INR-only; international cards out of scope |

--- removed ---
- stripe (#347 — international cards out of scope until future task)
- paypal

## ads

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | google_adsense |  | global | |
| secondary | google_admanager |  | global | |

--- removed ---
- (none)

---

## recall_summary_vector_query *(post-#362)*

> Long-term-summary embedding lookup, **gated by the two-tier
> recall-intent detector** in `infra/features-roadmap-362.md` §1.2.
> Per-turn unconditional cost is zero; only recall-intent turns pay
> the ~30–60 ms summary-vector lookup. Per Latency Rule 14,
> per-turn synchronous summary-vector queries on every chat turn
> are forbidden.

| tier | provider_slug | model_id | region | notes |
|---|---|---|---|---|
| primary | pinecone | (n/a, vector store; index `syrabit-summaries`, namespace = `user_id`) | aws-us-east-1 | top-k=3; metadata `{session_id, summary_version, summary_text, last_updated_iso, source_turn_count}`; 90-day idle eviction by background sweeper |
| primary | upstash_redis | (n/a, kv; key `summary:short:{session_id}`) | upstash-eu-west-1 | short-conversation fast path (< 8 turns total); 24h TTL; skips Pinecone round-trip when full history is already cheap to scan |
| secondary | pinecone | (same as primary, replica namespace) | aws-us-east-1 | rollback only — read-only mirror used if primary namespace returns 5xx; populated by the same off-critical-path summarizer |

**Embed model for summary vectors:** `@cf/baai/bge-m3` (canonical
`embed_hotpath`; same model used for the per-turn user-message
embedding so the vector spaces are compatible).

--- removed ---
- (none)

---

## Excluded providers (global)

- **cerebras** — absent from every chain
- **groq** — absent from every chain
- **bedrock** direct (Claude / Titan / Jamba / Nova) — removed (#347)
- **openai** direct — removed (#347)
- **anthropic** direct — removed (#347)
- **xai / grok** — removed from chat (#347)
- **stripe** — removed (#347)
- **resend** — removed (#347)
- **quge5** — removed (#347)
- **railway / digitalocean hosting** — removed (#347)
