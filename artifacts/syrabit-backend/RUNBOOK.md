# Syrabit.ai Backend — Ops Runbook

Operational notes for on-call engineers. Keep entries short and
tactical — link out to source for the gory details.

---

## Pinecone chunk migration (task #206)

### One-time migration

After running `embed_chunks_bulk` to ensure all chunks have embeddings,
copy them to Pinecone:

```bash
# Dry run first
python scripts/migrate_chunks_to_pinecone.py --dry-run --ensure-index

# Real migration
python scripts/migrate_chunks_to_pinecone.py --ensure-index
```

### Initial run evidence (2026-05-01)

| Metric | Value |
|--------|-------|
| MongoDB embedded chunks | 0 |
| Pinecone `syrabit-ahsec` vectors | 0 |
| Migration result | `{total: 0, upserted: 0, failed: 0, duration_s: 4.01}` |
| Index host | `syrabit-ahsec-vtlityl.svc.aped-4627-b74a.pinecone.io` |
| Index spec | AWS us-east-1, 1024-dim cosine, serverless |
| `PINECONE_WRITE` | `true` (set after migration) |

The chunks collection was empty at migration time — chapter content has not
been ingested yet. Both Atlas $vectorSearch and Pinecone returned empty results
for all 5 AHSEC/SEBA parity queries (consistent — both backends agree).

### Re-run after content ingestion

Once `embed_chunks_bulk` has run with the content pipeline:

```bash
# Verify counts match
python scripts/validate_rag_parity.py
# Expected: "PARITY VALIDATED — 5/5 queries above 70% threshold"
```

### Environment variables

| Variable | Value | Notes |
|----------|-------|-------|
| `PINECONE_API_KEY` | secret | Pinecone API key |
| `PINECONE_INDEX` | `syrabit-ahsec` | Index name |
| `PINECONE_WRITE` | `true` | Enables Pinecone writes in embed_chunks_bulk |
| `PINECONE_SKIP_MONGO_EMBED` | unset | When `PINECONE_WRITE=true`, MongoDB embedding write is already skipped by default. Set to `false` to re-enable it (Atlas fallback warm-up only). |
| `ATLAS_VS_ENABLED` | unset | Set to `true` to re-enable the Atlas $vectorSearch index check at startup (emergency fallback recovery only). Default: off. |
| `PINECONE_ATLAS_FALLBACK` | `false` | Set to `false` once Pinecone parity is confirmed to disable the Atlas fallback in RAG queries. |

---

## Drop MongoDB embedding arrays (Task #208 / Task #216)

After Pinecone parity is confirmed (≥70 % top-K overlap on the built-in
5 AHSEC/SEBA queries, or a larger custom set), run this to reclaim ~8 KB
per chunk document from MongoDB.  Each 1024-float embedding stored in Atlas
inflates document reads with no query benefit once Pinecone is the active
vector store.

### Pre-flight checklist

Work through every item in order before running the drop.

```bash
# 1. Confirm chunks collection is non-empty (content must be ingested first)
#    Connect to Atlas / mongosh and run:
db.chunks.countDocuments()
#    Expected: > 0

# 2. Validate Pinecone parity (≥70% top-K overlap on all 5 built-in queries)
python scripts/validate_rag_parity.py
#    Expected: "PARITY VALIDATED — 5/5 queries above 70% threshold"
#    For higher confidence, add more queries:
python scripts/validate_rag_parity.py \
    --queries "Newton laws of motion AHSEC Class 11" \
              "Photosynthesis SEBA Class 10 Biology" \
              "French Revolution AHSEC History" \
              "Linear equations algebra SEBA Class 9" \
              "Organic chemistry reactions AHSEC Class 12"
#    Expected: "PARITY VALIDATED — 10/10 queries above 70% threshold"

# 3. Confirm PINECONE_WRITE=true (dual-write active — no new embeddings lost)
echo $PINECONE_WRITE   # must print "true"

# 4. Confirm Atlas fallback is already disabled
echo $PINECONE_ATLAS_FALLBACK   # must print "false"

# 5. Confirm Atlas continuous backup is active in Atlas UI, OR take a
#    manual snapshot before proceeding.
```

### Execute the drop

```bash
# Step A — dry run: prints document count, makes no writes
python scripts/drop_mongo_embeddings.py --dry-run
#   Expected log line: "[DRY RUN] Would unset embedding on N chunk documents."

# Step B — validate on one subject first (optional but recommended)
python scripts/drop_mongo_embeddings.py --subject-id <subject_id> --dry-run
python scripts/drop_mongo_embeddings.py --subject-id <subject_id>
#   Expected log line: "remaining_with_embedding (subject_id=<id>)=0"

# Step C — full drop across all chunks
python scripts/drop_mongo_embeddings.py
#   Expected log line: "remaining_with_embedding=0"
#   Exit code 0 = success.  Exit code 1 = pre-flight guard failed, no writes.
```

### Drop the Atlas Vector Search index (manual, Atlas UI)

Run this only after the script logs `remaining_with_embedding=0`.

1. Open **Atlas UI → Database → Browse Collections → chunks →
   Indexes → Search Indexes**.
2. Delete the index named **`vector_index`**.
3. Confirm `ATLAS_VS_ENABLED` is **not** set (or is `false`) so startup
   no longer calls `ensure_vector_index()`.

### Run evidence

#### Initial cutover (2026-05-01) — deferred, content not yet ingested

| Metric | Value |
|--------|-------|
| Chunks with `embedding` field | 0 |
| Parity queries passed | 0/5 (both backends returned empty — consistent) |
| Dry-run output | `[DRY RUN] Would unset embedding on 0 chunk documents.` |
| Drop executed | No — deferred until content is ingested |
| Atlas vector index deleted | No — deferred |
| Reason for deferral | `embed_chunks_bulk` had not run; chunks collection was empty at Pinecone cutover time |

#### Post-ingestion run (date TBD) — to be filled in by operator

| Metric | Value |
|--------|-------|
| Chunks with `embedding` field (pre-drop) | _fill in_ |
| Parity queries passed | _fill in_ (e.g. "10/10 above 70% threshold") |
| Dry-run reported count | _fill in_ |
| Drop duration | _fill in_ (e.g. "42.3 s") |
| `dropped` count logged | _fill in_ |
| `failed` count logged | _fill in_ |
| `remaining_with_embedding` after drop | _fill in_ (target: 0) |
| Atlas vector index deleted | _fill in_ (Yes / No + date) |
| Operator | _fill in_ |
| Run date | _fill in_ |

### New ingestion behaviour

With `PINECONE_WRITE=true`, `embed_chunks_bulk` now defaults to **not** writing
the `embedding` float array to MongoDB (Task #208 default flip).  To restore
the old behaviour for an emergency Atlas warm-up, set
`PINECONE_SKIP_MONGO_EMBED=false` temporarily then restart workers.

---

## Assamese purity override propagation

**Endpoints**

- `GET    /admin/assamese-purity` — read live config + persisted override
- `PATCH  /admin/assamese-purity` — set `behaviour` and/or `threshold`
- `DELETE /admin/assamese-purity` — clear the override
- `POST   /admin/assamese-purity/test` — fire the sanitiser against a sample
- `GET    /admin/assamese-purity/stats` — dashboard counts

**How propagation works**

The override is persisted to `db.api_config.assamese_purity_override`
and held in-memory by each gunicorn worker. On every PATCH/DELETE
only the worker that served the request updates its own in-memory
copy synchronously. Sibling workers pick up the change from a
background poll loop in `routes/cms_sarvam_health.py`
(`_assamese_purity_refresh_loop`) that re-reads the persisted doc
every `_ASM_REFRESH_INTERVAL_SECONDS` (currently **15s**).

**Propagation budget: ~20s**

When the admin UI says a change applies "immediately", what we
actually promise on-call is:

> A PATCH or DELETE made on one worker is observed by every other
> worker within **~20 seconds** (one 15s poll cycle plus jitter for
> mongo round-trip and event-loop scheduling).

If a customer report says "I disabled translate but the bot is
still translating after 30 seconds", that is a real bug — escalate.
The expected behaviour is full convergence inside the 20s budget.

**What to check if propagation is broken**

1. `GET /admin/assamese-purity` on each worker (curl through the LB
   a few times) — `config.behaviour_source` should be `override` on
   all workers within 20s of the PATCH.
2. Look for `[INDIC-SANITIZE] reconciled persisted override` /
   `reconciled cleared override` log lines on each worker every
   ~15s. Missing lines mean the loop died.
3. Look for `[INDIC-SANITIZE] refresh loop tick failed` warnings —
   the loop swallows exceptions and keeps going, but a persistent
   failure (mongo down, auth error) means propagation is stalled.
4. As a last resort, restart the api workers — boot reloads the
   persisted doc synchronously via `apply_persisted_assamese_purity_override`.

**Alert: `assamese_override_refresh_stalled`**

Each worker bumps an in-process heartbeat
(`metrics._asm_last_refresh_at`) after every successful refresh tick.
The alerting loop pages on-call (email + webhook + persisted alert
+ push) when the heartbeat falls behind
`_ALERT_THRESHOLDS["assamese_refresh_stale_seconds"]` (default
**60s** = 4 missed ticks). The alert body includes the offending
worker's pid so you can target the restart.

What to do when this alert fires:

1. Tail api logs and look for `[INDIC-SANITIZE] refresh loop tick
   failed` — the message after the colon names the underlying cause
   (mongo auth, motor disconnect, etc.).
2. Confirm mongo is reachable from the api host (`/admin/health`
   `mongodb.status`). If mongo is the root cause, fix that first —
   the loop will resume on its own once the next tick succeeds.
3. If only one worker is stuck (its pid is in the alert body but
   sibling workers stay quiet), restart just that worker — the
   in-memory state will reload from the persisted doc on boot.
4. Tune the threshold from the Alert Settings page if a known
   maintenance window is going to exceed 60s of mongo unavailability,
   then revert it after — leaving it loose hides real regressions.

**Where it's tested**

- `tests/test_admin_assamese_purity.py::TestCrossWorkerPropagation`
  pins the budget constant and simulates two workers sharing one
  mongo to verify PATCH and DELETE both propagate.
- `tests/test_admin_assamese_purity.py::TestPersistedOverrideRoundTrip`
  covers the boot-time loader.

**If you change the interval**

Update `_ASM_REFRESH_INTERVAL_SECONDS` in
`routes/cms_sarvam_health.py`, update the budget number in this
runbook, and update the `<= 20` assertion in
`test_propagation_budget_constant_is_within_runbook_promise`.

---

## Nightly grounded-recall regression alert (Task #587)

**Alert type:** `grounded_recall_regression`
**Trigger:** `recall@5` from the nightly live bench drops more than the
configured gate vs `bench/fixtures/baseline.json`.

**What it means**

A live run of the grounded-answer pipeline (web search +
internal-chapter retrieval + citation builder) returned fewer of the
hand-labelled expected sources than the committed baseline. Students
are likely seeing weaker citations on at least the queries listed in
the alert body's "Misses" section.

**Triage**

1. Open the alert email — the body contains the per-metric current vs
   baseline diff and up to 10 miss IDs/queries. Pull the full report
   from the admin tile (it reads `bench/results/latest.json`).
2. Check `bench/results/latest.json` retriever — if it says `live`,
   the regression came from production retrievers; if `offline`, it
   came from the CI gate (code regression in
   `grounded_answer._build_citations`).
3. Re-run on demand: `python scripts/run_grounded_recall_nightly.py`
   exits 0 on pass, 2 on gate fail, 3 on runtime error. Use this to
   confirm the regression persists after a hot fix.
4. False positive after a deliberate fixture update? Regenerate the
   baseline with `python -m bench.grounded_recall --save-results
   --json` and copy the metrics block into
   `bench/fixtures/baseline.json`.

**Where it runs**

- In-process scheduler: `bench.grounded_recall._grounded_recall_nightly_loop`,
  wired into `server.py` lifespan. Polls every 5 min, fires once per
  UTC day inside a ±30 min window around the target hour. Cross-replica
  dedup via atomic CAS on `db.job_locks` (`_id =
  grounded_recall_nightly_marker`).
- External belt-and-suspenders: `.github/workflows/grounded-recall-nightly.yml`
  runs the offline bench on cron at 04:30 UTC so the citation builder
  is gated even when the backend is mid-deploy.

**Env vars (all optional)**

| Var | Default | Effect |
| --- | --- | --- |
| `GROUNDED_RECALL_NIGHTLY_ENABLED` | `true` | Set to `false` to disable the in-process loop entirely. |
| `GROUNDED_RECALL_NIGHTLY_HOUR_UTC` | `3` | Target hour (UTC) for the daily run. ±30 min window. |
| `GROUNDED_RECALL_NIGHTLY_GATE` | `0.05` | Max allowed `recall@5` drop vs baseline before paging. |

**Where it's tested**

- `tests/test_bench_grounded_recall_nightly.py` covers gate pass, gate
  fail (alert dispatched with metric delta + miss list), missing
  baseline (no alert spam on first deploy), scheduling window, and
  cross-replica dedup.

---

## Google sign-in via Supabase OAuth (setup checklist)

As of Task #156, Google sign-in is handled entirely by Supabase. The backend no
longer issues Google credentials or verifies Google ID tokens. All Google OAuth
flows go through Supabase and are exchanged at `/api/auth/supabase-session`.

### One-time Supabase dashboard configuration

1. Open your Supabase project → **Authentication → Providers → Google**.
2. Toggle **Enable Sign in with Google** to on.
3. Paste your **Google Cloud OAuth 2.0 Client ID** and **Client Secret**
   (from Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs).
4. Copy the **Redirect URL** shown by Supabase
   (format: `https://<project-ref>.supabase.co/auth/v1/callback`).
5. In Google Cloud Console, add that Redirect URL to
   **Authorised redirect URIs** on the same OAuth 2.0 client.
6. Save both.

### How the frontend flow works

1. User clicks **Sign in with Google** → `GoogleSignInButton` calls
   `supabase.auth.signInWithOAuth({ provider: 'google' })`.
2. Browser redirects to Google, user authenticates, Google redirects back to
   the Supabase callback URL.
3. Supabase sets its own session and redirects to the app's `redirectTo` URL
   (the current page — `/login` or `/signup`).
4. `AuthContext.onAuthStateChange` fires `SIGNED_IN` with `provider='google'`.
5. The handler calls `_exchangeSupabaseSession(session.access_token)` which
   hits `POST /api/auth/supabase-session` and sets the httpOnly cookie + JWT.
6. User is now fully authenticated with the correct role
   (`admin` / `staff` / `student` resolved in `supabase_session` handler).

### Role resolution (staff fix from Task #156)

The old `/auth/google` endpoint had a bug: it only checked `is_admin` and
defaulted to `student`, skipping the `staff` role entirely.
Now that Google sign-in goes through `/auth/supabase-session`, staff users
get `role="staff"` correctly (lines 262-266 of `routes/auth.py`).

### GA4 credentials (separate from sign-in)

`GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` in the environment are
**only** for the GA4 Data API client (`ga4_client.py`). They are not used for
Google sign-in. Set them separately from the Supabase provider credentials.

---

## Cloudflare wins program — feature flags (Task #383)

Five Cloudflare workstreams were activated under one task, each gated by an
independent flag so on-call can flip / roll back any one piece without
touching the others. All flags are read at module-import time from the
process environment; flip them in Replit Secrets (or your prod env: Railway,
Cloud Run, DO App Platform) and restart the app.

| Flag                   | Default | What it gates                                                                 |
|------------------------|---------|-------------------------------------------------------------------------------|
| `CF_AIGW_OBS_ON`       | `1`     | `ai_gateway_observability` parses `cf-aig-*` headers + tallies counters.      |
| `VECTORIZE_SHADOW_ON`  | `0`     | Pinecone (or whichever primary) writes/queries are mirrored into Vectorize.   |
| `R2_PRIMARY_ON`        | `0`     | Chapter PDFs / audio / exports / backups served from R2 first (else origin).  |
| `CF_EDGE_CACHE_ON`     | `0`     | `kv_cache.KvCache` mirrors hot reads into the CF KV namespace via the worker. |
| `TURNSTILE_ON`         | `0`     | Public POST routes wrapped in `Depends(require_turnstile)` enforce the token. |
| `CF_WEB_ANALYTICS_ON`  | `0`     | SSR shell renders the CF beacon snippet; `GA4_ENABLED` defaults to OFF.       |
| `CF_TUNNEL_ONLY_ON`    | `0`     | Origin advertises that only Cloudflare CIDRs are accepted (informational).    |
| `GA4_ENABLED`          | `1`*    | When `0`, every GA4 call returns `None` immediately — keeps token in DB.       |

\* `GA4_ENABLED` defaults to `not CF_WEB_ANALYTICS_ON` so flipping the CF
beacon on auto-disables GA4. Set `GA4_ENABLED=1` to keep both running in
parallel during the comparison window.

### Required secondary env vars

* `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` — Cloudflare → Turnstile.
* `CF_WEB_ANALYTICS_TOKEN` — Cloudflare → Analytics & Logs → Web Analytics →
  Site → JS snippet (the `data-cf-beacon` token).
* `CF_ANALYTICS_API_TOKEN` + `CF_WEB_ANALYTICS_SITE_TAG` — only needed if you
  want the `/admin/cf-health` panel to show "pageviews in the last hour"
  via the GraphQL Analytics API. Token needs `Account Analytics:Read`.
* `CF_TUNNEL_ALLOWED_IPS` — comma-separated CIDRs to allow when
  `CF_TUNNEL_ONLY_ON=1`. Defaults to the canonical Cloudflare IPv4 ranges
  from <https://www.cloudflare.com/ips/>.

### Unified health route

Hit `GET /api/admin/cf-health` (admin-gated) for one JSON aggregate of every
workstream's flag state, counters, and last-N samples:

```bash
curl -s "$ORIGIN/api/admin/cf-health" -H "Cookie: admin_session=…" | jq
```

Each block carries `{enabled, configured, ...}` so the admin UI can render
status pills regardless of which flags are on. A workstream that fails to
import or snapshot returns `{"error": "..."}` — the rest of the panel keeps
rendering.

### Rollback values

Set everything to its pre-task-383 state:

```env
CF_AIGW_OBS_ON=0
VECTORIZE_SHADOW_ON=0
R2_PRIMARY_ON=0
CF_EDGE_CACHE_ON=0
TURNSTILE_ON=0
CF_WEB_ANALYTICS_ON=0
CF_TUNNEL_ONLY_ON=0
GA4_ENABLED=1
```

### Cloudflare dashboard configuration that lives outside this repo

These pieces require the operator to log into Cloudflare — they are gated
behind the same flags but the activation step is a dashboard click, not a
code change:

1. **AI Gateway → Logs / Cache / Guardrails.** Enable in Cloudflare →
   AI → AI Gateway → `<gateway>` → Settings. Headers parsed by
   `ai_gateway_observability` only appear once Cache + Logs are on.
2. **Vectorize index.** Create in Cloudflare → Workers AI → Vectorize.
   Set `VECTORIZE_INDEX_NAME` to match `vectorize_client.VECTORIZE_INDEX`.
3. **R2 lifecycle / Cache Reserve.** Cache Reserve is a paid add-on
   enabled per-zone in Caching → Cache Reserve. R2 lifecycle rules
   (Standard → Infrequent Access at 30 days) live in R2 → Bucket → Settings.
4. **KV namespace.** Provisioned in Task #405 — run
   `wrangler kv:namespace create CF_EDGE_CACHE --env <production|staging>`
   once per env and paste the printed id into the `[[env.<env>.kv_namespaces]]`
   block in `workers/edge-proxy/wrangler.toml`. The worker exposes
   GET / PUT / DELETE `/api/edge/kv-cache/{key}` gated by the
   `X-Edge-Admin-Secret` header (matched against the worker's
   `D1_SYNC_SECRET`), so once the namespace id is filled in and
   `CF_EDGE_CACHE_ON=1` is set on the backend the
   `kv_writes` / `kv_reads` counters in `/admin/cf-health` start
   incrementing.
5. **Cloudflare Tunnel.** Install `cloudflared` next to the origin,
   `cloudflared tunnel create syrabit-origin`, then add the tunnel to the
   Zero Trust dashboard. Flip `CF_TUNNEL_ONLY_ON=1` once the origin
   firewall is restricted to the published CIDRs.

### Wiring map — where each flag actually changes runtime behaviour

The flags above don't do anything until they're consumed by a request
path. After Task #383's review remediation pass, the wiring is:

| Flag                  | Wired into                                            | Behavioural change |
|-----------------------|-------------------------------------------------------|--------------------|
| `CF_AIGW_OBS_ON`      | `providers/azure_openai.py` `call_chat` + `stream_chat` | Parses `cf-aig-*` headers from every gateway response into the `/admin/cf-health` counters. |
| `VECTORIZE_SHADOW_ON` | `retrievers/factory.py` via `vectorize_shadow.maybe_wrap_with_shadow`; ops endpoints `/admin/vectorize-shadow{,/reset}`; `scripts/vectorize_parity_nightly.py` | Default mirror rate is **1.0** (every query/upsert shadowed) for parity-grade recall@k. Override via `VECTORIZE_SHADOW_SAMPLE_RATE` if Vectorize bandwidth becomes the bottleneck. |
| `R2_PRIMARY_ON`       | `r2_storage.r2_primary_read_url(key, s3_fallback_url=...)` is the canonical read-URL emitter, called from both upload-success branches in `routes/admin_content.py::upload_content_image` (R2 + Supabase fallback) and `upload_content_file` (PDF). Toggle flips the URL the API hands back without a redeploy. `/admin/r2-storage-health` reports backend state. | When the flag is on **and** the same key exists in R2, every upload-success response (and any chapter PDF / image URL) is served via the R2 public URL; otherwise the helper returns the original Supabase URL. This means a key backfilled into R2 starts being served from R2 immediately on flag flip — no caller change needed. |
| `CF_EDGE_CACHE_ON`    | `routes/syllabus.py::get_syllabus` (await `cache.get`) and `routes/content.py::get_chapters` (await `cache.get` cross-pod hot path; falls back to Mongo on miss; mirrors result back into KV with a 5-min TTL). Admin chapter writes (`add_chapter`, `update_chapter`, `_cascade_delete_subject_assets`) call `routes.content.invalidate_chapters_kv(subject_id)` so renames/reorders are visible across pods immediately. `edu_allowlist.invalidate_cache()` covers the allowlist mirror on the admin write path. | Hot syllabus + chapter-index reads served from in-process LRU + KV mirror; cold/sibling pods served from KV mirror via the **async** path (so a Cloud Run cold start hydrates without touching Mongo). Admin writes purge both the LRU and the KV mirror, so staleness is bounded by the operator's intent, not the 5-min TTL. |
| `TURNSTILE_ON`        | `routes/auth.py` POST `/auth/signup` and `/auth/reset-request`; `routes/edu_browser.py` POST `/edu/request-site` and `/edu/educator/submit-site`; `routes/admin_review_prompts.py` POST `/analytics/review-prompt-event` | Public POSTs require a verified Turnstile token; 403 `turnstile_required` on miss. Dependency is dormant when the flag is off so the gate can ship before the secret is provisioned. |
| `CF_WEB_ANALYTICS_ON` | `artifacts/syrabit/index.html` runtime injector hits `/api/cf-web-analytics/config` | Frontend appends the CF beacon `<script>` when the origin reports the flag is on; rotating the token requires no SPA rebuild. |
| `CF_TUNNEL_ONLY_ON`   | `cf_tunnel_only.CfTunnelOnlyMiddleware` (registered in `server.py` after `MtlsClientCertMiddleware`) + `/admin/cf-health.tunnel` | When `CF_TUNNEL_ALLOWED_IPS` is non-empty, requests whose **immediate TCP peer** (`scope['client']`) falls outside the CIDR set are rejected 403 `cf_tunnel_only`. The middleware deliberately does **not** consult `cf-connecting-ip` / `x-forwarded-for` — those are user-controlled headers, so a direct caller could otherwise forge them and bypass the gate. Two valid deployment shapes: (1) `cloudflared` sidecar on the same host, in which case the peer is loopback (`CF_TUNNEL_ALLOWED_IPS=127.0.0.0/8,::1/128`); (2) Cloudflare edge → managed origin (Cloud Run / Railway), in which case the peer is a CF edge egress IP (`CF_TUNNEL_ALLOWED_IPS` ← public list at `https://www.cloudflare.com/ips/`). The shipped default covers **both** IPv4 and IPv6 CF prefixes so dual-stack origins (Cloud Run / Railway IPv6-by-default) don't 403 valid traffic on flag flip. Open paths: `/api/healthz`, `/api/readyz`, `/api/ready`, `/health`, `/api/admin/cf-health` (avoids chicken-and-egg lockout if the rule misfires). Empty CIDR list with the flag on defaults to **passthrough + warning** so an empty env var cannot black-hole the origin; set `CF_TUNNEL_FAIL_CLOSED_ON_EMPTY=1` to flip that to **reject-all** (open paths still pass) for environments that prioritise lockdown over availability during misconfiguration. |
| `GA4_ENABLED`         | `routes/admin_ga4.py` measurement-protocol gate     | Server-side GA4 events fire only when the flag is on. |

### AI Gateway guardrail blocks — log signal

`ai_gateway_observability.record_aig_response()` now emits a structured
`logger.warning("[ai-gateway] guardrail block …")` line on every block
(and `logger.info` on every rewrite) so on-call sees the event in the
logging pipeline immediately, instead of waiting for someone to refresh
`/admin/cf-health`. Fields included: `provider`, `model`, `category`,
`log_id`, `event_id` — all of which are CF-side identifiers that paste
directly into the AI Gateway log search UI.

### Nightly parity job

`scripts/vectorize_parity_nightly.py` runs the bench/grounded_recall
fixtures through `ShadowRetriever(..., shadow_sample_rate=1.0)` so the
admin panel surfaces a stable recall@10 number even when chat traffic
is light. Schedule under cron and alert on exit code != 0.

---

## Task #386 — Cloudflare Tier 2 (translator gate, SSR, Polish/Mirage,
Smart Tiered Cache, D1 mirror, Durable-Object chat)

Six new flags ship behind hard rollback values. Every flag defaults
to its previous behaviour so a rollback is always a single env-var
flip + worker restart — no redeploy of the edge worker or Pages
project required.

### Flag matrix + rollback values

| Flag | Default | Wired into | Rollback value |
|------|---------|-----------|----------------|
| `TRANSLATE_PROVIDER` | `auto` | `vertex_services.translate` (skips Google Translate when set to `workers_indic`); `llm.call_translate_with_dispatch` (pins pool to `workers_ai_indic`, no fallback). | `TRANSLATE_PROVIDER=auto` — restores the weighted Google + Workers + Azure pool. |
| `SSR_ENABLED` | `false` | `artifacts/syrabit/functions/_middleware.js` Pages middleware proxies `/<seo-route>` → backend `/html/<path>`. Counter snapshot lives in `cf_ssr_health.snapshot()`. | `SSR_ENABLED=0` (in **Pages env vars** — this is read by the Pages Function, not the backend container) restores SPA-shell-only delivery. The legacy bot-UA prerender path in `_worker.js` is unaffected. |
| `CF_SPEED_FEATURES_ON` | `false` | `cf_speed_smoke.apply_speed_features()` wraps `cf_enterprise.speed_optimize_all` (Polish + Mirage + Auto Minify + Brotli + Early Hints). `polish_smoke()` probes a known image and surfaces the `cf-polished` / `cf-bgj` headers in the cf-health row. | `CF_SPEED_FEATURES_ON=0` — the helper becomes a no-op. To **revert** zone settings already applied to CF, run `python -c "import asyncio,cf_enterprise as e; asyncio.run(e.speed_optimize_all_disable())"` (settings PATCH `value=off`). |
| `CF_TIERED_CACHE_ON` | `false` | `cf_tiered_cache.apply_tiered_cache()` (PUT `tiered_cache_smart_topology_enable`); `cf_tiered_cache.purge_by_cache_tags(tags)` is the canonical entry point for callers that need to invalidate by `Cache-Tag`. | `CF_TIERED_CACHE_ON=0` — `apply_tiered_cache` and `purge_by_cache_tags` short-circuit. To turn the zone setting itself off run `tiered_cache_disable()`. |
| `D1_MIRROR_ON` | `false` | `d1_mirror.export_extended_payload(db)` adds `seo_meta`, `audit_log`, `syllabus_map` to the existing D1 sync payload; `d1_mirror.sync_extended(db)` is invoked alongside `d1_sync.sync_full`. Lag exposed at `/admin/cf-health.d1_mirror`. | `D1_MIRROR_ON=0` — extended tables stop being included in the next sync. Already-mirrored rows stay in D1 (harmless — Pages Functions read them opportunistically and fall back to live origin data). |
| `DO_CHAT_ON` | `false` | `do_chat.{get_session,put_session,delete_session,rate_check}` dispatch to the edge proxy at `/do/chat-session/<id>` and `/do/rate-limiter/check`. The edge proxy routes them to `ChatSession` / `RateLimiter` Durable Objects (`workers/edge-proxy/src/{chat_session,rate_limiter}.ts`). The same edge worker also enforces **pre-origin** chat-ingress rate limits keyed per-verified-user when `EDGE_JWT_HS256_SECRET` is set, falling back to per-IP otherwise. When the flag is off **or** the edge call fails, the helpers transparently fall through to an in-process dict + token-bucket so chat state is never lost. | `DO_CHAT_ON=0` — every call serves from the in-process backend with no edge round-trip. Existing DO storage is untouched and ready to be re-enabled. |

### Required edge-side configuration (one-time, before `DO_CHAT_ON=1`)

1. `cd artifacts/syrabit/workers/edge-proxy && npx wrangler deploy` —
   the migration block `v1-task-386` creates the `ChatSession` +
   `RateLimiter` DO classes the first time it runs.
2. Backend env vars:
   - `DO_CHAT_BASE_URL` (or reuse `EDGE_WORKER_URL`) — the public
     URL of the edge proxy worker.
   - `DO_CHAT_SHARED_SECRET` (or reuse `DISPATCH_SHARED_SECRET`) —
     bearer auth for `/do/...` calls; must match the worker secret
     of the same name.
3. Verify with `curl -H "Authorization: Bearer $DISPATCH_SHARED_SECRET"
    -X POST -d '{"key":"smoke:1","limit":3,"window_s":60}'
    https://<edge>/do/rate-limiter/check` — expect
    `{"allowed":true,"remaining":2,...}`.
4. **Per-user edge rate limiting** (optional, recommended): set the
   wrangler secret `EDGE_JWT_HS256_SECRET` to the same value as the
   backend `JWT_SECRET`. The edge worker verifies the Bearer token
   with HS256/SubtleCrypto **before** keying the limiter; tokens that
   fail verification (forged signature, wrong secret, expired,
   malformed, RS256-claimed) degrade to per-IP scope. Without this
   secret the limiter stays per-IP — never per-client-supplied
   identity. Per-user budget defaults to `EDGE_CHAT_USER_RATE_LIMIT`
   (or 60 req/min); per-IP defaults to `EDGE_CHAT_RATE_LIMIT`
   (30 req/min). The 429 response advertises which scope blocked it
   via `X-Edge-Rate-Scope: do-chat-user|do-chat-ip`.

### Pages SSR rollback

Two env vars exist on purpose because Pages Functions and the backend
container live in separate runtimes that cannot read each other's
config:

| Env var | Where it's set | What it controls |
|---------|----------------|------------------|
| `SSR_ENABLED` | **Pages project** env vars (Settings → Environment variables) | Whether the Pages middleware actually serves SSR HTML. Authoritative for user-visible behaviour. |
| `PAGES_SSR_ENABLED` | **Backend container** env vars | Backend's *mirror* of the Pages flag, read only by `/admin/cf-health._ssr_snapshot` to detect drift between the two runtimes. Set to the same value as Pages `SSR_ENABLED` to keep `flag_drift=false`. |

Both must be flipped together for a clean rollout/rollback. To roll back:

1. Pages dashboard → Production env vars → set `SSR_ENABLED=0` →
   "Save and redeploy" (the env-var update triggers a tiny redeploy
   that takes < 30 s — no source change needed).
2. Confirm by `curl -I https://syrabit.ai/seba/class-10/general/science`
   — the response should **not** contain `X-SSR-Rendered:
   pages-functions`.

### Translation rollback verification

```
curl -H "Authorization: Bearer $ADMIN_BEARER" \
  https://api.syrabit.ai/admin/cf-health | jq '.translate_provider'
```

Expected after rollback (`TRANSLATE_PROVIDER=auto`):
```
{
  "providers": {
    "google_translate": { "success": N, "share": ~0.9 },
    "workers_indic":    { "success": M, "share": ~0.1 }
  },
  "primary_provider": "google_translate",
  "flag": "auto"
}
```

If `primary_provider` is still `workers_indic` after the flag flip,
the worker process is stale — restart the API workflow. The metric
counter is in-process and resets on restart, which is the intended
behaviour (no false historical numbers after a rollback).

### Per-workstream tests

| Workstream | Test file |
|------------|-----------|
| Translator gate | `tests/test_translate_provider_gate.py` |
| Speed features + smoke | `tests/test_cf_tier2_helpers.py` (`test_speed_*`, `test_polish_smoke_*`) |
| Tiered cache + purge | `tests/test_cf_tier2_helpers.py` (`test_tiered_cache_*`) |
| D1 mirror + lag | `tests/test_cf_tier2_helpers.py` (`test_d1_mirror_*`) |
| Durable-Object chat fallback | `tests/test_cf_tier2_helpers.py` (`test_do_chat_*`) |
| /admin/cf-health rows | `tests/test_admin_cf_health_route.py::test_task_386_rows_have_expected_shape` |

Run all Task #386 tests in one shot:
```
pytest tests/test_translate_provider_gate.py \
       tests/test_cf_tier2_helpers.py \
       tests/test_admin_cf_health_route.py
```

