# Deployment — V4 Locked

> **Authoritative against:** [`infra/v4-locked-architecture.md`](../infra/v4-locked-architecture.md).
> See also: `docs/SECRET_ROTATION.md`, `CLOUDFLARE_DEPLOYMENT_WIRING.md`,
> `artifacts/syrabit/docs/infra/aca-cutover.md`.

---

## §1 — Surfaces

| Surface | Where | Deploy mechanism |
|---|---|---|
| **Frontend** (`artifacts/syrabit/`) | Cloudflare Pages project `syrabit-web` | Auto-deploys from `main` branch on push (Cloudflare Pages GitHub integration). |
| **Backend** (`artifacts/syrabit-backend/`) | Azure Container Apps `syrabit-backend` in `eastus2` | `.github/workflows/azure-container-apps-deploy.yml` on push to `main`. |
| **Rust core** (`backend/rust-core/`) | Azure Container Apps `rust-core` (separate app) in `eastus2` | Sister workflow; async-batch only, off the chat hot path. |
| **Edge proxy** (`workers/edge-proxy/`) | Cloudflare Worker `syrabit-edge-proxy` | `wrangler deploy --env production` (manual or via CI). |
| **Embed worker** (`artifacts/syrabit/workers/embed-worker/`) | Cloudflare Workers `syrabit-embed-worker` (prod) + `-staging` | `pnpm run deploy:staging` → smoke → `pnpm run deploy:production` → smoke. See `artifacts/syrabit/workers/embed-worker/README.md`. |
| **Re-embed Lambda** (V4 §3) | AWS Lambda in `ap-south-1` | Terraform-managed; SAM build + `sam deploy --stack syrabit-reembed`. |
| **Secrets sync** | GitHub Actions `secrets-sync.yml` | Cron daily + Azure KV rotation hook. |

---

## §2 — Backend deploy procedure (Azure Container Apps)

The deploy workflow is a single ARM PATCH against the existing ACA
revision. **Do not** introduce a multi-step apply — the cutover
runbook (`artifacts/syrabit/docs/infra/aca-cutover.md`) proves the
single-PATCH path is the only one that doesn't strand traffic on the
helloworld fallback.

### Pre-deploy gate (mandatory)

```bash
cd artifacts/syrabit-backend
python -c "import server"
```

If this fails locally, **do not push**. Silent missing-file drift
between local FS and `main` has broken the live deploy 5 times.
Pre-deploy import smoke gate is tracked in follow-up Task #439.

### Deploy

Push to `main` → workflow runs:

1. Build Docker image, tag with `git sha`, push to ACR.
2. Single ARM PATCH against `syrabit-backend`:
   - `properties.template.containers[0].image` → new image.
   - `properties.configuration.ingress.targetPort = 8000`.
   - `properties.configuration.ingress.traffic = [{latestRevision: true, weight: 100}]`.
   - `properties.template.containers[0].probes` includes `liveness` and `readiness` on `/api/health`.
3. Wait for new revision to be `Healthy`.
4. Verify external health check: `curl https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io/api/health`.

### Bicep template (drift safety)

`infra/azure/aca-syrabit-backend.bicep` mirrors the runtime contract
enforced by the workflow. Drift here regresses the running revision
on the next `az deployment group create`. Verify the template
includes:
- Probe path `/api/health`.
- `ADMIN_JWT_SECRET` wired via `secretRef`.
- `targetPort: 8000`.

### Rollback

ACA revision rollback (within 14 days):

```bash
az containerapp revision activate \
  --name syrabit-backend \
  --resource-group <rg> \
  --revision <previous-revision-name>
az containerapp ingress traffic set \
  --name syrabit-backend \
  --resource-group <rg> \
  --revision-weight <previous-revision-name>=100
```

Hard rollback to DigitalOcean (within 14 days post-cutover only):

```bash
# Re-deploy from .do/app.yaml
doctl apps create-deployment <app-id>
# Flip edge-proxy BACKEND_URL back to the DO origin
cd workers/edge-proxy
wrangler secret put BACKEND_URL --env production  # paste the DO FQDN
wrangler deploy --env production
```

After 14 days, the DO floor expires; only ACA `westus3` re-deploy
remains as the regional-failover path (V4 §8).

---

## §3 — Frontend deploy procedure (Cloudflare Pages)

Auto-deploys on push to `main`. Preview branches get isolated preview
URLs but do not propagate to `syrabit.ai`. To force a re-deploy without
a new commit:

1. Cloudflare Dashboard → Pages → `syrabit-web` → Deployments → **Retry deployment**.

---

## §4 — Edge & embed worker deploys

See `CLOUDFLARE_DEPLOYMENT_WIRING.md` §3.

The embed worker has its own staging environment
(`embed-staging.syrabit.ai`); always smoke-test on staging before
promoting to production. Smoke gate:

```bash
cd artifacts/syrabit/workers/embed-worker
./scripts/smoke.sh staging        # exit non-zero blocks promotion
pnpm run deploy:production
./scripts/smoke.sh production
```

---

## §5 — Disaster recovery (V4 §8)

- **RTO = 4 h, RPO = 15 min.** Quarterly drill is mandatory.
- **Azure `eastus2` is an explicit accepted SPOF.** On regional Azure
  outage, full API is down until `westus3` re-deploy completes.
- **Drill procedure:**
  1. Simulate Cloudflare Workers outage → verify embed-failover to
     Vertex + SQS re-embed queue drain.
  2. Simulate Azure `eastus2` outage → manual `westus3` re-deploy
     from Bicep, restore secrets from KV geo-replica, flip
     edge-proxy `BACKEND_URL` to the new ACA FQDN.
  3. Restore Mongo Atlas + Pinecone from snapshots within 4 h SLA.

---

## §6 — Removed deploy targets (Task #347)

- **Railway** — fully decommissioned by Task #336.
- **DigitalOcean App Platform** — kept on disk for 14-day rollback
  floor only; `.do/app.yaml` and `digitalocean-deploy.yml` will be
  deleted after the rollback window expires.
- **Stripe webhooks** — routes deleted.
- **Resend** — replaced by SendGrid via Azure Marketplace.
