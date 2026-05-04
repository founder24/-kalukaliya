# AWS-Native Advanced Features

**Status:** Live (Task #337)
**Owner:** infra + AI
**Companion docs:**
[`../infra/ADR-0001-four-way-hosting-rebalance.md`](../infra/ADR-0001-four-way-hosting-rebalance.md),
[`../infra/aws-landing-zone.md`](../infra/aws-landing-zone.md),
[`../infra/provider-credit-matrix.md`](../infra/provider-credit-matrix.md)
**Terraform:** [`../../infra/aws/aws-native-features.tf`](../../infra/aws/aws-native-features.tf)
**Admin panel:** [`../../src/components/admin/AdminAwsNativePanel.jsx`](../../src/components/admin/AdminAwsNativePanel.jsx)

---

## 1. What this is

A set of AWS-managed AI / utility services that ride on top of the
existing AWS landing zone (workers + queues + SES) and add **net-new
feature surfaces** to Syrabit. Every entry below is wired as an
**additional** path in the relevant failover chain — none replace an
existing GCP / Sarvam / Cohere / ElevenLabs provider.

Hosting-plan alignment is critical: per
[`cloud-allocation-plan.md`](../infra/cloud-allocation-plan.md) §6 + §9,
**Amazon Bedrock is Cohere-only** in the four-cloud architecture
(`embed-multilingual-v3` + `rerank-v3.5`). Anthropic Claude, Meta
Llama, Mistral, Amazon Titan, and Amazon Nova on Bedrock are
**explicitly out of scope** — Azure OpenAI (GPT-4.1-mini) and Vertex
Gemini 2.5 Flash cover those LLM roles per the credit matrix. This
keeps the $1k AWS Activate balance funding the embed/rerank workload
it is best at and avoids the Bedrock Marketplace BYOK billing failures
we hit in the original draft of this task.

## 2. At-a-glance

| Feature                   | Where it slots in            | Failover position           | Admin toggle                         |
| ------------------------- | ---------------------------- | --------------------------- | ------------------------------------ |
| **Bedrock — Cohere only** | embed + rerank pools          | Primary path                | `aws.bedrock_cohere.enabled`         |
| **Polly (Neural / Gen)**  | TTS chain                     | Tier 3 (after ElevenLabs, Google TTS) | `aws.polly.enabled`        |
| **Transcribe**            | STT chain                     | Tier 3 (after Deepgram, Google STT)   | `aws.transcribe.enabled`   |
| **Textract**              | OCR pipeline (structured)     | Branch (per upload-type flag) | `aws.textract.enabled`             |
| **Rekognition**           | User image upload pre-R2      | Required guard               | `aws.rekognition.enabled`           |
| **Comprehend**            | Sampled chat + reviews job    | Background only (analytics)  | `aws.comprehend.enabled`            |
| **Translate**             | Sarvam translate path         | Fallback on Sarvam 429 / 5xx | `aws.translate.enabled`             |
| **Personalize**           | Home + "Continue Learning"    | Feature flag with deterministic fallback | `aws.personalize.enabled` |
| **Fraud Detector**        | Signup + payment intent       | Risk score → admin review    | `aws.fraud_detector.enabled`        |

Every feature has a dedicated least-privilege IAM role
(`syrabit-aws-native-<feature>-prod`) declared in
[`aws-native-features.tf`](../../infra/aws/aws-native-features.tf).
There are **no static AWS access keys** anywhere in the stack —
callers exchange short-lived OIDC tokens via STS.

## 3. Feature runbooks

### 3.1 Bedrock — Cohere only

* **Models:** `cohere.embed-multilingual-v3`,
  `cohere.rerank-v3-5:0` (region: `us-east-1`).
* **Caller:** `services/backend` AI dispatcher
  (`get_weighted_chain('embed' | 'rerank')`). Bedrock-Cohere is the
  primary path; Cohere direct API stays as the fallback for
  cross-region resilience.
* **Failure mode:** STS token rejection → dispatcher falls through to
  Cohere direct → Voyage AI → Pinecone-AI rerank.
* **Admin surfacing:** Bedrock latency / throttle / spend tile on
  `AdminHealth` and the AWS-native panel; cost is tagged
  `feature=bedrock_cohere` on Cost Explorer.
* **Out of scope:** No chat-LLM Bedrock routes (Claude / Llama /
  Mistral / Titan / Nova). The Lambda IAM in
  `lambda-bedrock-proxy.tf` retains the legacy Claude+Titan ARNs only
  for the in-flight decommission window — do not add new code that
  targets them.

### 3.2 Polly — third-tier TTS

* **Voices:** Neural English (`Joanna`, `Matthew`); Generative for
  long-form Read-Aloud where available; Hindi `Kajal` and Tamil
  `Aditi` for Indic coverage. (Polly does not yet ship Assamese — the
  Sarvam path stays primary for `as`.)
* **Caller:** `tts_router.synthesize(text, lang)` — third tier after
  `elevenlabs` and `google_tts`.
* **Failure mode:** Throttle (`aws_polly_throttled`) or 5xx → router
  bubbles up to the deterministic fallback voice already used when
  every provider is down.
* **Cost guard:** `polly:SynthesizeSpeech` request count alarm at
  `> 200k chars / day` (CloudWatch).

### 3.3 Transcribe — third-tier STT

* **Caller:** `stt_router.transcribe_stream(audio, lang)` — third
  tier after `deepgram` and `google_chirp`. Uses
  `StartStreamTranscription` for mic input; batch jobs stage audio in
  the workers' S3 ingest bucket.
* **Failure mode:** Deepgram + Google both timing out → Transcribe
  picks up. If Transcribe also fails, the UI surfaces the existing
  "voice unavailable" toast.

### 3.4 Textract — structured-document OCR

* **Use cases:** past-paper tables, marks-sheet uploads, handwritten
  exam answers, application forms.
* **Caller:** `ocr_pipeline.process(upload, mode='structured')`. The
  `mode` flag is set per upload-type in admin: `past_paper`,
  `marks_sheet`, `handwritten_answer` route to Textract; everything
  else stays on the existing Vision OCR path.
* **Output:** Structured JSON (tables + key/value pairs + raw blocks)
  written next to the original upload in the OCR result bucket.
* **Failure mode:** Textract 429 / 5xx → pipeline falls back to
  generic Vision OCR with a `degraded=true` flag stored on the
  result row; admin moderation surfaces these for review.

### 3.5 Rekognition — image moderation

* **Trigger:** every `POST /uploads/image` *before* the object is
  committed to R2. Response-time budget: 800 ms (cached 24 h on the
  upload's content hash).
* **Action:**
  * `confidence < 70` on every label → pass through to R2.
  * `confidence ≥ 70` on `Explicit Nudity`, `Violence`, `Hate
    Symbols`, `Drugs & Tobacco Paraphernalia` → quarantine in the
    `rekognition-quarantine/` prefix and surface on the admin
    moderation queue (`/admin/moderation`).
* **Failure mode:** Rekognition outage → uploads are **rejected**
  (closed-by-default — we do not ship un-moderated user images to R2).
  The admin panel exposes a break-glass toggle to flip this to
  open-by-default with a 30-minute expiry.

### 3.6 Comprehend — sampled NLP analytics

* **Trigger:** ACA cron job (`comprehend_sampler`, hourly) reads up
  to 5 000 chat messages and 500 reviews from the analytics warehouse
  and runs `BatchDetectPiiEntities` + `BatchDetectSentiment`.
* **Output:** Aggregated PII frequency + sentiment-by-cohort tiles in
  the analytics warehouse — **never** surfaced in the user UX, never
  used to auto-block content. Admin moderation queue is still the
  loop for action.
* **Sampling rate** is admin-configurable; default 1 % for chat,
  100 % for reviews.

### 3.7 Translate — Sarvam fallback

* **Caller:** `translate_router.translate(text, src, tgt)` — second
  tier after Sarvam for Indic ↔ English. NLLB-200 stays as the third
  tier for the long tail of language pairs.
* **Failure mode:** Sarvam returns 429 / 5xx → router calls Translate
  with the same `(src, tgt)` pair. Translate is **not** the primary
  for Assamese: Sarvam's Assamese model is still measurably better on
  our internal eval set, so we only flip to Translate when Sarvam is
  unhealthy.

### 3.8 Personalize — home rail recommendations

* **Datasets ingested:** chapter views, quiz attempts, flashcard
  reviews — last 90 days, exported nightly to the
  `syrabit-personalize-import` S3 bucket by the existing analytics
  pipeline.
* **Solutions:** `aws-user-personalization` recipe; one campaign for
  chapters (`syrabit-chapters-campaign`), one for quizzes
  (`syrabit-quizzes-campaign`).
* **Surface:** Home page "Recommended for you" rail and the
  "Continue Learning" suggestions fall back to the deterministic
  ranker (popularity × recency × subject affinity) when the feature
  flag is off, when the user has no history, or when the campaign
  returns < 3 items.
* **Cold-start:** Anonymous users always see the deterministic rail;
  Personalize only kicks in once ≥ 5 events are recorded for a user.
* **Failure mode:** Campaign 5xx → render deterministic rail with the
  same UI; PostHog event `recs_fallback` fires for monitoring.

### 3.9 Fraud Detector — risk score on signup + payment

* **Triggers:**
  * `POST /auth/signup` — `event_type=signup`, features =
    `email_domain`, `ip_country`, `device_fingerprint`,
    `signup_velocity_5m`.
  * `POST /payments/intent` — `event_type=payment_intent`, features =
    `amount_inr`, `plan`, `ip_country`, `card_bin_country`.
* **Outputs:** numeric risk score 0–1000 + outcome label (`approve`,
  `review`, `block`).
* **Wiring:**
  * `approve` → request continues unchanged.
  * `review` → request proceeds, but is queued in the admin
    moderation panel for retroactive review and the user is
    soft-rate-limited on the next request.
  * `block` → request is rejected with a generic 4xx; ops is paged
    only on aggregate burst (alert if `blocked_count > 20 / 5min`).
* **Failure mode:** Fraud Detector outage → score defaulted to 0
  (allow), event tagged `fd_unavailable=true` for the analytics
  pipeline so we can backfill in review.

## 4. IAM + secrets

* Roles: `syrabit-aws-native-<feature>-prod`, declared in
  [`aws-native-features.tf`](../../infra/aws/aws-native-features.tf).
* Trust: assumed by Lambda workers (service principal) and by the DO
  backend via the GitHub-OIDC federated `repo:syrabit/syrabit:*`
  subject (short-lived STS exchange — Doppler ships the OIDC token,
  not a static key).
* No static AWS access keys are issued for these features.
* Per-feature config (Personalize campaign ARN, Fraud Detector
  detector name, Bedrock guardrail ID) lives in Secrets Manager
  under `syrabit/prod/aws-native/<feature>` and is rotated via the
  same 1Password → AWS console flow as the rest of the worker
  secrets.

## 5. Cost & observability

* CloudWatch dashboard `syrabit-aws-native-prod` covers per-feature
  invocations, latency, and feature-specific signals (Rekognition
  flagged ratio, Personalize CTR vs deterministic).
* Cost Explorer is filtered by the `feature=<service>` tag carried
  on every IAM role; the admin billing panel renders a per-feature
  rollup from the daily Cost Explorer export.
* Cost guards (Activate budget headroom):
  * Bedrock-Cohere: alarm at $20 / day.
  * Polly: alarm at $5 / day.
  * Transcribe: alarm at $5 / day.
  * Textract: alarm at $5 / day.
  * Rekognition: alarm at $5 / day.
  * Comprehend: alarm at $2 / day (sampled, expect single-digit).
  * Translate: alarm at $5 / day.
  * Personalize: alarm at $10 / day.
  * Fraud Detector: alarm at $2 / day.

## 6. Admin surfacing

`AdminAwsNativePanel.jsx` renders one tile per feature with:

* Enabled / disabled toggle (writes to `/admin/aws-native/toggle`).
* Live throttle + p95 latency from the CloudWatch dashboard JSON.
* 7-day spend pulled from Cost Explorer (cached 1 h).
* Direct link to the per-feature CloudWatch widget for deep dives.

The panel is registered as the `awsnative` admin section. Failure
modes for each feature are also linked from the corresponding tile
back to this runbook.

## 7. Out of scope

* Hosting / queue / cron infra (covered by `aws-landing-zone.md` +
  `workers-on-aws.md`).
* Any GCP / Cloudflare / Digital Ocean changes.
* Replacing any existing provider — every AWS feature here is an
  *additional* path.
* Routing Anthropic Claude / Llama / Mistral / Titan / Nova through
  Bedrock — explicitly excluded by §6 + §9 of
  `cloud-allocation-plan.md`. Bedrock is Cohere-only.
* Automated content-blocking decisions based on Comprehend or
  Rekognition alone — admin review remains in the loop.
