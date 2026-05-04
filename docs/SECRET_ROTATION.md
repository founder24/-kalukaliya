# Secret Rotation Runbook

> **Audience:** anyone rotating a shared secret in this project.
> **Goal:** rotate any secret end-to-end without rediscovering where it
> needs to land.
>
> Rotating the wrong secret in the wrong order takes down
> `api.syrabit.ai` for everyone. Read the relevant section *before*
> running anything.

This project has secrets that live in **up to five places** at once:

| Location | What lives there | How to set |
|---|---|---|
| **Replit Secrets** | Everything the local dev backend needs | Workspace → Secrets pane (or Replit secrets MCP) |
| **Cloudflare Worker** (`syrabit-edge`) | Worker-side shared secrets | `wrangler secret put NAME` from `workers/edge-proxy/` |
| **Cloudflare Pages** (`syrabit-zip-convert`) | Build-time `VITE_*` only | `pnpm run pages:apply-config` (see `apply-pages-config.mjs`) |
| **Backend deploy env** (Digital Ocean App Platform — Task #336) | Runtime backend secrets | `bash scripts/digitalocean.sh var-set syrabit-backend KEY=VAL` (under the hood: `doctl apps update --spec`). Bulk import from a key=value file: `bash scripts/digitalocean.sh import-env syrabit-backend do-backend-vars.env`. |
| **`.dev.vars`** (worker, gitignored) | Local `wrangler dev` only | Plain text file in `workers/edge-proxy/.dev.vars` |

A secret that lives in **more than one place** is the dangerous kind —
miss one and prod silently breaks. Those are listed in §1. Single-place
secrets are in §2.

---

## 1. Multi-place shared secrets

These MUST be rotated in every listed place, in the listed order, in
the same change window. The **direction of break** column tells you
what fails first if the values drift.

### 1.1 `D1_SYNC_SECRET`

The shared bearer token between the worker and the backend's
`/api/admin/d1-export` endpoint. Used by the worker's nightly D1 sync
job to pull content from the backend into Cloudflare D1.

| Place | Variable | How |
|---|---|---|
| Worker | `D1_SYNC_SECRET` | `cd workers/edge-proxy && wrangler secret put D1_SYNC_SECRET` |
| Backend (Digital Ocean) | `D1_SYNC_SECRET` | `bash scripts/digitalocean.sh var-set syrabit-backend D1_SYNC_SECRET="<value>"` (Task #336) |
| Replit Secrets | `D1_SYNC_SECRET` | Secrets pane (so local dev can still call the export route) |
| `workers/edge-proxy/.dev.vars` | `D1_SYNC_SECRET=<value>` | Edit file, restart `wrangler dev` |

**Rotation order** — backend first, then worker, in the same window:

```bash
NEW=$(openssl rand -hex 32)

# 1. Backend (it accepts BOTH old and new only if you maintain a list — we don't,
#    so the window between this and step 2 will fail D1 sync. That's tolerable;
#    sync is nightly. If you can't tolerate it, deploy a temporary "accept either"
#    backend first.)
bash scripts/digitalocean.sh var-set syrabit-backend D1_SYNC_SECRET="$NEW"   # Task #336 — DO replaces Railway

# 2. Worker
cd workers/edge-proxy && echo "$NEW" | wrangler secret put D1_SYNC_SECRET

# 3. Cloud Run (if the backend is deployed there too)
gcloud secrets versions add d1-sync-secret --data-file=- <<< "$NEW"
gcloud run services update syrabit-backend \
  --update-secrets D1_SYNC_SECRET=d1-sync-secret:latest

# 4. Replit Secrets — set via the Secrets pane (do NOT echo to a file).

# 5. Local .dev.vars — APPEND or replace the single line, do not
#    overwrite the whole file (it may hold BACKEND_URL, PAGES_ORIGIN,
#    BACKEND_ORIGIN_SECRET, etc. for `wrangler dev`):
sed -i.bak '/^D1_SYNC_SECRET=/d' workers/edge-proxy/.dev.vars 2>/dev/null || true
echo "D1_SYNC_SECRET=$NEW" >> workers/edge-proxy/.dev.vars
```

**Direction of break:** if the worker has an old value, nightly D1
sync silently 401s (search api logs for `d1-export 401`). The site
still serves stale content.

### 1.2 `BACKEND_ORIGIN_SECRET` ↔ `ORIGIN_SHARED_SECRET`

The header (`X-Origin-Auth`) the worker injects on every backend fetch
when the backend is on Cloud Run. Cloud Run's
`OriginSharedSecretMiddleware` rejects requests that don't carry it,
so randoms can't bypass `api.syrabit.ai` and hit the Cloud Run URL
directly. **Same value, two names** — the worker calls it
`BACKEND_ORIGIN_SECRET`, the backend calls it `ORIGIN_SHARED_SECRET`.

| Place | Variable | How |
|---|---|---|
| Worker | `BACKEND_ORIGIN_SECRET` | `wrangler secret put BACKEND_ORIGIN_SECRET` |
| Backend (Cloud Run) | `ORIGIN_SHARED_SECRET` | Secret Manager binding — see `artifacts/syrabit-backend/CLOUDRUN-DEPLOY.md` |
| `workers/edge-proxy/.dev.vars` | `BACKEND_ORIGIN_SECRET=<value>` | For `wrangler dev` against a real Cloud Run backend |

**Rotation order** — backend MUST accept the new value before the
worker starts sending it:

```bash
NEW=$(openssl rand -hex 32)

# 1. Backend: add new secret version, redeploy with `ORIGIN_SHARED_SECRET=$NEW`
gcloud secrets versions add origin-shared-secret --data-file=- <<< "$NEW"
gcloud run services update syrabit-backend --update-secrets ORIGIN_SHARED_SECRET=origin-shared-secret:latest

# 2. Worker
echo "$NEW" | wrangler secret put BACKEND_ORIGIN_SECRET
```

**Direction of break:** if you flip the worker first, every API call
through `api.syrabit.ai` returns 401 immediately — total outage.
Always backend → worker.

(Skip this entire section if you're on Digital Ocean and ORIGIN_SHARED_SECRET is enforced — DO doesn't enforce origin
secrets; the variable is unused.)

### 1.3 `EDGE_AI_FALLBACK_SECRET` ↔ `WORKERS_AI_FALLBACK_SECRET`

Shared secret for the `/api/ai/fallback/{chat,embed,tts,stt}` worker
routes. Backend sends it as `X-Edge-AI-Secret`; worker rejects 401 if
it doesn't match. Stops randoms from burning Workers AI quota by
hitting `api.syrabit.ai/api/ai/fallback/*` directly.

| Place | Variable | How |
|---|---|---|
| Worker | `EDGE_AI_FALLBACK_SECRET` | `wrangler secret put EDGE_AI_FALLBACK_SECRET` |
| Backend (Digital Ocean — Task #336) | `WORKERS_AI_FALLBACK_SECRET` | `bash scripts/digitalocean.sh var-set syrabit-backend WORKERS_AI_FALLBACK_SECRET=…` |
| Replit Secrets | `WORKERS_AI_FALLBACK_SECRET` | Secrets pane |

**Rotation order:** worker first (so it accepts new), then backend.
Wrong order = AI fallback 401s on the next provider failure (which
might be hours later — silent).

```bash
NEW=$(openssl rand -hex 32)

# 1. Worker (accepts new value)
echo "$NEW" | wrangler secret put EDGE_AI_FALLBACK_SECRET

# 2. Backend — Digital Ocean (Task #336)
bash scripts/digitalocean.sh var-set syrabit-backend WORKERS_AI_FALLBACK_SECRET="$NEW"

# 2b. Backend — Cloud Run (if both backends are live in parallel)
gcloud secrets versions add workers-ai-fallback-secret --data-file=- <<< "$NEW"
gcloud run services update syrabit-backend \
  --update-secrets WORKERS_AI_FALLBACK_SECRET=workers-ai-fallback-secret:latest
```

### 1.4 `JWT_SECRET` and `ADMIN_JWT_SECRET`

Backend-only signing keys. **Two independent values** — the boot guard
in `config.py` (Task #770) refuses to start if they're equal. Rotation
invalidates every existing session of that audience.

| Place | Variable | Notes |
|---|---|---|
| Replit Secrets | `JWT_SECRET`, `ADMIN_JWT_SECRET` | required — backend won't boot without them |
| Backend (Digital Ocean — Task #336) | same | `bash scripts/digitalocean.sh var-set syrabit-backend JWT_SECRET="$(openssl rand -hex 48)"` |
| Backend (Cloud Run) | same | Secret Manager bindings |

**Direction of break:** rotating `JWT_SECRET` logs out every signed-in
user. Rotating `ADMIN_JWT_SECRET` logs out every admin. Plan it.

### 1.5 `SYNTHETIC_PROBE_CF_ACCESS_*`

The Cloudflare Access service-token pair the synthetic-probe worker
uses to bypass Access on protected admin endpoints. Lives in
`workers/edge-proxy/src/synthetic-probe.ts`.

| Place | Variable | How |
|---|---|---|
| Worker | `SYNTHETIC_PROBE_CF_ACCESS_CLIENT_ID` (var, public) | `wrangler.toml` `[vars]` or dashboard |
| Worker | `SYNTHETIC_PROBE_CF_ACCESS_CLIENT_SECRET` (secret) | `wrangler secret put SYNTHETIC_PROBE_CF_ACCESS_CLIENT_SECRET` |
| Cloudflare Zero Trust dashboard | service token issuance | Access → Service Auth → rotate |

Generate the new token in the Zero Trust dashboard first, paste both
ID and secret into the worker, deploy, then revoke the old token.

---

## 2. Single-place secrets (rotate at the source-of-truth provider)

These live in exactly one place. Rotating them means: change them at
the upstream provider, then mirror the new value into Replit Secrets +
the backend deploy env. They are **never** set on Cloudflare Pages or
the worker — `apply-pages-config.mjs` actively strips them from Pages
to prevent leaking into the public build log.

| Secret | Source-of-truth | Consumed by |
|---|---|---|
| `RAZORPAY_KEY_SECRET` | Razorpay dashboard → Settings → API Keys | backend |
| `RAZORPAY_WEBHOOK_SECRET` | Razorpay dashboard → Webhooks | backend (signature verification) |
| `RAZORPAY_KEY_ID` | Razorpay dashboard | backend |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Stripe dashboard | backend |
| `RESEND_API_KEY` | Resend dashboard | backend (transactional email) |
| `GROQ_API_KEY`, `GROQ_API_KEY_2` | console.groq.com | backend |
| `GEMINI_API_KEY`, `GEMINI_API_KEY_2` | aistudio.google.com | backend |
| `OPENROUTER_API_KEY` | openrouter.ai | backend |
| `CEREBRAS_API_KEY` | cerebras.ai | backend |
| `SARVAM_API_KEY*` | sarvam.ai | backend |
| `XAI_API_KEY` | x.ai console | backend |
| `OPENAI_API_KEY` | platform.openai.com | backend |
| `JINA_API_KEY`, `VOYAGE_API_KEY` | provider dashboards | backend |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Cloud Console → OAuth credentials | backend (Google sign-in) |
| `CF_TURNSTILE_SECRET_KEY` | Cloudflare dashboard → Turnstile | backend (chat + auth verification). The corresponding **site key** is `VITE_TURNSTILE_SITE_KEY` and goes on Pages, not the backend. |
| `MONGO_URL` | MongoDB Atlas → Database Access | backend |
| `DATABASE_URL` | Supabase / Postgres provider | backend |
| `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY`, `SUPABASE_URL` | supabase.com | backend |
| `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` | upstash.com | backend |
| `SESSION_SECRET` | self-generated (`openssl rand -hex 48`) | backend |
| `CF_ANALYTICS_API_TOKEN`, `CF_PAGES_API_TOKEN`, `CF_ZONE_ID` | Cloudflare dashboard → API Tokens | backend (analytics + deploy automation) |
| `DIGITALOCEAN_ACCESS_TOKEN` (Task #336 — replaces `RAILWAY_API_TOKEN`) | cloud.digitalocean.com → API → Tokens & Keys | local tooling (`scripts/digitalocean.sh`, `pnpm run do:*`) + GitHub Actions (`.github/workflows/digitalocean-deploy.yml` repo secret of the same name). Companion repo secrets: `DO_APP_ID_SYRABIT_BACKEND`, `DO_APP_ID_RUST_CORE`. |
| `GITHUB_TOKEN` | github.com → Developer Settings | local tooling only |
| `TRUSTPILOT_API_KEY` | Trustpilot dashboard | backend |
| `KV_ALERT_SECRET` | self-generated | worker only (`wrangler secret put`) |

After rotating any of these, mirror to:

1. **Replit Secrets** (so local dev keeps working).
2. **Backend deploy env** — Digital Ocean App Platform (Task #336 made it the sole runtime origin; Railway and Cloud Run are decommissioned).
3. Restart the workflow / redeploy.

---

## 3. Frontend (Pages) build-time variables

`VITE_*` variables are baked into the public JavaScript bundle at
build time. They are NOT secrets — anyone can read them with
DevTools. Rotating one means a new Pages build:

```bash
pnpm run pages:apply-config   # syncs from Replit Secrets to Pages
# Trigger a new Pages deploy (push to main, or "Retry deployment" in dashboard).
```

Currently in this category: `VITE_BACKEND_URL`, `VITE_WORKER_API_URL`,
`VITE_GA4_ID`, `VITE_TURNSTILE_SITE_KEY`,
`VITE_FIREBASE_*` (if used).

`apply-pages-config.mjs` enforces a hard deny-list (`DO_NOT_SET`) of
backend secrets that must never be set on Pages. If you see a build
log mentioning one of those names, stop the deploy — the secret is
already burnt.

---

## 4. Verification checklist (run after every rotation)

```bash
# Backend boots cleanly (no RuntimeError on missing secrets)
curl -fsS https://api.syrabit.ai/api/health/deep | jq '.status'

# Worker → backend handshake (BACKEND_ORIGIN_SECRET / EDGE_AI_FALLBACK_SECRET)
curl -fsS https://api.syrabit.ai/api/health | jq '.edge_proxy_ok'

# D1 sync — manual trigger
curl -fsS -X POST https://api.syrabit.ai/api/edge/d1-sync \
  -H "Authorization: Bearer $D1_SYNC_SECRET" | jq '.synced'

# JWT — sign in as test user, verify the cookie returned is freshly signed
# (decoded `kid` claim should match a hash of the new JWT_SECRET if you use kid;
# otherwise just confirm login works at https://syrabit.ai/login).

# Razorpay webhook — trigger a test payment and check api logs for
# "razorpay webhook signature verified".
```

If any check fails, the most common cause is a value mismatch between
the worker and the backend. Re-check §1 in order.

---

## 5. When in doubt

- **Always rotate the backend first**, then the worker, unless the
  section above says otherwise. Backend accepting an extra value is
  cheap; worker sending an unaccepted value is an outage.
- **Never put a backend secret in `wrangler.toml` `[vars]`** —
  `[vars]` get *deployed* on every `wrangler deploy` and overwrite
  whatever the dashboard had. Only `wrangler secret put` is safe for
  secrets.
- **Never put a backend secret on Cloudflare Pages** —
  `apply-pages-config.mjs` will refuse, but only if you run it.
  Don't bypass the script.
- **Generate new secrets with high entropy**: `openssl rand -hex 32`
  for shared bearer tokens (32 bytes = 64 hex chars), or
  `python3 -c 'import secrets; print(secrets.token_hex(48))'` for JWT
  signing keys (48 bytes = 96 hex chars; the boot guard requires 64+).

---

## See also

- `docs/DEPLOYMENT.md` — environment variable matrix per surface
- `workers/edge-proxy/DEPLOY.md` — initial worker setup + D1 sync
  bootstrap
- `artifacts/syrabit-backend/CLOUDRUN-DEPLOY.md` — Cloud Run secret
  manager bindings
- `docs/DIGITALOCEAN-DEPLOYMENT.md` — Digital Ocean env setup (Task #336; replaces the retired Railway runbook at `artifacts/syrabit-backend/RAILWAY-DEPLOY.md`)
- `artifacts/syrabit/scripts/apply-pages-config.mjs` — the
  deny-list of secrets that must never reach Pages
- `docs/audits/FULL_APP_AUDIT_2026-04-23.md` — findings S1
  (D1_SYNC_SECRET committed) and S2 (JWT secret derivation) that
  motivated this runbook
