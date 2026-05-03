# Cloudflare Feature Inventory — Syrabit.ai

Zone: **syrabit.ai** · Account: `d66e40eac539fff1db270fddf384a5ec`  
Worker: **syrabit-edge** · Route: `api.syrabit.ai/*`, `syrabit.ai/*`, `www.syrabit.ai/*`

---

## Active Features (Code-Controlled)

| Feature | Status | Where |
|---|---|---|
| Workers Smart Placement | ✅ Active | `wrangler.toml` `[placement] mode="smart"` |
| Workers AI binding (fallback fan-out) | ✅ Active | `wrangler.toml` `[ai]` + `handleAiFallback` |
| Vectorize edge semantic search | ✅ Active | `[[vectorize]]` SYLLABUS_INDEX (legacy 768-dim binding retired in Task #308) |
| D1 (content DB replica) | ✅ Active | `[[d1_databases]]` CONTENT_DB |
| KV (rate limit + bot HTML cache) | ✅ Active | `[[kv_namespaces]]` RATE_LIMIT + BOT_HTML_CACHE |
| Workers Logpush | ✅ Active | `wrangler.toml` `logpush=true` |
| Workers Observability (10% sampling) | ✅ Active | `wrangler.toml` `[observability]` |
| HTTP/3 alt-svc injection | ✅ Active | `index.ts` injects `alt-svc: h3=":443"` on all Pages responses |
| CF Image Resizing (`/cdn-cgi/image/`) | ✅ Active | `src/utils/imageCdn.js` — all image components use `cdnImage()`/`cdnSrcSet()` |
| Synthetic probe (1-min cron) | ✅ Active | `src/synthetic-probe.ts` + `wrangler.toml` crons |
| Bot HTML KV cache (prerender) | ✅ Active | `handleBotContentRequest` in `index.ts` |
| AI crawler hard-block (403) | ✅ Active | `AI_BOT_UA` regex in `index.ts` |
| Edge cache (Workers Cache API) | ✅ Active | `caches.default` — cacheable API routes per `monitored-urls.json` |
| `Cache-Tag` headers | ✅ Active | `buildCacheTags()` in `index.ts` — see tag taxonomy below |
| `Surrogate-Control` headers | ✅ Active | All D1 + cached backend responses |
| `Vary: Accept-Encoding, Accept` | ✅ Active | All cacheable JSON + XML responses |
| Tag-based edge purge | ✅ Active | `POST /api/edge/purge` with `{ "prefixes": [...] }` |

---

## Dashboard-Required Features

These must be enabled/configured in the **Cloudflare dashboard** (dash.cloudflare.com).  
They cannot be provisioned via `wrangler.toml` or Worker code.

### 1. Argo Smart Routing

> **Impact**: cuts origin RTT by 30–60 ms for Assam (India) traffic by routing through Cloudflare's private backbone instead of the public internet.

**Enable**:  
Dashboard → syrabit.ai → Speed → Optimization → **Argo** → Toggle ON  
*Bundled with the Enterprise zone plan (free Cloudflare-for-Startups upgrade) — do **not** subscribe to "Smart Shield Argo Zone Level Plan - Basic" separately, that is a duplicate $5/mo charge for the same product (cancelled 2026-05-03; see `docs/cloudflare-cost-map.md`).*

**Also enable for the Workers subdomain** if `api.syrabit.ai` is a separate zone.

---

### 2. Tiered Caching (Upper-Tier POP)

> **Impact**: cache misses from edge POPs in India/Asia go to a Singapore/Tokyo upper-tier POP first, not directly to the Railway origin. Cuts origin load by ~60–70% on cache-miss traffic.

**Enable**:  
Dashboard → syrabit.ai → Caching → **Tiered Cache** → Enable  
Recommended topology: **Smart Tiered Cache** (CF picks the best upper tier per region automatically)

---

### 3. HTTP/3 + QUIC (Dashboard Toggle)

> **Note**: The Worker already injects `alt-svc: h3=":443"; ma=86400` on all Pages responses, which tells supporting browsers to upgrade. But the upgrade only works if HTTP/3 is also enabled at the zone level.

**Enable**:  
Dashboard → syrabit.ai → Speed → Optimization → **HTTP/3 (with QUIC)** → ON  
Dashboard → syrabit.ai → Speed → Optimization → **0-RTT Connection Resumption** → ON

---

### 4. Early Hints (103 Preload)

> **Impact**: Cloudflare emits `103 Early Hints` with `Link: preload` for the main JS chunk and primary font before the full HTML is ready, letting browsers start downloading critical assets ~200–400 ms earlier.

**Enable**:  
Dashboard → syrabit.ai → Speed → Optimization → **Early Hints** → ON

**Preload headers are already set** in `index.html`:
```html
<link rel="preload" href="https://fonts.gstatic.com/s/spacegrotesk/..." as="font" type="font/woff2" crossorigin />
```
CF automatically converts `<link rel="preload">` tags in HTML responses into Early Hints when the feature is enabled.

---

### 5. Polish (Image Compression) + Mirage (Lazy Loading)

> **Impact**: Polish converts JPEG/PNG to WebP/AVIF automatically. Mirage lazy-loads images on slow connections. Together they cut image payload 40–70% for mobile users.

**Enable**:  
Dashboard → syrabit.ai → Speed → Optimization → **Polish** → **Lossless** (or Lossy for more aggressive savings)  
Dashboard → syrabit.ai → Speed → Optimization → **Mirage** → ON

> **Note**: `imageCdn.js` already routes images through `/cdn-cgi/image/format=auto,width=N,...` which handles format conversion at the Workers layer. Polish is additive — it also applies to images that don't go through `imageCdn.js` (e.g., OG images, icons served directly by Pages).

---

### 6. Cache Rules (Replacing Legacy Page Rules)

> **Current state**: the Worker handles all cache logic in code via `monitored-urls.json` → `getCacheTtl()` / `isCacheable()`. There are **no legacy Page Rules** for caching — the Worker IS the cache layer.

The Cloudflare Cache Rules dashboard (syrabit.ai → Caching → Cache Rules) is used alongside the Worker. The Worker's `caches.default` and CF's zone-level cache are complementary:

| Layer | Handles |
|---|---|
| Worker `caches.default` | Per-POP Workers Cache — stores API JSON (D1 + backend fetch) |
| CF Zone Cache | Static assets from Pages (JS/CSS/images), zone-level CDN cache |

**Recommended Cache Rules** to add in the dashboard (for static assets from Pages):

| Rule | Expression | Action |
|---|---|---|
| Immutable static assets | `http.request.uri.path matches "^/assets/.*\|^/icons/.*\|^/fonts/.*"` | Cache everything, Edge TTL: 1 year, Browser TTL: 1 year |
| Service worker | `http.request.uri.path eq "/sw.js"` | Cache everything, Edge TTL: 5 min (allows SW updates) |
| Manifest | `http.request.uri.path eq "/manifest.json"` | Cache everything, Edge TTL: 1 day |

---

## Cache Tag Taxonomy

The Worker now sets `Cache-Tag` on all cacheable responses. Use these tags to purge content after a publish event (dashboard or API):

| Tag | Covers |
|---|---|
| `api-content` | All `/api/content/*` responses |
| `library-bundle` | `/api/content/library-bundle` (navbar payload) |
| `chapter-{id}` | A specific chapter's API responses |
| `subject-{slug}` | A specific subject's API responses |
| `seo-pages` | All `/api/seo/**` SEO HTML and data |
| `sitemap` | `/api/seo/sitemap*` and `/sitemap.xml` |

**Tag-based purge via CF API**:
```bash
curl -X POST "https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache" \
  -H "Authorization: Bearer {CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"tags": ["chapter-{id}"]}'
```

**Edge purge via Worker** (internal — used by the publish pipeline):
```bash
curl -X POST "https://api.syrabit.ai/api/edge/purge" \
  -H "Authorization: Bearer {D1_SYNC_SECRET}" \
  -H "Content-Type: application/json" \
  -d '{"prefixes": ["/api/content/chapters/{id}"]}'
```

---

## Monitored URLs → Cache TTL Reference

Cache TTLs come from `monitored-urls.json` (single source of truth). Key entries:

| Path prefix | TTL | Notes |
|---|---|---|
| `/api/content/library-bundle` | 300s | Slim=1 variant for navbar |
| `/api/content/chapters/` | 300s | Per-chapter content |
| `/api/seo/` | 3600s | SEO HTML pages |
| `/api/seo/sitemap*` | 3600s | Sitemap documents |

Modify TTLs in `monitored-urls.json` → redeploy the Worker. No dashboard change needed.

---

## Zone / Account IDs

| Resource | ID |
|---|---|
| Cloudflare Account | `d66e40eac539fff1db270fddf384a5ec` |
| Worker name | `syrabit-edge` |
| KV — RATE_LIMIT | `3ee723af8e82480eb6d4855b0ca09f69` |
| KV — BOT_HTML_CACHE | `a92591899d544ddb8ac61c54d2b40180` |
| KV — CONTENT_CACHE | `981e939bcca445c481d4be818ebefee7` |
| D1 — syrabit-content | `da5b5b9d-a8f9-43dd-bd23-a938f5c0cf69` |
| Vectorize — syllabus-index-v2 | (1024-dim cosine, Gemini multilingual-e5-large) |
| ~~Vectorize — syllabus-index~~ | (768-dim BGE legacy fallback — retired in Task #308) |
| Pages project | `syrabit-zip-convert` |

> **Zone ID**: retrieve from the dashboard sidebar for `syrabit.ai` — needed for Cache Tag purge API calls.

---

## Quick-Reference: Deploy the Worker

```bash
cd workers/edge-proxy
npx wrangler deploy
```

Required secrets (set once via `wrangler secret put <NAME>`):
- `D1_SYNC_SECRET` — shared with the backend for D1 sync + edge purge auth
- `BACKEND_ORIGIN_SECRET` — injected as `X-Origin-Auth` on every backend fetch
- `EDGE_AI_FALLBACK_SECRET` — guards `/api/ai/fallback/*` and `/api/edge/search`
- `SYNTHETIC_PROBE_CF_ACCESS_CLIENT_ID` / `_SECRET` — for the 1-min probe
- `SYNTHETIC_PROBE_ADMIN_JWT` — admin token for the diagnostics probe
- `SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL` — Slack/PagerDuty webhook for alerts
