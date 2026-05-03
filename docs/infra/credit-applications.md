# Pre-Seed Startup Credit Applications Tracker

**Last updated:** 2026-05-03
**Status:** Active — see machine-readable sidecar `credit-applications.json`
**Owner:** founder@syrabit.ai
**Task:** #323

This doc is the **single source of truth** for every pre-seed-eligible startup-credit
programme Syrabit is claiming. The tracker table below is rendered in the admin
Routing panel as a per-provider "Application status" badge — the badge reads
`credit-applications.json` (same directory) via the `/admin/credit-applications`
endpoint, so updating that JSON updates the UI.

> **Do not** edit `artifacts/syrabit-backend/config.py` `PROVIDER_CREDITS` or
> `POOL_WEIGHTS` based on aspirational credit grants. Only update those
> constants once the relevant grant is **approved in writing** — see the
> [When a grant is approved](#when-a-grant-is-approved) checklist.

---

## Realistic outcome estimate

| Bucket | Amount |
|---|---:|
| Already approved (no action needed) | **$5,500** (MongoDB $500 + Cloudflare $5,000) |
| Guaranteed instant signups | ~$263 (Deepgram $200 + AssemblyAI $50 + Sarvam ₹1,100 ≈ $13) |
| Likely approved (easy pre-seed forms, ~70% landing rate) | ~$10–12K (OpenRouter $5K + ElevenLabs ~$4K + AssemblyAI ~$1.5K + Deepgram ~$1K + Exa equiv) |
| **Total claimable runway after this task** | **~$16–18K cash credit** + free Pinecone Standard Tier + Exa Enterprise Pro 6 mo |
| Combined with existing hyperscaler pool ($2K Vertex + $2.5K Azure + $1K AWS) | **~$22–24K total runway** vs. previous stale pool of ~$11K |

---

## Company facts packet (copy-paste into every form)

> Drafted once so each application form takes < 5 min to fill. Lift verbatim
> into the "Company description / use case / team" fields.

```text
Legal name:        Syrabit (sole proprietorship under founder, India-registered)
Founder:           [Founder name], based in Guwahati, Assam, India
Udyam status:      Registered MSME (Udyam-AS-XX-XXXXXXX) — confirms India-incorporated startup
Live product URL:  https://syrabit.ai
GitHub org:        https://github.com/syrabit
Founded:           2025
Funding stage:     Pre-seed, unfunded (no institutional capital raised)
Team size:         1 (founder, full-stack)
Target market:     AHSEC / SEBA / NCERT students in Assam, India (Class 9–12, ~3M addressable)
Stack:             FastAPI (Python) + React + Cloudflare Workers + MongoDB Atlas +
                   GCP Vertex AI + Azure OpenAI + Sarvam (Indic LLM) + Deepgram +
                   ElevenLabs + Pinecone + Exa
Use case for <PROVIDER>:
  <provider-specific blurb — see per-provider section below>
Expected monthly volume:
  - LLM / chat:      ~5M tokens/mo, growing 30% MoM
  - STT / TTS:       ~50 hours/mo audio
  - Embeddings:      ~2M tokens/mo (RAG ingest + queries)
  - Vector search:   ~100K queries/mo
  - Web search:      ~30K queries/mo
Why pre-seed entry tier (not the headline ceiling):
  Syrabit is an unfunded pre-seed solo founder serving an emerging-market
  EdTech niche. We do NOT qualify for "$100K Deepgram" / "$150K AssemblyAI"
  ceilings that require $250K+ raised or scaled paid usage. We are applying
  for the explicit pre-seed/unfunded entry tier of your programme.
```

### Per-provider use-case blurbs

| Provider | Blurb (drop into "use case" field) |
|---|---|
| **OpenRouter** | "Multi-LLM gateway for AHSEC/SEBA Class 11–12 chat tutoring. We route across Claude, GPT-4o-mini, Llama-3.3, and Gemini Flash via your unified API instead of integrating each provider directly. ~5M tokens/mo today, growing 30% MoM." |
| **ElevenLabs** | "Audio-first study notes for visually-impaired and low-literacy students in Assam. We synthesise Class 11–12 syllabus chapters into Assamese / Hindi / English audio (~50 hours/mo). Multilingual_v2 is the only voice model with usable Assamese prosody." |
| **Deepgram** | "Real-time voice tutor for spoken Q&A. Students ask in code-mixed Assamese-English, we transcribe with nova-3 and reply via TTS. ~50 hours/mo, latency-critical (< 500 ms streaming p95)." |
| **AssemblyAI** | "Async transcription of long-form lecture videos for our content team — ~30 hours/mo of teacher-recorded content needs accurate timestamps + speaker diarisation for chunking into MCQs." |
| **Exa** | "Web-grounded RAG for current-affairs and SEBA-syllabus updates that change yearly. Exa's neural search outperforms keyword search for our query mix; ~30K queries/mo." |
| **Pinecone** | "Curated chapter-level vector index (`syrabit-ahsec`, ~50K vectors, Cohere 1024-dim) for syllabus-aligned RAG. Need Standard Tier for production SLA + Pro Support for index migrations." |
| **Sarvam** | "Native Indic LLM for Assamese conversational reasoning — Sarvam-M outperforms Gemini and GPT-4o on Assamese script tasks. Primary `assamese_rag_chat` provider." |

---

## Tracker table

> **Authoritative data lives in [`credit-applications.json`](./credit-applications.json).**
> This table is regenerated from the JSON whenever the doc is touched.
> Status legend matches the JSON: `approved`, `submitted`, `in_progress`,
> `ready`, `not_started`, `rejected`, `expired`, `disabled`.

| Provider | Programme | URL | Tied email | Tier USD | Status | Approved $ | Expires | Notes |
|---|---|---|---|---:|---|---:|---|---|
| `mongodb_atlas` | MongoDB for Startups | <https://www.mongodb.com/startups> | founder@syrabit.ai | 500 | ✅ approved | 500 | 2026-12-01 | Atlas free-tier cluster + $500 promo credit applied. |
| `cloudflare` | Cloudflare for Startups | <https://www.cloudflare.com/forstartups/> | founder@syrabit.ai | 5,000 | ✅ approved | 5,000 | 2026-09-01 | Enterprise zone — WAF, Turnstile, mTLS, Zero Trust, Pages, R2, D1, Vectorize. |
| `deepgram` | Deepgram (instant signup) | <https://console.deepgram.com/signup> | founder@syrabit.ai | 200 | 🟡 ready | — | — | No review — instant. Capture `DEEPGRAM_API_KEY`. |
| `deepgram_startup` | Deepgram Startup Program | <https://deepgram.com/startup-program> | founder@syrabit.ai | 1,000 | ⚪ not started | — | — | Stacks on the $200 instant signup. |
| `assemblyai` | AssemblyAI (instant signup) | <https://www.assemblyai.com/dashboard/signup> | founder@syrabit.ai | 50 | 🟡 ready | — | — | No review — instant. Capture `ASSEMBLYAI_API_KEY`. |
| `assemblyai_startup` | AssemblyAI Startup | <https://www.assemblyai.com/startups> | founder@syrabit.ai | 1,500 | ⚪ not started | — | — | Stacks on the $50 instant signup. |
| `elevenlabs` | ElevenLabs Grants | <https://elevenlabs.io/grants> | founder@syrabit.ai | 4,000 | ⚪ not started | — | — | Highest-yield easy form, ~70% landing rate with packet. |
| `sarvam` | Sarvam (instant signup) | <https://dashboard.sarvam.ai/signup> | founder@syrabit.ai | 13 | 🟡 ready | — | — | ₹1,100 ≈ $13. Capture `SARVAM_API_KEY`. |
| `sarvam_startup` | Sarvam Startup Programme (India) | <https://www.sarvam.ai/startup-programme> | founder@syrabit.ai | 0 | ⚪ not started | — | — | Stacks on ₹1,100 signup; tier amount varies. |
| `openrouter` | OpenRouter (free starter) | <https://openrouter.ai/sign-up> | founder@syrabit.ai | 0 | 🟡 ready | — | — | No card required. Capture `OPENROUTER_API_KEY`. |
| `openrouter_startup` | OpenRouter Startup Program | <https://openrouter.ai/startups> | founder@syrabit.ai | 5,000 | ⚪ not started | — | — | Largest single ask; explicit pre-seed framing. |
| `exa_ai` | Exa Startup | <https://exa.ai/startups> | founder@syrabit.ai | 0 | ⚪ not started | — | — | Grants 6 months Enterprise Pro (not USD). |
| `pinecone_ai` | Pinecone for Startups | <https://www.pinecone.io/startups/> | founder@syrabit.ai | 0 | ⚪ not started | — | — | Grants free Standard Tier + Pro Support (not USD). |
| `bedrock` | AWS Activate (Bedrock) | <https://aws.amazon.com/activate/> | founder@syrabit.ai | 1,000 | 🚫 disabled | 1,000 | — | Disabled — AWS Bedrock daily token quota exhausted. |

---

## When a grant is approved

Use this checklist every time an approval email lands. **Each step is gated;
do not auto-apply config changes from aspirational tiers.**

1. **Capture the credit details** — amount, expiry, billing-account ID,
   reference number. Update the matching row in `credit-applications.json`:
   `status: "approved"`, `approved_usd: <real-number>`, `expires_on: <date>`,
   `reference_id: <id-from-email>`.
2. **Capture the API key** via Replit Secrets (use the environment-secrets
   workflow — never commit). The provider already has an env-var alias in
   `routes/admin_vertex.py:PROVIDER_META`; if not, add one in the same map.
3. **Reconcile `PROVIDER_CREDITS`** in `artifacts/syrabit-backend/config.py`:
   bump the integer to match the real grant. This changes draw weights — do it
   in a focused commit with a single-provider scope.
4. **Reconsider `POOL_WEIGHTS`**: if the new credit is large enough to
   promote the provider above the existing primary on a pool, open a separate
   review (do not bundle with the credit-amount bump).
5. **Update the admin badge** by re-running the doc → JSON sync (just edit
   the JSON; the panel reads it on next refresh).
6. **Set a calendar reminder** 30 days before `expires_on` so we don't lose
   unused credit at the cliff.

---

## Out of scope (do not pursue under this task)

- Programmes requiring funding (DeepInfra DeepStart $250K min, Mistralship
  selective cohort, Groq Partner hand-picked, Together AI Accelerator).
- Discount-only programmes (Cohere 25% off, Qdrant 20% off).
- Free-tier-only providers already in use (Voyage AI, Tavily, HF Pro,
  Replicate trial).
- "Up to $100K" / "up to $150K" aspirational ceilings — claim only the
  pre-seed entry tier, let usage organically promote us.
- Editing `PROVIDER_CREDITS` / `POOL_WEIGHTS` based on aspirational tiers —
  see the gated checklist above.
- Dispatcher-side changes (credit-burn-aware scaling, daily $$ caps,
  free-tier counters) — tracked under separate dispatcher tasks.
- Migrating workloads to new providers — this task claims credit only.
