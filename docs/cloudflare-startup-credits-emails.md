# Cloudflare for Startups — Email Templates

These are the templates for the two scheduled emails to the Cloudflare for
Startups team. Keep them under source control so future operators can copy-
paste-send without reconstructing context.

Reply-to / from address should be `infra@syrabit.ai` (or whichever address is
on the startup-program enrollment record).

---

## 1. Unlock locked-but-free features (send once, now)

**To**: `startups@cloudflare.com` (the address the startup rep last replied
from — verify before sending)

**Subject**: Syrabit.ai — request to unlock locked startup-program features
on account `d66e40eac539fff1db270fddf384a5ec`

**Body**:

> Hi team,
>
> Syrabit.ai (Cloudflare for Startups participant, account ID
> `d66e40eac539fff1db270fddf384a5ec`, primary zone `syrabit.ai`) would like
> to activate the following features that the dashboard currently shows as
> "available on Enterprise" / "contact your account team", per the startup
> program documentation that says these are 100% included for participants:
>
> 1. **SSL for SaaS** — we plan to offer custom student / institution
>    domains pointed at `syrabit.ai`; SSL for SaaS lets us issue certs for
>    those without provisioning a separate ACM stack.
> 2. **Advanced Certificate Manager** — we want wildcard certs for
>    `*.assets.syrabit.ai` once the asset CDN fan-out goes live.
> 3. **Advanced Rate Limiting** — currently using a Durable Object limiter;
>    we'd like to evaluate moving it onto the dashboard rules so we can
>    retire the DO line item.
> 4. **(Anything else surfaced as "Enterprise-only" in the dashboard that
>    is in fact included for startup participants — please flag.)**
>
> If any of the above are NOT in scope of the program, please let us know
> the per-feature cost so we can decide whether to enable on the card on
> file vs leave off.
>
> Separately, we'd like to confirm that one of our **3 free Enterprise
> domain upgrades** has been applied to apex `syrabit.ai`. We expect Bot
> Management, advanced WAF, prioritized DDoS, 100% SLA, Argo Smart Routing,
> and Enterprise support to all be live on that zone.
>
> Thanks,
> Syrabit infra

**After sending**: log the date in `docs/cloudflare-cost-map.md` under
"Last reviewed", and add a follow-up reminder at +14 days to chase if no
reply.

---

## 2. Month-9 top-up request (drafted now, send ~2027-02-03)

**To**: the startup rep's direct address (note it on the enrollment record;
do not BCC `startups@cloudflare.com` for this — it's a relationship email).

**Subject**: Syrabit.ai — credit top-up request, ~9 months into the program

**Body** (fill `{...}` placeholders from the latest monthly review doc):

> Hi {rep first name},
>
> Quick update on Syrabit.ai's Cloudflare for Startups credit usage,
> ~9 months into the 12-month window.
>
> **Headline numbers (as of {YYYY-MM-DD})**
> - Total credits granted: $5,000
> - Credits consumed: ${X,XXX} ({P}%)
> - Credits remaining: ${Y,YYY}
> - Of credits consumed, **${Z,ZZZ} ({Q}%) is Workers AI**, separated
>   on the invoice via the AI Gateway tag `workers-ai-fallback:*` and
>   `workers-ai-edge-vector-search` (see attached invoice export).
> - Non-Workers-AI burn (R2 + Vectorize + D1 + KV + DO + AE combined)
>   averaged ${A}/month, all under the per-line-item caps in our
>   internal cost map.
>
> **What's driving the Workers AI spend**
> - We use Workers AI as the auto-fallback for chat (Llama 3.3 70B),
>   embeddings (BGE-large), STT (Whisper-large-v3-turbo), and TTS
>   (MeloTTS) when our primary providers (Vertex Gemini / Sarvam) are
>   degraded. {N}% of fallback events succeed end-to-end on Workers AI
>   without student-visible errors.
> - Edge-side vector search at `/api/edge/vector-search` runs the BGE
>   embedder + Vectorize query inline, eliminating a backend round-trip.
> - {Add 1–2 sentences on the growth story, e.g. "MAU went from {X} to
>   {Y} this period; Workers AI calls per MAU went from {A} to {B}."}
>
> **What's next**
> - We're scoping a migration of {one specific workload, e.g. "the
>   syllabus-aware Q&A pipeline"} onto Workers AI as primary, which
>   will materially raise per-day inference volume.
> - At current burn we'd exhaust the pool around {YYYY-MM-DD}.
>
> **The ask**
> Could we get an additional **${requested top-up, e.g. $5,000 or
> $10,000}** in startup credits, scoped specifically to Workers AI, to
> cover the migration above without throttling student traffic onto the
> paid tier?
>
> Happy to share a deeper usage breakdown, or jump on a 15-min call if
> easier.
>
> Thanks,
> {your name}, Syrabit

**Attachments**: the most recent invoice PDF (Cloudflare → Billing →
Invoices) and a CSV export of the AI Gateway request log filtered by the
`workers-ai-*` tags.

**Where the numbers come from**: `docs/cloudflare-monthly-cost-review.md`
captures all of these per-month — the month-9 review row is what fills in
the placeholders above.
