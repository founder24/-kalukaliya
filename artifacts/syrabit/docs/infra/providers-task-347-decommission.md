# Provider Decommissioning — Task #347 (2026-05-04)

> ⚠️ **V4 cross-reference (2026-05-06).** The locked source of truth for the
> overall Syrabit architecture is [`infra/v4-locked-architecture.md`](../../../../infra/v4-locked-architecture.md).
> If anything below disagrees with V4, V4 wins. This doc is preserved as the
> operator-facing rationale for each Task #347 vendor removal and its
> replacement — it is the V4-companion to V4 §0 (cost-share) and the
> "Removed providers" gotchas in `replit.md`.

This document records why each vendor was removed in Task #347 and what
its replacement is. It is the operator-facing companion to the deeper
`provider-credit-matrix.md` and `providers-architecture.md` docs.

---

## LLM providers — removed

| Vendor | Why removed | Replacement |
|---|---|---|
| **OpenAI (direct)** | Azure OpenAI exposes the same `gpt-4o-mini` deployment via the AI Gateway BYOK slug `azure-openai`, with $2 500 of Activate credit attached. Direct OpenAI was billable from the first token and added a second SDK + audit surface for no functional gain. | `azure_openai` (CF AI Gateway BYOK → Azure OpenAI Service) |
| **Anthropic (direct)** | Never made it to a production routing pool (`PROVIDER_PRIORITY` only ever held Claude in development sketches). The vendor was kept around as a stale `from anthropic import …` import; deleted. | None — `azure_openai` + `vertex` cover the equivalent capability tier. |
| **xAI Grok** | `_XAI_KEY` BYOK lifecycle was wired but never enabled in any pool weight. Removing the slug + env var deletes a confusing dead branch. | None — Workers AI `mistral-7b` covers the same English balanced-fallback band at $0/token. |
| **AWS Bedrock** | AWS account-wide daily token quota was exhausted across every on-demand text model in every region (verified via direct boto3 SigV4 + CF AI Gateway BYOK probing). Task #337 / `bedrock-reenable.md` are reversed by this task. | `azure_openai` + `vertex` (chat); `deepgram`/`elevenlabs` (voice); `cohere`/`voyage_ai` (embeddings). |

## Workers AI — promoted

To keep paid-provider exhaustion from cascading into hard 5xx, three
named Workers AI variants were added to `PROVIDER_PRIORITY` and given
small non-zero weights in the relevant pools:

| Pool key | New entry | CF model | Role |
|---|---|---|---|
| `english_rag_chat` | `workers_ai_llama32_3b` | `@cf/meta/llama-3.2-3b-instruct` | Fast-mode 3B; lowest TTFT for burst traffic. |
| `english_rag_chat` + `content` | `workers_ai_mistral_7b` | `@cf/mistral/mistral-7b-instruct-v0.1` | Balanced 7B fallback when the paid tiers throttle. |
| `assamese_rag_chat` | `workers_ai_llama31_8b` | `@cf/meta/llama-3.1-8b-instruct-fp8` | Indic-friendly chat fallback paired with the existing IndicTrans2 (`workers_ai_indic`) weight-zero last resort. |

The IndicTrans2 promotion (`@cf/ai4bharat/indictrans2`) already lived
in the `translate` pool as `workers_ai_indic`; this task only confirmed
it remains at weight 10 000 in that pool.

## Email — Resend → SendGrid

The transactional email chain is now:

1. **Cloudflare Email Worker** (`syrabit-email`) — POSTs to SendGrid v3
   from the edge. Zero per-message cost under our CF $5k credit.
2. **SendGrid v3 HTTP API** in-process from `email_templates.py` —
   used when `EMAIL_WORKER_URL` is unset or the Worker returns a 5xx.
3. **Amazon SES** via the `email-fallback` SQS queue — final-tier
   retry handled by the `syrabit-email-worker` Lambda. Kept as 5xx-only
   fallback per SES account-status agreement.

Resend was removed because:

* Its India deliverability dropped sharply through April 2026 (43 % open
  vs SendGrid's 71 % on the same templates).
* SendGrid is already provisioned for the AWS SES → SendGrid signing
  domain (`em.syrabit.ai`) — no new DKIM rotation needed.

`email_templates.py` keeps the SES tier behind the same SQS topic so
the email-worker Lambda's DLQ + alarm coverage is unchanged.

## Payments — Stripe removed

Razorpay (INR) is now the sole supported gateway. Stripe accounted for
< 4 % of completed payments over the prior 90 days, all of which were
USD test charges that did not resolve to active subscriptions. The
`/payments/stripe/create-checkout` and `/webhooks/stripe` routes now
return HTTP 410 Gone with a deprecation message; the prior handlers are
deleted, and the Stripe SDK import has been removed.

## Ad networks — Quge5 removed

Quge5 was disabled site-wide on 2026-04-19 (popunders + adult
creatives). Task #347 finishes the cleanup: the `useQuge5Multitag`
hook is deleted, the Admin → Ads management UI no longer offers it as
a network choice, and `KNOWN_NETWORKS` on the backend rejects it. The
literal `'quge5'` is intentionally retained inside the
`DISABLED_NETWORKS` allowlist so any cached admin-config row that
still references it is stripped at boot rather than silently allowed.

## bedrock-proxy Worker — deleted

`workers/bedrock-proxy/` is removed. It SigV4-signed Polly /
Transcribe / Translate requests for the (now-decommissioned) Bedrock
provider. CF Worker `syrabit-bedrock-proxy` should be deleted from the
Cloudflare dashboard as a manual cloud-side step (see deviations in
the Task #347 commit message).

## Lint guardian

`artifacts/syrabit-backend/scripts/check_dead_providers.py` was
extended with a second regex (`BANNED_VENDOR_USES`) that fails CI on
new `import stripe` / `import resend` / `workers/bedrock-proxy`
references. The existing `BANNED_LITERAL` regex now also includes
`quge5`. Files that legitimately reference these vendors (admin alert
routes still using Resend, historical runbooks) are explicitly
allowlisted with a `# Task #347` justification.
