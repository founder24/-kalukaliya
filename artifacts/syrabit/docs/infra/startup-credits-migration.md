# Startup Credits Migration Runbook

**Task #263** — Replace paid Cloudflare add-ons (~$40–50/mo) with workloads
covered by existing startup credit programmes so the net Cloudflare bill drops
to $0 add-on spend while the Enterprise zone (WAF, Turnstile, mTLS, Zero Trust,
Pages) stays intact.

---

## Credit Inventory

| Programme | Provider | Reference Grant | Confirmed Active | Approx Expiry |
|-----------|----------|----------------|-----------------|---------------|
| Google Cloud for Startups | GCP (vertex, Cloud Run, Cloud Storage, Cloud CDN, Cloud Logging) | $2,000 | ✅ Yes | TBD — update when billing console shows |
| AWS Activate | AWS (Bedrock, Lambda, Route 53) | $1,000 | ✅ Yes | TBD |
| Azure for Startups | Azure OpenAI | $2,500 | ✅ Yes (weight=1 fallback) | TBD |
| Sarvam Startup Credits | Sarvam LLM | $500 | ✅ Yes | TBD |
| Cartesia Startup Credits | Cartesia TTS | $500 | ✅ Yes | TBD |
| ElevenLabs Startup Credits | ElevenLabs TTS | $500 | ✅ Yes | TBD |
| AssemblyAI Startup Credits | AssemblyAI STT | $1,000 | ✅ Yes | TBD |
| Cohere Startup Credits | Cohere embed/rerank | $1,000 | ✅ Yes | TBD |
| Pinecone Startup Credits | Pinecone vector search | $500 | ✅ Yes | TBD |
| Exa Startup Credits | Exa neural search | $1,000 | ✅ Yes | TBD |
| Tavily Startup Credits | Tavily live search | $500 | ✅ Yes | TBD |
| Cloudflare Enterprise | CF WAF, Turnstile, mTLS, Zero Trust, Pages, D1, Vectorize | Enterprise | ✅ Covered | TBD |

**Action:** Update the "Approx Expiry" column quarterly. Credit low-water
warnings fire automatically via `/admin/credits/summary` (20% threshold).

---

## Add-on Migration Table

Track each paid CF subscription here. Update `status` when each migration completes.

| Add-on | Monthly Cost | Migration Target | Credit Programme | Status | Notes |
|--------|-------------|-----------------|-----------------|--------|-------|
| Workers for Platforms | ~$25 | GCP Cloud Run (asia-south1, already in use) | Google Cloud for Startups | 🟡 Pending | Dispatch logic in edge-proxy worker. Move tenant dispatch to a Cloud Run service; update the edge worker to call the Cloud Run URL. Cancel subscription at dash.cloudflare.com → Workers & Pages → Plans. |
| Argo Smart Routing | ~$5 | GCP Premium Tier network routing (no action needed — GCP is already Premium Tier) | Google Cloud for Startups | 🟡 Pending | Disable Argo at dash.cloudflare.com → Speed → Optimization → Argo. Confirm latency metrics in Cloud Monitoring within 48h. |
| Workers Paid | ~$5 | GCP Cloud Run (email-worker, bedrock-proxy logic) — free-tier Workers handles remaining lightweight workers | Google Cloud for Startups / AWS Activate | 🟡 Pending | Verify that remaining Worker request volume stays <100k/day (free tier). Move compute-heavy workers to Cloud Run. Cancel Workers Paid via dash.cloudflare.com → Workers & Pages → Plans. |
| Basic Load Balancing | ~$5 | GCP Global HTTPS Load Balancer (fronting Cloud Run, already active) + Route 53 health-check failover (AWS Activate) | Google Cloud for Startups / AWS Activate | 🟡 Pending | Wire Route 53 health-check record pointing at Railway + Cloud Run origins. Cancel at dash.cloudflare.com → Traffic → Load Balancing. |
| Cache Reserve | ~$5 | GCP Cloud CDN (attached to the existing Cloud Run Load Balancer) | Google Cloud for Startups | 🟡 Pending | Enable Cloud CDN on the Cloud Run backend service in GCP Console. Set Cache-Control headers on API responses to match current CF edge TTLs. Cancel at dash.cloudflare.com → Caching → Cache Reserve. Update nightly-smoke.js Cache Reserve check to warn-only after cancellation. |
| R2 Paid (syrabit-media) | ~$5 | GCP Cloud Storage (asia-south1, already has GCP) as primary; R2 as CDN origin only if zero-cost | Google Cloud for Startups | 🟡 Pending | Create `syrabit-media` bucket in Cloud Storage asia-south1. Update backend upload path and CDN signed-URL generation to use GCP Storage SDK. Re-point the Cloudflare Pages `_headers` or Worker rules to the new GCS CDN URL. Cancel R2 Paid if monthly bill > $0. |

**Total projected monthly savings: ~$50/mo**

---

## Migration Checklist

### Step 1 — Argo Smart Routing (quickest win, $5/mo)

- [ ] Confirm Cloud Run asia-south1 is on GCP Premium Tier (check Cloud Console → Network Service Tiers — Premium should be default).
- [ ] Disable Argo Smart Routing at **Cloudflare dash → Speed → Optimization → Argo**.
- [ ] Monitor `p95` and `p99` latency in Cloud Monitoring for 48h. If latency rises > 20ms, re-enable and investigate before proceeding.
- [ ] Update the "Status" row in this table to ✅ Complete + date.

### Step 2 — Cache Reserve ($5/mo)

- [ ] Enable Cloud CDN on the Cloud Run backend service in **GCP Console → Network Services → Cloud CDN → Add Origin → Backend Service**.
- [ ] Set `Cache-Control: public, max-age=86400` (or higher) on `/api/seo/*`, `/static/*`, and `/assets/*` responses in `server.py`.
- [ ] Validate cache HIT rate in Cloud Monitoring (Cache Hit Ratio metric) for 24h.
- [ ] Cancel Cache Reserve at **Cloudflare dash → Caching → Cache Reserve → Cancel subscription**.
- [ ] Update `nightly-smoke.js` Cache Reserve assertion to `warn()` instead of `failures.push()` (it already degrades to a warning when the API returns 1135 plan-restriction; confirm the check is non-blocking after cancellation).
- [ ] Update the "Status" row in this table to ✅ Complete + date.

### Step 3 — R2 Paid ($5/mo)

- [ ] Create GCS bucket `syrabit-media` in asia-south1 (multi-region) with uniform bucket-level access.
- [ ] Update `artifacts/syrabit-backend/` upload routes to use the GCP Storage client library (already imported for Vertex/GCS elsewhere) for new uploads.
- [ ] Run a one-time sync: `gsutil -m rsync -r gs://syrabit-r2-media gs://syrabit-media` (or equivalent rclone command).
- [ ] Re-point CDN signed-URL generation to GCS.
- [ ] Monitor R2 bill for one billing cycle; cancel R2 Paid at **Cloudflare dash → R2 → Manage → Cancel** if bill reaches $0.
- [ ] Update the "Status" row in this table to ✅ Complete + date.

### Step 4 — Basic Load Balancing ($5/mo)

- [ ] Add a Route 53 health-check failover record for `api.syrabit.ai` pointing to the Railway origin (primary) and the Cloud Run origin (secondary). Use Route 53 health checks on `/health`.
- [ ] Confirm failover works by taking Railway down in staging (or by lowering the threshold).
- [ ] Cancel Cloudflare Load Balancing at **Cloudflare dash → Traffic → Load Balancing → Delete pool**.
- [ ] Update `cf_enterprise.py` Load Balancing section to note the migration.
- [ ] Update the "Status" row in this table to ✅ Complete + date.

### Step 5 — Workers Paid + Workers for Platforms ($30/mo)

These are the largest items and require a code migration.

- [ ] Audit actual Workers request counts in **Cloudflare dash → Workers & Pages → {worker name} → Analytics**. If all workers total < 100k requests/day, Workers Paid is not needed.
- [ ] Extract `email-worker` and `bedrock-proxy` logic into a GCP Cloud Run service (or AWS Lambda if AWS Activate balance is larger). Update the edge-proxy worker to call the new endpoint.
- [ ] Migrate Workers for Platforms dispatch logic to the same Cloud Run service. Update `artifacts/syrabit/workers/edge-proxy/src/index.ts` (if present) to call `CLOUD_RUN_DISPATCH_URL` env var instead of the Workers for Platforms binding.
- [ ] Deploy, smoke-test, and confirm all routes still pass the nightly-smoke suite.
- [ ] Cancel **Workers Paid** and **Workers for Platforms** subscriptions at **Cloudflare dash → Workers & Pages → Plans**.
- [ ] Update the "Status" row in this table to ✅ Complete + date.

---

## Smoke-Test Validation

After each step, run the nightly smoke to confirm no regressions:

```bash
CLOUDFLARE_API_TOKEN=<token> \
CLOUDFLARE_ZONE_ID=5b8c97df4431491dc7f60ea72fb61871 \
CLOUDFLARE_ACCOUNT_ID=d66e40eac539fff1db270fddf384a5ec \
node artifacts/syrabit/scripts/nightly-smoke.js
```

The Cache Reserve check (`nightly-smoke.js` Phase 4) already degrades to a
`warn()` on plan-restriction errors (CF API code 1135), so cancelling Cache
Reserve will not cause the smoke run to exit 1.

---

## Admin Panel

The migration status is visible at **Admin → Health → CF Add-on Migration** panel
(added by Task #263). Each row shows:
- Service name and monthly cost saved
- Migration target and credit programme covering it
- Status badge: 🟡 Pending / 🔵 In Progress / ✅ Complete

Endpoint: `GET /admin/credits/cf-addons` (admin-gated).

---

## Rollback

Each step is independently reversible:
- **Argo**: Re-enable at Cloudflare dash → Speed → Optimization → Argo.
- **Cache Reserve**: Re-subscribe at Cloudflare dash → Caching → Cache Reserve.
- **R2**: Re-point upload routes to R2 SDK; R2 data is not deleted during migration.
- **Load Balancing**: Re-create the Cloudflare LB pool using `cloudflare-lb-apply.js`.
- **Workers for Platforms / Workers Paid**: Re-subscribe and re-deploy workers.

---

*Last updated: 2026-05-02 — Task #263*
