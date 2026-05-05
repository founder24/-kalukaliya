# syrabit-embed-worker

Custom Cloudflare Worker that exposes `POST /embed` for the Syrabit RAG
pipeline. Runs Gemma-300M + Qwen3-0.6B on the Workers AI binding,
mean-pools their hidden states and folds them down to a 1024-dim vector
that drops straight into the existing Pinecone serverless index
(1024-dim, cosine).

The wrangler workspace ships **two** environments:

| Env          | Worker name                      | Route                              | Purpose                                      |
| ------------ | -------------------------------- | ---------------------------------- | -------------------------------------------- |
| `production` | `syrabit-embed-worker`           | `embed.syrabit.ai/*`               | Live traffic from `providers/workers_embed.py`. |
| `staging`    | `syrabit-embed-worker-staging`   | `embed-staging.syrabit.ai/*`       | Canary for new models / pooling formulas / `EMBED_DIMS` bumps before flipping production. |

Both envs share `src/index.ts`. The only intentional drift is
`EMBED_WORKER_VERSION` (staging carries a `-staging` suffix so
`/version` makes it obvious which worker answered) and the
`EMBED_SHARED_SECRET` wrangler secret, which is set independently per
env.

---

## One-time staging setup

Production was wired up in Task #400. Staging needs the same three
pieces — wrangler secret, DNS placeholder, and WAF SBFM-skip rule —
mirrored under the staging hostname.

### 1. Push the staging secret

```bash
cd artifacts/syrabit/workers/embed-worker
# Generate a fresh 256-bit secret — do NOT reuse the production one.
openssl rand -hex 32 | wrangler secret put EMBED_SHARED_SECRET --env staging
```

Store the same value in the backend so smoke-test runs from a workstation
can authenticate:

- Local: export `EMBED_STAGING_SHARED_SECRET=<value>` in your shell.
- CI / shared: drop it into the Replit secret store (or whichever vault
  the smoke-test job reads from) under `EMBED_STAGING_SHARED_SECRET`.
  The production backend keeps using `EMBED_SHARED_SECRET`; only the
  smoke harness reads the staging variant.

> **Shortcut:** steps 2 + 3 below are codified in
> [`scripts/setup-staging-cloudflare.sh`](scripts/setup-staging-cloudflare.sh).
> Export `CF_API_TOKEN` (Zone:Read + DNS:Edit + Zone WAF:Edit) and
> `CF_ZONE_ID`, then run the script — it is idempotent and upserts
> both the AAAA record and the SBFM-skip rule. Use the manual steps
> below only if you don't have an API token and have to click through
> the dashboard.

### 2. DNS placeholder (proxied AAAA)

Cloudflare will not bind a Workers route to a hostname that has no DNS
record on the zone. Mirror the production setup:

- Zone: `syrabit.ai`
- Type: `AAAA`
- Name: `embed-staging`
- Target: `100::` (the documented "discard" address — the request
  never reaches an origin because the Worker route intercepts it)
- Proxy status: **Proxied** (orange cloud)
- TTL: Auto

This matches the existing `embed` record one-for-one; the only change
is the hostname.

### 3. WAF SBFM-skip rule

Super Bot Fight Mode will otherwise challenge the backend's probes
(they look like server-to-server JSON traffic, not a browser session).
Add a custom rule under **Security → WAF → Custom rules** that mirrors
the production rule:

- Name: `Skip SBFM for embed-staging worker`
- When incoming requests match: `(http.host eq "embed-staging.syrabit.ai")`
- Then take action: **Skip**, with these checked:
  - All remaining custom rules
  - Super Bot Fight Mode
- Place the rule directly above the existing `Skip SBFM for embed worker`
  rule so the order is stable.

You can confirm by hitting `/health` from a non-Cloudflare IP and
checking that no `cf-mitigated` header comes back.

---

## Deploy to staging

```bash
cd artifacts/syrabit/workers/embed-worker
pnpm install            # first time only, picks up wrangler
pnpm run deploy:staging # = wrangler deploy --env staging
```

Wrangler will publish `syrabit-embed-worker-staging` and bind it to
`embed-staging.syrabit.ai/*`. The `[env.staging.ai]` block re-declares
the Workers AI binding (top-level `[ai]` does **not** inherit into
named envs — Task #400 deploy fix).

---

## Smoke check

After every staging deploy, run the bundled smoke script — it is the
canonical four-shape probe and exits non-zero on the first failed
assertion, so it's safe to wire into a CI gate after
`wrangler deploy --env staging`:

```bash
EMBED_STAGING_SHARED_SECRET=... ./scripts/smoke.sh staging
# or, against production after promote:
EMBED_SHARED_SECRET=... ./scripts/smoke.sh production
```

The script asserts: `/health` returns 200 with `dims=1024`, the
`version` carries the `-staging` suffix on staging, unauthorized
`/embed` returns 401, and authorized `/embed` returns vectors of
length exactly 1024. If `dims` ever differs from 1024 — **stop**. The
Pinecone index is fixed at 1024 and a mismatch will silently corrupt
vector search after promote.

If you want to drive the probe by hand instead:

```bash
HOST=https://embed-staging.syrabit.ai
SECRET="$EMBED_STAGING_SHARED_SECRET"

# 1. Health — expect 200 + dims:1024 + the configured models.
curl -sS -w '\n[HTTP %{http_code} in %{time_total}s]\n' "$HOST/health"

# 2. Version — expect the staging suffix so you know which worker answered.
curl -sS "$HOST/version"

# 3. Auth — no header should yield 401.
curl -sS -o /dev/null -w '[HTTP %{http_code}]\n' -X POST "$HOST/embed" \
  -H 'content-type: application/json' \
  -d '{"texts":["ping"]}'

# 4. Embed — with the secret, expect a 1024-long float vector per text.
curl -sS -X POST "$HOST/embed" \
  -H 'content-type: application/json' \
  -H "X-Embed-Secret: $SECRET" \
  -d '{"texts":["the mitochondria is the powerhouse of the cell","photosynthesis"]}' \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);v=d["vectors"][0];print({"dims":d["dims"],"count":d["count"],"version":d["model_version"],"models":d["models"],"first_vector_length":len(v),"first_4":v[:4]})'
```

Expected pass criteria:

- `/health` returns `{"ok": true, "dims": 1024, ...}` with `version`
  ending in `-staging`.
- `/embed` without `X-Embed-Secret` returns `401 {"error":"unauthorized"}`.
- `/embed` with the secret returns `vectors` of length 2 where each
  inner vector is exactly 1024 floats. `dims` and `model_version` in
  the response must match what `/health` reported.

If `dims` differs from 1024 — **stop**. The Pinecone index is fixed at
1024 and a mismatch will silently corrupt vector search after promote.

For a longer-running check, point a backend instance at the staging
URL by overriding the env var:

```bash
EMBED_PROVIDER_PRIMARY=workers_ai_custom \
WORKERS_EMBED_URL=https://embed-staging.syrabit.ai/embed \
EMBED_SHARED_SECRET="$EMBED_STAGING_SHARED_SECRET" \
python -m artifacts.syrabit-backend.scripts.embed_smoke
```

(Use a scratch backend; do **not** flip the production env vars to the
staging URL.)

---

## Promote staging → production

Promote only after the smoke check passes and you've eyeballed the
Workers AI dashboard for `syrabit-embed-worker-staging` to confirm
latency and error counts match production.

1. Bump `EMBED_WORKER_VERSION` in **both** the `[vars]` and
   `[env.production.vars]` blocks of `wrangler.toml` (and the staging
   one to the matching `-staging` suffix). The backend's
   `/admin/health/embed-stack` reads `model_version` and uses it to
   detect a vector-shape change, so the bump must be a real semver
   bump if anything in the pooling formula changed.
2. Commit + open a PR with the `wrangler.toml` change and any
   `src/index.ts` diff that was canaried.
3. After merge, deploy production:

   ```bash
   cd artifacts/syrabit/workers/embed-worker
   pnpm run deploy:production  # = wrangler deploy --env production
   ```

4. Re-run the smoke check against `https://embed.syrabit.ai` and append
   the result to `artifacts/syrabit/docs/infra/cutover-evidence/`.
5. Leave the staging worker at the same version — it is the rollback
   floor for the next change, not a long-lived divergent build.

### Rollback

`wrangler rollback --env production` reverts the production worker to
the prior published version. The staging env is unaffected, so you
can keep iterating there while production is back on the previous
build.
