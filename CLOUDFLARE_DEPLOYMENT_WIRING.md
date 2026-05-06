# Cloudflare Deployment Wiring — V4 Locked

> **Authoritative against:** [`infra/v4-locked-architecture.md`](infra/v4-locked-architecture.md).
> Cloudflare owns 40 % of the V4 cost split (V4 §0). This doc is the
> operator runbook for every Cloudflare-side asset Syrabit depends on.

---

## §1 — Cloudflare assets owned by Syrabit

| Asset | Name(s) | Purpose |
|---|---|---|
| Pages project | `syrabit-web` | SSR for `syrabit.ai` and `chat.syrabit.ai`. Built from `artifacts/syrabit/`. |
| Worker | `syrabit-edge-proxy` (`workers/edge-proxy/`) | Fronts `api.syrabit.ai`; injects `ORIGIN_SHARED_SECRET`; forwards `traceparent` / `baggage`; routes to Azure ACA `syrabit-backend` (`eastus2`). |
| Worker | `syrabit-embed-worker` (production) and `syrabit-embed-worker-staging` (`artifacts/syrabit/workers/embed-worker/`) | Hosts EmbeddingGemma-300M + Qwen3-0.6B mean-pooled to 1024-dim. Routes: `embed.syrabit.ai`, `embed-staging.syrabit.ai`. |
| AI binding | `[ai]` per env in `wrangler.toml` | Workers-AI access for embed worker. |
| R2 bucket | `syrabit-r2` | Chapter PDFs, audio, exports, **final backups** (S3 dumps sync into R2 nightly). |
| KV namespace | `syrabit-kv` | Chapter index, syllabus map, A/B flags, allowlists. |
| D1 database | `syrabit-d1` | SEO meta, audit logs, **syllabus map (read-before-Mongo for V4 vectorless RAG tier-1)**. |
| Vectorize | `syrabit-vectorize` | Edge RAG cache **only** — never primary store. Pinecone `aws-ap-south-1` is the primary vector store. |
| AI Gateway | `syrabit-aig` | BYOK paths to Gemini and Azure OpenAI; `cf-aig-cost` headers feed the credit-burn meter (V4 §10 Rule C). |
| Cache Reserve | enabled on `syrabit.ai` zone | Long-TTL assets. |
| WAF | enabled on `syrabit.ai` zone | Custom rules incl. "Skip SBFM for embed worker" on `embed.syrabit.ai` and `embed-staging.syrabit.ai`. |
| RateLimiter DO | per-route bindings | Robust rate limiting for public auth + content endpoints. |
| Analytics Engine | `syrabit-analytics` | Request metrics. |
| Workers Logpush | → Sentry | Worker logs ship to Sentry; `traceparent` is preserved on push (V4 §7). |
| DNS | zone `syrabit.ai` | Authoritative DNS lives on Cloudflare (SES + SendGrid SPF/DKIM/DMARC published here). |

---

## §2 — Request flow (V4 chat hot path)

```
Browser
  └─▶ chat.syrabit.ai (Pages SSR)
        └─▶ api.syrabit.ai (Worker: syrabit-edge-proxy)
              ├─ inject ORIGIN_SHARED_SECRET header
              ├─ inject sentry-trace + traceparent + baggage
              └─▶ Azure ACA syrabit-backend (eastus2)
                    │
                    ├─ Llama-Guard-2 pre-filter (on ACA compute)
                    ├─ Chat dispatch (V4 §4, user-locked 2026-05-06 via B3):
                    │   Azure OpenAI gpt-4.1-nano (SOLE primary)
                    │     ↓ on 5xx / exhaust
                    │   Workers-AI Mistral-7B (A9 #1)
                    │     ↓ on 5xx
                    │   Workers-AI Llama-3.2-3B (A9 #2)
                    │     ↓ on 5xx
                    │   generic Workers-AI gpt-oss-20b (terminal)
                    │   (No CF Worker token-length/risk router built;
                    │    Vertex co-primary + Qwen3-0.6B short-turn path
                    │    explicitly rejected by founder.)
                    │
                    ├─ embed call ──▶ embed.syrabit.ai (Worker: syrabit-embed-worker)
                    │                    ├─ EmbeddingGemma-300M + Qwen3-0.6B (mean-pool 1024-dim)
                    │                    └─▶ Pinecone aws-ap-south-1 namespace=cached_gemma_today
                    │
                    └─ on embed-worker outage:
                          RAG_EMBEDDING_PROVIDER=fallback_vertex
                          ├─ Vertex multilingual embedding
                          ├─▶ Pinecone namespace=fallback_vertex_pending_reembed
                          └─▶ AWS SQS syrabit-reembed-queue (Lambda re-embeds back when CF returns)
```

---

## §3 — Deploy procedures

### Edge proxy

```bash
cd workers/edge-proxy
pnpm install
wrangler deploy --env production
```

Required secrets (push via `wrangler secret put`):
- `ORIGIN_SHARED_SECRET` (must equal backend's expected value; sourced from AKV via Terraform-CI sync)

### Embed worker

```bash
cd artifacts/syrabit/workers/embed-worker
pnpm install
# staging first
pnpm run deploy:staging
./scripts/smoke.sh staging
# then production
pnpm run deploy:production
./scripts/smoke.sh production
```

Required secrets per env (separate values for `production` and `staging`):
- `EMBED_SHARED_SECRET` (must equal backend's `WORKERS_EMBED_SECRET`)

See `artifacts/syrabit/workers/embed-worker/README.md` for the full
operator runbook (Task #413).

### Pages (frontend)

Auto-deploys from the `main` branch via Cloudflare Pages GitHub
integration. Preview deploys are gated by branch protection;
production is the `main` branch only.

---

## §4 — Cross-cloud secret topology (V4 §6)

Cloudflare-side secrets (`wrangler secret put` outputs + worker
bindings) are **read-only replicas** of Azure Key Vault. The
Terraform-CI job in `.github/workflows/secrets-sync.yml`:

1. Pulls each secret from Azure KV (SoT).
2. Pushes to Cloudflare via `wrangler secret put` for each worker /
   each env.
3. Pushes the same value to AWS Secrets Manager `ap-south-1`.
4. Computes SHA-256 of each value across all three stores; **fails the
   pipeline on any pair mismatch**.

**Do not edit Cloudflare secrets directly via the dashboard.** Any
manual change will be overwritten on the next sync run, and the hash
check will fail in the interim.

---

## §5 — Observability

- **Workers Logpush** → Sentry project `syrabit-edge`. `traceparent`
  header is preserved on push.
- **Cloudflare AI Gateway** response headers (`cf-aig-cost`,
  `cf-aig-cache`, `cf-aig-model`) are scraped into the credit-burn
  meter.
- **Analytics Engine** powers the admin RAG telemetry dashboard.
- **Sentry Performance** is the end-to-end trace owner (V4 §7);
  every CF Worker emits `sentry-trace` + `traceparent` + `baggage`,
  and Azure ACA + AWS Lambda re-emit them on every downstream call.

---

## §6 — Domain wiring

| Hostname | Record | Pointer |
|---|---|---|
| `syrabit.ai` | CNAME | Cloudflare Pages `syrabit-web.pages.dev` |
| `chat.syrabit.ai` | CNAME | Cloudflare Pages `syrabit-web.pages.dev` (same project, different route) |
| `api.syrabit.ai` | Worker route | `syrabit-edge-proxy` |
| `embed.syrabit.ai` | AAAA `100::` (proxied) | Worker route → `syrabit-embed-worker` (production) |
| `embed-staging.syrabit.ai` | AAAA `100::` (proxied) | Worker route → `syrabit-embed-worker-staging` |
| MX / TXT (SPF/DKIM/DMARC) | TXT records | Published for both SendGrid and SES; same domain serves both senders. |

DNS is **only** managed by Cloudflare. No other registrar / DNS
provider has authoritative records for `syrabit.ai`.

---

## §7 — Removed Cloudflare assets (Task #347)

- `syrabit-bedrock-proxy` Worker — deleted (Bedrock removed from chain).
- Stripe webhook routes on edge proxy — deleted.
- Resend mailer Worker — deleted (replaced by SendGrid via Azure Marketplace).
