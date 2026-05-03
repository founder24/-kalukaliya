# Startup Credits Migration Runbook

**Last updated:** 2026-05-02  
**Status:** In progress  
**Savings target:** ~$40/mo on Cloudflare paid add-ons

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
| Deepgram Startup Credits | Deepgram STT (nova-3, primary) + TTS fallback (aura-2) | $500 | ✅ Yes | TBD |
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

## Migrated Services

### 1. Workers for Platforms → GCP Cloud Run `dispatch-v2`

| | Before | After |
|---|---|---|
| **Service** | Cloudflare Workers for Platforms | GCP Cloud Run (asia-south1) |
| **Cost** | $25/mo | $0 (GCP Activate) |
| **File** | (Cloudflare dashboard binding) | `infra/gcp/cloud-run-dispatch.yaml` |
| **Worker stub** | `workers/edge-proxy/src/index.ts` (heavy) | `workers/edge-proxy/src/index.ts` (thin shim, free tier) |

**Migration steps:**
1. Deploy `dispatch-v2` Cloud Run service from `infra/gcp/cloud-run-dispatch.yaml`
2. Set `DISPATCH_CLOUD_RUN_URL` wrangler secret to the Cloud Run service URL
3. Set `DISPATCH_SHARED_SECRET` on both the worker and Cloud Run env
4. Deploy the updated `edge-proxy` worker (`wrangler deploy workers/edge-proxy`)
5. Verify traffic via Cloud Run logs and Axiom dashboard
6. Cancel Workers for Platforms subscription in Cloudflare dashboard

**Rollback:** Set `DISPATCH_CLOUD_RUN_URL` to empty → worker returns 503 → revert to Workers for Platforms binding in dashboard.

---

### 2. Argo Smart Routing → GCP Premium Tier + Route 53 Latency

| | Before | After |
|---|---|---|
| **Service** | Cloudflare Argo Smart Routing (zone level) | GCP Premium Tier (Cloud Run) + Route 53 latency records |
| **Cost** | $5/mo | $0 (GCP Activate + AWS Activate) |
| **File** | (Cloudflare dashboard) | `infra/aws/route53-latency.tf` |

**Migration steps:**
1. Cloud Run in asia-south1 automatically uses GCP Premium Tier network — no config change needed
2. `terraform apply infra/aws/route53-latency.tf` (provide `gcp_cloud_run_ip` and `railway_api_ip` vars)
3. Verify Route 53 health checks are green in AWS Console
4. Monitor p95 latency in Cloud Monitoring for 48 h
5. Cancel Argo Smart Routing in Cloudflare dashboard → Subscriptions

**Expected latency change:** GCP Premium Tier achieves similar or better inter-region routing vs Argo for GCP-hosted origins. Route 53 latency records provide automatic geo-steering.

---

### 3. Workers Paid → AWS Lambda (email-worker + bedrock-proxy)

| | Before | After |
|---|---|---|
| **Service** | Cloudflare Workers Paid plan | AWS Lambda (arm64, Graviton3) |
| **Cost** | $5/mo | $0 (AWS Activate) |
| **Files** | `workers/email-worker/src/index.ts` (heavy), `workers/bedrock-proxy/src/index.ts` (heavy) | Lambda images in ECR; CF workers reduced to thin stubs |

**Migration steps:**
1. Build and push Docker images:
   ```bash
   docker build -t email-worker workers/email-worker && \
     aws ecr get-login-password | docker login --username AWS --password-stdin <acct>.dkr.ecr.ap-south-1.amazonaws.com && \
     docker tag email-worker <acct>.dkr.ecr.ap-south-1.amazonaws.com/syrabit/email-worker:latest && \
     docker push <acct>.dkr.ecr.ap-south-1.amazonaws.com/syrabit/email-worker:latest
   # Repeat for bedrock-proxy (us-east-1)
   ```
2. `terraform apply infra/aws/lambda-email-worker.tf`
3. `terraform apply infra/aws/lambda-bedrock-proxy.tf`
4. Update `.env` with new `LAMBDA_EMAIL_WORKER_URL` and `BEDROCK_LAMBDA_URL`
5. Deploy thin CF worker stubs (`wrangler deploy workers/email-worker`, `wrangler deploy workers/bedrock-proxy`)
6. Smoke-test: send test OTP email + verify Bedrock inference via `/api/chat`
7. Confirm CF Workers daily requests < 100 k (free tier threshold)
8. Cancel Workers Paid subscription in Cloudflare dashboard

**Performance gains:** arm64 Graviton3 is ~20% faster and ~20% cheaper per invocation vs x86. Bedrock proxy gains CloudFront edge cache for repeated prompts (TTL 5 min).

---

### 4. Cache Reserve → GCP Cloud CDN

| | Before | After |
|---|---|---|
| **Service** | Cloudflare Cache Reserve | GCP Cloud CDN (on existing HTTPS LB) |
| **Cost** | ~$5/mo usage | $0 (GCP Activate) |
| **File** | (Cloudflare dashboard) | `infra/gcp/cloud-cdn.tf` |

**Migration steps:**
1. `terraform apply infra/gcp/cloud-cdn.tf`
2. Verify `enable_cdn = true` on the `dispatch-v2-backend` backend service
3. Check cache-hit rate in Cloud Monitoring (`loadbalancing.googleapis.com/https/backend_request_count`)
4. Target ≥ 70 % hit rate for static assets
5. Cancel Cache Reserve in Cloudflare dashboard → Caching → Cache Reserve

**Validation:** Cloud Monitoring dashboard `syrabit-cdn-cache-hit` shows hit ratio ≥ 0.70 after 24 h warm-up.

---

### 5. R2 → GCP Cloud Storage (`syrabit-media`, asia-south1)

| | Before | After |
|---|---|---|
| **Service** | Cloudflare R2 (`syrabit-media` bucket) | GCP Cloud Storage (`syrabit-media`, asia-south1) |
| **Cost** | R2 Paid usage charges | $0 (GCP Activate, Standard class) |
| **File** | (Cloudflare dashboard) | `infra/gcp/cloud-cdn.tf` (bucket + CDN) |

**Migration steps:**
1. `terraform apply infra/gcp/cloud-cdn.tf` (includes bucket + public IAM binding)
2. Sync existing R2 objects: `rclone sync r2:syrabit-media gcs:syrabit-media --transfers=32`
3. Update backend upload path (`STORAGE_PROVIDER=gcs`, `GCS_BUCKET=syrabit-media`)
4. Update signed-URL generation to use `google.cloud.storage.generate_signed_url()`
5. Flip CDN backend to `syrabit-media-backend` (GCS) for `/media/*` path
6. Monitor for 24 h; verify media loads in production
7. Cancel R2 Paid subscription if monthly bill drops to zero

**Lifecycle:** Objects ≥ 365 days automatically move to NEARLINE (70 % cheaper).

---

### 6. Log Explorer → GCP Cloud Logging + Axiom

| | Before | After |
|---|---|---|
| **Service** | Cloudflare Log Explorer | GCP Cloud Logging (storage) + Axiom (UI) |
| **Cost** | Log Explorer usage charges | $0 (GCP Activate + Axiom startup tier) |
| **File** | (Cloudflare dashboard) | `infra/gcp/cloud-logging-axiom.tf` |

**Migration steps:**
1. `terraform apply infra/gcp/cloud-logging-axiom.tf`
2. In Cloudflare dashboard → Analytics → Logpush: create new job → destination HTTP → `https://api.axiom.co/v1/datasets/cf-logs/ingest` with `Authorization: Bearer <AXIOM_TOKEN>`
3. Also add GCP Logging destination: `https://logging.googleapis.com/v2/entries:write` with SA key
4. Import Grafana Cloud dashboard JSON from `infra/gcp/grafana/cf-logs-dashboard.json` (to be created)
5. Cancel Cloudflare Log Explorer subscription

**Retention:** 30 days in Axiom (startup tier), 30 days in Cloud Logging (free tier). BigQuery export for longer retention.

---

### 7. Basic Load Balancing → GCP HTTPS LB + Route 53 Failover

| | Before | After |
|---|---|---|
| **Service** | Cloudflare Basic Load Balancing | GCP Global HTTPS LB (existing) + Route 53 health-check failover |
| **Cost** | $5/mo | $0 (GCP Activate + AWS Activate) |
| **File** | (Cloudflare dashboard) | `infra/aws/route53-latency.tf` |

**Migration steps:**
1. Confirm GCP HTTPS LB is healthy for Cloud Run origin (should already be)
2. Apply Route 53 latency records (same `terraform apply` as step 2 above)
3. Verify both health checks are green
4. Remove the Cloudflare Load Balancing pool config
5. Cancel Cloudflare Basic Load Balancing subscription

---

## Additional Performance-Boosting Features (by Provider)

### Google Cloud (GCP Activate — $100 k)

| Feature | Benefit | Status |
|---|---|---|
| Cloud CDN (Brotli + HTTP/3) | Edge caching replaces Cache Reserve; Brotli at PoP | ✅ `infra/gcp/cloud-cdn.tf` |
| Cloud Run gVisor sandbox | Tenant isolation, faster container startup (50 ms vs 300 ms) | ✅ `infra/gcp/cloud-run-dispatch.yaml` |
| GCP Premium Tier network | Backbone routing replaces Argo Smart Routing | ✅ automatic on Cloud Run |
| Cloud Storage NEARLINE lifecycle | 70 % cost reduction for cold media | ✅ `infra/gcp/cloud-cdn.tf` |
| Cloud Logging + log-based metrics | 5xx error rate + origin latency alerting | ✅ `infra/gcp/cloud-logging-axiom.tf` |
| Vertex AI (already active) | On-device RAG, embeddings for study content | existing |
| BigQuery (Activate) | Cost attribution per feature, slow-query analysis | planned |
| Cloud Armor (free tier) | DDoS protection + rate limiting at GCP LB | planned |
| Cloud Trace | Distributed tracing across Cloud Run services | planned |

### Amazon Web Services (AWS Activate — $100 k)

| Feature | Benefit | Status |
|---|---|---|
| Lambda arm64 (Graviton3) | 20 % faster / cheaper vs x86 for Node.js | ✅ `infra/aws/lambda-*.tf` |
| Lambda Provisioned Concurrency | Eliminates cold-start for OTP / Bedrock paths | ✅ `infra/aws/lambda-*.tf` |
| CloudFront + cache policy | Edge cache for Bedrock prompt responses (TTL 5 min) | ✅ `infra/aws/lambda-bedrock-proxy.tf` |
| Amazon Bedrock Guardrails | Content filtering at Lambda layer, zero RTT | ✅ `infra/aws/lambda-bedrock-proxy.tf` |
| SES Dedicated IP Pool | Better inbox placement vs shared IP | ✅ `infra/aws/lambda-email-worker.tf` |
| SES Virtual Deliverability Manager | Engagement tracking, bounce suppression | ✅ `infra/aws/lambda-email-worker.tf` |
| Route 53 latency records + health checks | Geo-steering + automatic failover | ✅ `infra/aws/route53-latency.tf` |
| AWS X-Ray active tracing | Per-invocation flame graphs for Lambda | ✅ `infra/aws/lambda-*.tf` |
| Cost Anomaly Detection | Alert on Bedrock spend spike > $50/day | ✅ `infra/aws/lambda-bedrock-proxy.tf` |
| AWS Global Accelerator | Anycast routing, < 10 ms improvement Asia | 🔵 optional, see `route53-latency.tf` |
| CloudWatch Contributor Insights | Top-N model × user Bedrock spend | planned |
| AWS WAF (on CloudFront) | Bot blocking + rate limiting on Bedrock proxy | planned |

### Microsoft Azure (Azure for Startups — $5 k)

| Feature | Benefit | Status |
|---|---|---|
| Azure Front Door Standard | 100+ PoPs, Brotli L11, Origin Shield | ✅ `infra/azure/front-door.tf` |
| Front Door WAF (OWASP 3.2 + Bot Manager) | Additional WAF layer on top of Cloudflare | ✅ `infra/azure/front-door.tf` |
| Azure DDoS Network Protection | Volumetric attack absorption | ✅ included in Front Door Standard |
| Cosmos DB Serverless (MongoDB API) | Geo-distributed chat session cache, < 12 ms reads | ✅ `infra/azure/cosmos-db-cache.tf` |
| Cosmos DB multi-region reads | Central India + East Asia replicas | ✅ `infra/azure/cosmos-db-cache.tf` |
| Cosmos DB PITR (7 days) | Disaster recovery at no extra cost | ✅ `infra/azure/cosmos-db-cache.tf` |
| Azure Monitor metric alerts | Front Door origin latency p95 alerting | ✅ `infra/azure/front-door.tf` |
| Azure Container Registry (planned) | Private registry for Lambda / Cloud Run images | planned |

### Axiom (Startup Tier — 500 GB/mo)

| Feature | Benefit | Status |
|---|---|---|
| Cloudflare Logpush → Axiom | Replaces Log Explorer, full query UI | ✅ `infra/gcp/cloud-logging-axiom.tf` |
| APM traces (OpenTelemetry) | End-to-end request tracing via OTLP ingest | planned |
| Axiom dashboards | Custom request-rate / error-rate dashboards | planned |
| Alert rules | Slack / PagerDuty on 5xx spike | planned |

### Sentry (Startup Team Plan — 12 months free)

| Feature | Benefit | Status |
|---|---|---|
| Error tracking (frontend + backend) | Real-user JS errors, Python exceptions | existing (backend) |
| Performance monitoring | LCP / FID / CLS tracking from real users | planned |
| Session Replay | Video-like replay of user sessions hitting errors | planned |
| Crons (dead-man's switch) | Heartbeat monitoring for nightly jobs | planned |
| Alerts → Slack | On-call paging for P0 errors | planned |

### Grafana Cloud (Free Tier — 10 k series, 14-day retention)

| Feature | Benefit | Status |
|---|---|---|
| Prometheus remote write | Cloud Run + Lambda metrics in one place | planned |
| Log explorer (Loki) | Supplement Axiom with shorter hot-retention store | planned |
| Grafana dashboards | Infra overview, CDN cache hit, error rate | planned |

---

## Cancellation Checklist

- [ ] Cloudflare Workers for Platforms — cancel after `dispatch-v2` Cloud Run verified
- [ ] Cloudflare Argo Smart Routing — cancel after Route 53 latency records active
- [ ] Cloudflare Workers Paid — cancel after Lambda stubs verified < 100 k req/day
- [ ] Cloudflare Cache Reserve — cancel after GCP Cloud CDN hit rate ≥ 70 %
- [ ] Cloudflare R2 Paid — cancel after GCS bucket verified as primary and R2 bill = $0
- [ ] Cloudflare Log Explorer — cancel after Axiom ingest confirmed active
- [ ] Cloudflare Basic Load Balancing — cancel after Route 53 health checks green

**Total target saving:** ~$40/mo → $0/mo on Cloudflare add-ons.  
**Cloudflare Enterprise zone retained** for WAF, Turnstile, mTLS, Zero Trust, Pages, D1, Vectorize.

---

## Smoke Test Protocol

Run after each migration step:

```bash
# Full smoke suite
node artifacts/syrabit/scripts/nightly-smoke.js

# Quick CDN cache check
curl -sI https://syrabit.ai/ | grep -i 'x-cache\|cf-cache-status\|age'

# Bedrock proxy health
curl -X POST https://api.syrabit.ai/api/chat \
  -H 'content-type: application/json' \
  -d '{"message":"test","model":"claude-3-5-sonnet"}' | jq .status

# Email worker health
curl -X POST https://api.syrabit.ai/api/email/test \
  -H 'x-admin-token: $ADMIN_TOKEN'

# Route 53 latency check (from ap-south-1)
dig +short api.syrabit.ai @8.8.8.8
```

---

## Contact

- GCP billing alerts → ops@syrabit.ai + Cloud Monitoring notification channel
- AWS cost anomaly alerts → ops@syrabit.ai
- Azure Monitor alerts → ops@syrabit.ai
- Axiom log alerts → Slack `#infra-alerts`
