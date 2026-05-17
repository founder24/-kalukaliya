# Cloudflare Audit Fix Implementation Plan

## Executive Summary

This document provides a step-by-step implementation plan to resolve the 5 critical failures identified in the Cloudflare Weekly Audit (Issue #8).

**Audit Date:** Sat, 16 May 2026  
**Failed Items:** 5  
**Warnings:** 10 (token scope gaps - no action required)  
**Passed:** 3  
**Skipped:** 1  

---

## Prerequisites

### Required Environment Variables

These must be set before running any fix scripts:

```bash
# Required for all phases
export CLOUDFLARE_API_TOKEN="<your-api-token>"
export CLOUDFLARE_ZONE_ID="5b8c97df4431491dc7f60ea72fb61871"
export CLOUDFLARE_ACCOUNT_ID="d66e40eac539fff1db270fddf384a5ec"

# Required for Phase 6 (Zaraz + GA4)
export GA4_MEASUREMENT_ID="G-XXXXXXXXXX"
```

### Required API Token Scopes

The `CLOUDFLARE_API_TOKEN` must have the following permissions:

| Scope | Required For |
|-------|-------------|
| Zone Settings: Edit | Phase 1 |
| R2: Edit | Phase 4 |
| Cache: Edit | Phase 4 |
| Workers: Edit | Phase 5 (wrangler deploy) |
| SSL and Certificates: Edit | Phase 6 |
| Zaraz: Edit | Phase 6 |
| Speed (Observatory): Edit | Phase 6 |

**Configure token at:** https://dash.cloudflare.com/profile/api-tokens

---

## Layer 1: Zone Settings Hardening (Audit Item #1)

### Problem
Zone settings show multiple configuration errors and scope gaps:
- sort_query_string_for_cache: [scope gap]
- true_client_ip_header: [scope gap]
- ech: error
- http3: error
- brotli: [scope gap]
- http2: error
- always_use_https: [scope gap]
- min_tls_version: error
- tls_1_3: error
- automatic_https_rewrites: [scope gap]
- ssl: error

### Solution Script
Location: `/workspace/artifacts/syrabit/scripts/cloudflare-phase1-apply.js`

### Execution Steps

```bash
# Step 1.1: Verify environment
echo "Zone ID: $CLOUDFLARE_ZONE_ID"
echo "Token present: ${CLOUDFLARE_API_TOKEN:+YES}"

# Step 1.2: Run Phase 1 apply script
cd /workspace
node artifacts/syrabit/scripts/cloudflare-phase1-apply.js
```

### Expected Output
```
✓ sort_query_string_for_cache: on
✓ true_client_ip_header: on
✓ ech: on
✓ http3: on
✓ brotli: on
✓ http2: on
✓ always_use_https: on
✓ min_tls_version: 1.2
✓ tls_1_3: zrt
✓ automatic_https_rewrites: on
✓ ssl: strict
```

### Manual Alternative (Dashboard)
If script fails due to token scope issues:
1. Go to https://dash.cloudflare.com
2. Select syrabit.ai zone
3. Navigate to: Edge Certificates, Speed, and Security sections
4. Manually enable each setting listed above

---

## Layer 2: R2 Storage Infrastructure (Audit Items #11, #13)

### Problems
- **Item #11:** R2 bucket `syrabit-cache-reserve` NOT FOUND
- **Item #13:** Custom domain `assets.syrabit.ai` NOT FOUND

### Solution Script
Location: `/workspace/artifacts/syrabit/scripts/cloudflare-phase4-apply.js`

### Execution Steps

```bash
# Step 2.1: Ensure token has R2: Edit and Cache: Edit scopes

# Step 2.2: Run Phase 4 apply script
cd /workspace
node artifacts/syrabit/scripts/cloudflare-phase4-apply.js
```

### What This Script Does

**Step 1:** Creates R2 bucket `syrabit-assets` (if not exists)
- Location: Auto-selected (closest to account region)
- Purpose: Student PDFs, syllabi, past papers storage

**Step 2:** Configures custom domain `assets.syrabit.ai`
- Points to `syrabit-assets` bucket
- Enables public access via CNAME

**Step 3:** Creates R2 bucket `syrabit-cache-reserve`
- Purpose: Cache Reserve backing store for zone

**Step 4:** Enables Cache Reserve on zone
- Links zone to `syrabit-cache-reserve` bucket
- Improves cache hit ratio for edge content

### Expected Output
```
Step 1 — R2 bucket: syrabit-assets
  ✓  syrabit-assets created

Step 2 — Custom domain: assets.syrabit.ai → syrabit-assets
  ✓  assets.syrabit.ai configured

Step 3 — R2 bucket: syrabit-cache-reserve
  ✓  syrabit-cache-reserve created

Step 4 — Cache Reserve: enabled on syrabit.ai zone
  ✓  Cache Reserve enabled
```

### DNS Prerequisite for Custom Domain
Before running Step 2, ensure DNS record exists:
```
assets.syrabit.ai  CNAME  <bucket>.r2.cloudflarestorage.com
```
Cloudflare may auto-provision this if the domain is already managed in the zone.

---

## Layer 3: Worker Deployment (Audit Item #14)

### Problem
Analytics Engine binding (ANALYTICS) fails with:
```
[{"code":10007,"message":"This Worker does not exist on your account."}]
```

### Root Cause
The `syrabitworker` Worker has not been deployed to Cloudflare Workers.

### Solution: Deploy Worker with Wrangler

#### Step 3.1: Navigate to Worker Directory
```bash
cd /workspace/workers/edge-proxy
```

#### Step 3.2: Verify wrangler.toml Configuration
The worker configuration already includes:
```toml
name = "syrabitworker"
main = "src/index.ts"
account_id = "d66e40eac539fff1db270fddf384a5ec"

[[analytics_engine_datasets]]
binding = "ANALYTICS"
dataset = "syrabit-edge-metrics"
```

#### Step 3.3: Install Dependencies (if needed)
```bash
pnpm install
```

#### Step 3.4: Deploy Worker
```bash
npx wrangler deploy
```

#### Step 3.5: Set CF_ANALYTICS_TOKEN Secret (Optional but Recommended)
```bash
npx wrangler secret put CF_ANALYTICS_TOKEN
```
This token needs Analytics Read scope for querying the dataset.

#### Step 3.6: Verify Deployment
```bash
npx wrangler tail --name syrabitworker
```

### Expected Output
```
🚧 Building list of assets...
🚧 Building asset...
🚧 Uploading...
✨ Success! Deployed syrabitworker
```

### Alternative: CI/CD Deployment
The worker can also be deployed via GitHub Actions workflow:
1. Push changes to main branch
2. Workflow automatically triggers deployment
3. Check GitHub Actions tab for status

---

## Layer 4: Zaraz Analytics + Observatory (Audit Item #19)

### Problem
Zaraz GA4 + Observatory configuration fails with:
```
Zaraz: [{"code":7003,"message":"Could not route to /zones/.../zaraz/config, perhaps your object identifier is invalid?"},{"code":7000,"message":"No route for that URI"}]
```

### Solution Script
Location: `/workspace/artifacts/syrabit/scripts/cloudflare-phase6-apply.js`

### Execution Steps

```bash
# Step 4.1: Set GA4 Measurement ID
export GA4_MEASUREMENT_ID="G-XXXXXXXXXX"  # Replace with actual ID

# Step 4.2: Run Phase 6 apply script
cd /workspace
node artifacts/syrabit/scripts/cloudflare-phase6-apply.js
```

### What This Script Does

**Step 1:** Issues mTLS client certificate for api.syrabit.ai
- Enhances origin security
- Validates client certificates at edge

**Step 2:** Enables Cloudflare Image Resizing on zone
- Allows on-the-fly image optimization
- Requires paid plan feature

**Step 3:** Configures Zaraz with GA4 tool
- Sets up page-view tracking
- Configures click event tracking
- Links to GA4 Measurement ID

**Step 4:** Schedules weekly Observatory Lighthouse runs
- Homepage audit
- Chapter page audit
- Performance monitoring

### Expected Output
```
Step 1 — mTLS client certificate
  ✓  Certificate issued for api.syrabit.ai

Step 2 — Image Resizing
  ✓  Image Resizing enabled on zone

Step 3 — Zaraz GA4 configuration
  ✓  GA4 tool configured with measurement ID G-XXXXXXXXXX

Step 4 — Observatory scheduling
  ✓  Weekly Lighthouse runs scheduled
```

### Prerequisites
- GA4 property must exist in Google Analytics
- Token must have Zaraz: Edit scope
- Zone must support Zaraz (Enterprise/Business plan)

---

## Layer 5: Verification & CI Re-run

### Step 5.1: Commit Changes
```bash
cd /workspace
git add -A
git commit -m "fix: apply Cloudflare phases 1, 4, 5, 6 to resolve audit failures

- Phase 1: Zone settings hardening (HTTP/3, TLS 1.3, HSTS, etc.)
- Phase 4: R2 buckets (syrabit-assets, syrabit-cache-reserve) and custom domain
- Phase 5: Deploy syrabitworker with Analytics Engine binding
- Phase 6: Zaraz GA4 + Observatory configuration

Resolves audit failures from weekly digest #8"
git push origin main
```

### Step 5.2: Trigger CI Audit
The GitHub Actions workflow will automatically run:
1. Push to main branch triggers workflow
2. Cloudflare audit job executes
3. Results posted to Issue #8

### Step 5.3: Monitor Results
Check GitHub Actions tab for:
- ✅ All 5 previously failed items should now PASS
- ⚠️ Warnings remain (token scope gaps - informational only)

### Expected Final Status
```
✅ PASS: 8 (was 3)
❌ FAIL: 0 (was 5)
⚠️ WARN: 10 (unchanged - token scope gaps)
⬜ SKIP: 1 (unchanged)
Total: 19
```

---

## Troubleshooting

### Error: "token lacks [scope]"
**Solution:** Update API token at https://dash.cloudflare.com/profile/api-tokens
- Add the missing scope
- Regenerate token if necessary
- Update GitHub secret: `CLOUDFLARE_API_TOKEN`

### Error: "no space left on device"
**Solution:** Clean up workspace before running scripts
```bash
rm -rf node_modules
rm -rf /tmp/*
```

### Error: "Worker does not exist"
**Solution:** Ensure wrangler is authenticated
```bash
npx wrangler login
npx wrangler deploy
```

### Error: "Bucket not found"
**Solution:** Run Phase 4 steps in order - bucket creation must precede domain configuration

---

## Post-Fix Maintenance

### Weekly Audit Monitoring
- Watch Issue #8 for weekly automated audit results
- Every Monday, CI posts full 19-item result
- Address new failures within 48 hours

### Token Scope Review
Quarterly review of API token scopes:
1. Check if any new features require additional scopes
2. Remove unused scopes (principle of least privilege)
3. Rotate tokens every 90 days

### Documentation Updates
Keep these docs current:
- `ENVIRONMENT_VARIABLES.md` - API token requirements
- `CLOUDFLARE_DEPLOYMENT_WIRING.md` - Architecture diagrams
- `docs/SECRET_ROTATION.md` - Token rotation procedures

---

## Appendix: Script Locations

| Script | Path | Purpose |
|--------|------|---------|
| Phase 1 | `artifacts/syrabit/scripts/cloudflare-phase1-apply.js` | Zone settings |
| Phase 2 | `artifacts/syrabit/scripts/cloudflare-phase2-apply.js` | Logpush jobs |
| Phase 3 | `artifacts/syrabit/scripts/cloudflare-phase3-apply.js` | Zero Trust + Waiting Room |
| Phase 4 | `artifacts/syrabit/scripts/cloudflare-phase4-apply.js` | R2 + Cache Reserve |
| Phase 5 | `artifacts/syrabit/scripts/cloudflare-phase5-apply.js` | Analytics Engine |
| Phase 6 | `artifacts/syrabit/scripts/cloudflare-phase6-apply.js` | mTLS + Zaraz + Images |

---

## References

- Cloudflare API Documentation: https://api.cloudflare.com/
- Wrangler CLI Docs: https://developers.cloudflare.com/workers/wrangler/
- R2 Documentation: https://developers.cloudflare.com/r2/
- Zaraz Documentation: https://developers.cloudflare.com/zaraz/
- Observatory Documentation: https://developers.cloudflare.com/speed/observatory/
