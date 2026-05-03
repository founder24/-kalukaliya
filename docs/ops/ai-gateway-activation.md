# AI Gateway Activation Operational Checklist

**Purpose:** Stand up the Cloudflare AI Gateway (`syrabit-ai-gw`) and route
every `env.AI.run(...)` call from the `syrabit-edge` worker through it.
Once active, repeat embeddings / classification / chat prompts are served
from the gateway's response cache without re-billing the
Cloudflare-for-Startups $5,000 credit pool, and every Workers AI invoice
line item is tagged via `metadata.tag` so the monthly cost review can
group the spend.

**Scope:** Manual operational steps only. Code automation already in place:

- `aiGatewayOpts(env, "<tag>")` wraps every `env.AI.run` callsite in
  `workers/edge-proxy/src/index.ts`.
- `workers/edge-proxy/tests/workers-ai-tagging.test.ts` is a regression
  guard that fails CI if a future PR introduces an untagged callsite.
- `workers/edge-proxy/scripts/provision-ai-gateway.mjs` is the idempotent
  provisioning script invoked in Step 1 below.

---

## Three required gates (in order)

| Gate | What it does |
|---|---|
| **1. Provision gateway** | Creates `syrabit-ai-gw` with response caching enabled |
| **2. Set worker secret** | `WORKERS_AI_GATEWAY_ID` reaches the live worker |
| **3. Verify tagging** | AI Gateway → Logs shows `metadata.tag` on recent requests |

> The worker silently no-ops the gateway when `WORKERS_AI_GATEWAY_ID` is
> unset (calls go direct, no tag). It does **not** fail-closed — leaving
> the var unset is safe but means we are paying full price for every
> repeat embed.

---

## Prerequisites

| Item | Where to get it |
|---|---|
| `CLOUDFLARE_API_TOKEN` with **AI Gateway: Edit** on account `d66e40eac539fff1db270fddf384a5ec` | dash.cloudflare.com → My Profile → API Tokens → Create Custom Token |
| Wrangler CLI authenticated (`wrangler whoami`) | `pnpm dlx wrangler login` |
| Repo checkout with `workers/edge-proxy/` | this repo |

---

## Step 1 — Provision the gateway

The provisioning script is **idempotent**: it creates the gateway if
missing, otherwise reconciles its caching / logging settings to the
policy declared in the script body. Run it any time you change that
policy — a hand-edit in the dashboard will be overwritten on the next
run, by design.

```bash
CLOUDFLARE_API_TOKEN=<your-token> \
  node workers/edge-proxy/scripts/provision-ai-gateway.mjs
```

**Expected output (first run):**

```
Provisioning AI Gateway "syrabit-ai-gw" on account d66e40eac539fff1db270fddf384a5ec
[missing] gateway "syrabit-ai-gw" not found — creating
[created] gateway: syrabit-ai-gw
```

**Expected output (subsequent runs):**

```
[found] gateway "syrabit-ai-gw" already exists — reconciling settings
[updated] gateway: syrabit-ai-gw
```

Always preview with `--dry-run` first if you've edited the script:

```bash
CLOUDFLARE_API_TOKEN=<your-token> \
  node workers/edge-proxy/scripts/provision-ai-gateway.mjs --dry-run
```

### Cache TTL policy

The script sets a gateway-wide default of **3600s (1h)** with
`cache_invalidate_on_update = true`. Per-route overrides for
embeddings (24h is reasonable — embeddings are deterministic) are
configured in the dashboard:

1. dash.cloudflare.com → AI → AI Gateway → `syrabit-ai-gw` → Settings.
2. Add a route override: model = `@cf/baai/bge-large-en-v1.5`,
   cache TTL = `86400`.
3. Optional: shorter TTL for chat (e.g. `300s`) if you observe stale
   responses in QA. Chat already uses `temperature: 0.3` so most
   prompts are cacheable.

---

## Step 2 — Point the worker at the gateway

The worker reads `env.WORKERS_AI_GATEWAY_ID` at runtime. Set it as a
Wrangler **secret** (not a plaintext var) so a future deploy from a
machine without the secret cannot accidentally clear it. The line in
`wrangler.toml` is intentionally left commented — a plaintext var
with the same name would override the secret on every deploy.

```bash
echo "syrabit-ai-gw" | wrangler secret put WORKERS_AI_GATEWAY_ID --name syrabit-edge
```

Then deploy the worker the usual way (CI on push to `main`, or
`pnpm --filter @workspace/edge-proxy deploy` from a maintainer
machine). No code change is needed — `aiGatewayOpts(env, ...)` flips
on as soon as the secret is present.

---

## Step 3 — Verify the tagging end-to-end

1. Trigger a Workers AI call. The simplest path is to force an embed
   fallback through the FastAPI backend (or hit `/api/edge/vector-search`
   on a chapter that isn't in cache).
2. dash.cloudflare.com → AI → AI Gateway → `syrabit-ai-gw` → Logs.
3. Open a recent request. Confirm:
   - `metadata.tag` is one of `workers-ai-fallback:chat`,
     `workers-ai-fallback:embed`, `workers-ai-fallback:stt`,
     `workers-ai-fallback:tts`, or `workers-ai-edge-vector-search`.
   - The model name matches the capability (e.g. embed →
     `@cf/baai/bge-large-en-v1.5`).
4. Make the same call again with the same input. The second request
   should show `cached: true` in the Logs row and contribute $0 to the
   credit pool.

If the tag column is empty, `WORKERS_AI_GATEWAY_ID` did not reach the
worker — re-run Step 2 and confirm `wrangler secret list --name
syrabit-edge` includes `WORKERS_AI_GATEWAY_ID`.

---

## Rollback

To stop routing through the gateway (e.g. emergency debug, or to
sidestep a gateway outage):

```bash
wrangler secret delete WORKERS_AI_GATEWAY_ID --name syrabit-edge
```

`aiGatewayOpts(env, ...)` returns `undefined` when the var is absent,
so `env.AI.run` falls back to its 2-argument shape — calls go direct,
no tagging, no caching. Re-run Step 2 to re-arm.

---

## Related

- `docs/cloudflare-cost-map.md` — AI Gateway row in the inventory.
- `docs/cloudflare-monthly-cost-review.md` — Step 5 verifies the tag
  list each month.
- `workers/edge-proxy/src/index.ts` — `aiGatewayOpts` and the two
  `env.AI.run` callsites in `handleAiFallback` + the edge
  vector-search handler.
- `workers/edge-proxy/tests/workers-ai-tagging.test.ts` — CI guard.
- `workers/edge-proxy/src/ai-gateway-cache-alert.ts` — Task #311
  watchdog. Pages via `SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL` when the
  rolling 24h embed cache-hit-rate falls below the 50% floor
  documented above. Required additional secret:
  `AI_GATEWAY_ANALYTICS_TOKEN` (CF API token with `AI Gateway: Read`
  scope on account `d66e40eac539fff1db270fddf384a5ec`). When the
  watchdog fires, the on-call action is the **Rollback** section
  above — disable the gateway routing, investigate the cache-key
  regression, then re-arm via Step 2.
