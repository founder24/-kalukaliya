# Syrabit Backend on Google Cloud Run (Task #606)

> ⚠️ **RETIRED PATH — DO NOT FOLLOW.** Google Cloud Run is **not** part of
> the current three-cloud hosting plan. **GCP / Vertex is inference-only**
> (Gemini API, plus retained Vision/STT/TTS/Discovery/Web Risk APIs) per
> [`../../docs/infra/cloud-allocation-plan.md`](../../docs/infra/cloud-allocation-plan.md)
> §6 — there is no GCP compute, no GCP secondary backend origin, and Task
> #606 has been dropped from the plan. The canonical backend origin is
> **AWS App Runner** in us-west-2 (§4.2); see
> [`./DEPLOY.md`](./DEPLOY.md) for the active runbook. This file is kept
> only for historical context.

---


Cloud Run is the production API origin behind Cloudflare. Cloudflare keeps
DNS, WAF, CDN, and the existing edge worker; Cloud Run gives us autoscaling,
scale-to-zero, fast cold starts, and a clean GCP-side home for the AI
workloads. The Cloud Run URL is **never** reached directly — the worker
proxies `api.syrabit.ai` to it and authenticates with a shared secret
(`X-Origin-Auth`).

```
syrabit.ai (Cloudflare Pages)
       │
       ▼
api.syrabit.ai (Cloudflare Worker — syrabit-edge)
       │ + X-Origin-Auth: <ORIGIN_SHARED_SECRET>
       ▼
syrabit-backend (Cloud Run, region: asia-south1)
       │
       ├─ MongoDB Atlas
       ├─ Supabase Postgres
       └─ Upstash Redis
```

---

## 1 — One-time GCP setup

```bash
PROJECT_ID=syrabit-prod
REGION=asia-south1
AR_REPO=syrabit
SERVICE=syrabit-backend
RUNTIME_SA=syrabit-backend@${PROJECT_ID}.iam.gserviceaccount.com

gcloud config set project ${PROJECT_ID}

# Enable APIs
# Note: Cloud Error Reporting (clouderrorreporting.googleapis.com) was
# previously enabled here, but no application code emits structured error
# events to it — Cloud Run streams stderr to Cloud Logging, which is a
# different surface. Re-add it here AND grant roles/errorreporting.writer
# below if/when an actual emitter is wired (e.g. google-cloud-error-reporting
# SDK calls from a global exception handler).
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  logging.googleapis.com

# Artifact Registry repo (Docker)
gcloud artifacts repositories create ${AR_REPO} \
  --repository-format=docker \
  --location=${REGION} \
  --description="Syrabit container images"

# Runtime service account (least-privilege)
gcloud iam service-accounts create syrabit-backend \
  --display-name="Syrabit Cloud Run runtime"

# Allow the runtime SA to read every secret it binds to (we set --update-secrets
# below, which requires roles/secretmanager.secretAccessor).
for ROLE in roles/secretmanager.secretAccessor roles/logging.logWriter \
            roles/monitoring.metricWriter roles/cloudtrace.agent; do
  # roles/errorreporting.writer was removed alongside the
  # clouderrorreporting.googleapis.com API enable above — re-add both
  # together when an actual error-reporting emitter is wired.
  gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${RUNTIME_SA}" --role="${ROLE}"
done

# Cloud Build SA needs Cloud Run Admin + actAs on the runtime SA so the
# `deploy` step in cloudbuild.yaml can roll a revision.
PROJECT_NUM=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
CB_SA=${PROJECT_NUM}@cloudbuild.gserviceaccount.com
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${CB_SA}" --role=roles/run.admin
gcloud iam service-accounts add-iam-policy-binding ${RUNTIME_SA} \
  --member="serviceAccount:${CB_SA}" --role=roles/iam.serviceAccountUser
```

---

## 2 — Create Secret Manager entries

Mirror the Railway env. The shared-secret entry below is the new one
introduced by Task #606 — generate it once and reuse the same value when
you bind the worker secret in step 6.

> **BYOK note (Task #666):** do NOT create a `gemini-api-key` Secret
> Manager entry and do NOT pass `GEMINI_API_KEY` to Cloud Run. After the
> BYOK migration, Gemini auth is supplied per-request by the Cloudflare
> AI Gateway binding using the user's own key. The block below has been
> trimmed accordingly.

```bash
# Generate a fresh shared secret (write it down — you'll bind it on the
# worker side too).
ORIGIN_SHARED_SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')
printf '%s' "${ORIGIN_SHARED_SECRET}" | \
  gcloud secrets create origin-shared-secret --data-file=-

# Mirror Railway env. Replace each <…> with the live value.
for entry in \
  "mongo-url:<MONGO_URL>" \
  "db-name:test_database" \
  "database-url:<SUPABASE_POSTGRES_DSN>" \
  "upstash-redis-rest-url:<UPSTASH_REDIS_REST_URL>" \
  "upstash-redis-rest-token:<UPSTASH_REDIS_REST_TOKEN>" \
  "supabase-url:<SUPABASE_URL>" \
  "supabase-service-key:<SUPABASE_SERVICE_KEY>" \
  "supabase-anon-key:<SUPABASE_ANON_KEY>" \
  "jwt-secret:<JWT_SECRET>" \
  "admin-jwt-secret:<ADMIN_JWT_SECRET>" \
  "admin-emails:<ADMIN_EMAILS>" \
  "admin-passwords:<ADMIN_PASSWORDS>" \
  "admin-names:<ADMIN_NAMES>" \
  "google-oauth-client-id:<GOOGLE_OAUTH_CLIENT_ID>" \
  "google-oauth-client-secret:<GOOGLE_OAUTH_CLIENT_SECRET>" \
  "groq-api-key:<GROQ_API_KEY>" \
  "groq-api-key-2:<GROQ_API_KEY_2>" \
  "cerebras-api-key:<CEREBRAS_API_KEY>" \
  "sarvam-api-key:<SARVAM_API_KEY>" \
  "sarvam-api-key-2:<SARVAM_API_KEY_2>" \
  "openrouter-api-key:<OPENROUTER_API_KEY>" \
  "razorpay-key-id:<RAZORPAY_KEY_ID>" \
  "razorpay-key-secret:<RAZORPAY_KEY_SECRET>" \
  "razorpay-webhook-secret:<RAZORPAY_WEBHOOK_SECRET>" \
  "resend-api-key:<RESEND_API_KEY>" \
  "cf-ai-gateway-account-id:<CF_AI_GATEWAY_ACCOUNT_ID>" \
  "cf-ai-gateway-id:<CF_AI_GATEWAY_ID>" \
  "cf-ai-gateway-token:<CF_AI_GATEWAY_TOKEN>" \
  "cloudflare-api-token:<CLOUDFLARE_API_TOKEN>" \
  "cf-zone-id:<CF_ZONE_ID>" \
  "cf-pages-deploy-hook-url:<CF_PAGES_DEPLOY_HOOK_URL>" \
  "d1-sync-secret:<D1_SYNC_SECRET>" \
  "kv-alert-secret:<KV_ALERT_SECRET>"; do
  name=${entry%%:*}; value=${entry#*:}
  printf '%s' "${value}" | gcloud secrets create "${name}" --data-file=- || \
    printf '%s' "${value}" | gcloud secrets versions add "${name}" --data-file=-
done
```

---

## 3 — Build and deploy

You can use either Cloud Build (auto on push, recommended) or the local
helper script for ad-hoc rollouts. Both call `gcloud run deploy` with the
same flags.

### Option A — Cloud Build trigger (recommended)

```bash
gcloud builds triggers create github \
  --name=syrabit-backend-main \
  --repo-name=<your-repo> --repo-owner=<your-org> \
  --branch-pattern=^main$ \
  --build-config=artifacts/syrabit-backend/cloudbuild.yaml \
  --included-files=artifacts/syrabit-backend/** \
  --substitutions=_REGION=${REGION},_AR_REPO=${AR_REPO},_SERVICE=${SERVICE},_IMAGE=backend,_RUNTIME_SA=${RUNTIME_SA},_MIN_INSTANCES=1,_MAX_INSTANCES=10,_CONCURRENCY=80,_CPU=2,_MEMORY=2Gi,_SECRETS=ORIGIN_SHARED_SECRET=origin-shared-secret:latest\,MONGO_URL=mongo-url:latest\,DB_NAME=db-name:latest\,DATABASE_URL=database-url:latest\,UPSTASH_REDIS_REST_URL=upstash-redis-rest-url:latest\,UPSTASH_REDIS_REST_TOKEN=upstash-redis-rest-token:latest\,SUPABASE_URL=supabase-url:latest\,SUPABASE_SERVICE_KEY=supabase-service-key:latest\,SUPABASE_ANON_KEY=supabase-anon-key:latest\,JWT_SECRET=jwt-secret:latest\,ADMIN_JWT_SECRET=admin-jwt-secret:latest\,ADMIN_EMAILS=admin-emails:latest\,ADMIN_PASSWORDS=admin-passwords:latest\,ADMIN_NAMES=admin-names:latest\,GOOGLE_OAUTH_CLIENT_ID=google-oauth-client-id:latest\,GOOGLE_OAUTH_CLIENT_SECRET=google-oauth-client-secret:latest\,GROQ_API_KEY=groq-api-key:latest\,GROQ_API_KEY_2=groq-api-key-2:latest\,CEREBRAS_API_KEY=cerebras-api-key:latest\,SARVAM_API_KEY=sarvam-api-key:latest\,SARVAM_API_KEY_2=sarvam-api-key-2:latest\,OPENROUTER_API_KEY=openrouter-api-key:latest\,RAZORPAY_KEY_ID=razorpay-key-id:latest\,RAZORPAY_KEY_SECRET=razorpay-key-secret:latest\,RAZORPAY_WEBHOOK_SECRET=razorpay-webhook-secret:latest\,RESEND_API_KEY=resend-api-key:latest\,CF_AI_GATEWAY_ACCOUNT_ID=cf-ai-gateway-account-id:latest\,CF_AI_GATEWAY_ID=cf-ai-gateway-id:latest\,CF_AI_GATEWAY_TOKEN=cf-ai-gateway-token:latest\,CLOUDFLARE_API_TOKEN=cloudflare-api-token:latest\,CF_ZONE_ID=cf-zone-id:latest\,CF_PAGES_DEPLOY_HOOK_URL=cf-pages-deploy-hook-url:latest\,D1_SYNC_SECRET=d1-sync-secret:latest\,KV_ALERT_SECRET=kv-alert-secret:latest
```

Pushes to `main` that touch `artifacts/syrabit-backend/**` build, push, and
roll out a new Cloud Run revision automatically. The `cloudbuild.yaml`
explicitly points `docker build` at `artifacts/syrabit-backend/Dockerfile`
with that folder as the build context, so the trigger works regardless
of whether you set the trigger working directory to repo-root or to the
backend folder.

#### Security posture (read this once)

The Cloud Run service is deployed `--allow-unauthenticated --ingress=all`
because Cloudflare Workers cannot mint Google-signed ID tokens, so we
cannot use Cloud Run's IAM-based origin auth. Defence-in-depth instead
relies on:

1. The `OriginSharedSecretMiddleware` (rejects everything except
   `/api/health` and `/health` with 403 unless the edge-injected
   `X-Origin-Auth` header matches `ORIGIN_SHARED_SECRET`).
2. The `*.run.app` URL never being published — DNS only points at
   Cloudflare, and the URL is treated as a secret in this runbook.
3. Cloudflare WAF / rate-limit rules in front of the worker.

`/docs` and `/openapi.json` are intentionally **not** in the open-paths
list, so the OpenAPI schema cannot be enumerated against the run.app
URL even by someone who guesses it. To use the swagger docs in
production, hit them through `https://api.syrabit.ai` so the worker
adds the secret header.

### Option B — local one-shot deploy

```bash
cd artifacts/syrabit-backend
PROJECT_ID=syrabit-prod REGION=asia-south1 AR_REPO=syrabit \
SERVICE=syrabit-backend \
RUNTIME_SA=syrabit-backend@syrabit-prod.iam.gserviceaccount.com \
SECRETS=ORIGIN_SHARED_SECRET=origin-shared-secret:latest,MONGO_URL=mongo-url:latest,…(see above) \
./scripts/deploy_cloud_run.sh
```

---

## 4 — Configure non-secret env vars on Cloud Run

The deploy commands set `PORT=8080`, `LOG_LEVEL=warning`, and
`GUNICORN_WORKERS=2` via `--set-env-vars`. The remainder is bound from
Secret Manager via `--update-secrets`. To add or change non-secret env
vars (e.g. `ASSAMESE_LEAK_BEHAVIOUR`, `RAG_RETRIEVER`, `LLM_MAX_CONCURRENT`),
add them to the `--set-env-vars` flag in `cloudbuild.yaml` and
`scripts/deploy_cloud_run.sh`, then redeploy.

---

## 5 — Health, statelessness, signals

The container is already Cloud Run-ready:

- **PORT** — `gunicorn.conf.py` binds to `${PORT:-7766}`; we override to
  `8080` (Cloud Run's default).
- **Healthcheck** — `GET /api/health` returns 200 once Mongo / Redis /
  Postgres are connected. Cloud Run's startup probe will use it via the
  service config (`gcloud run services update --startup-probe=…`).
- **SIGTERM** — Gunicorn handles SIGTERM gracefully with
  `graceful_timeout = 60`. Cloud Run sends SIGTERM 10s before terminating
  an instance, so workers drain in-flight requests cleanly.
- **Stateless** — no on-disk state. The startup-leader lock
  (`/tmp/.syrabit_startup.lock` in `lifespan`) is per-instance; multiple
  Cloud Run instances each acquire their own /tmp lock and gracefully
  skip seeding/index creation as designed.

---

## 6 — Bind the worker to Cloud Run

After the first revision is live, point Cloudflare at it:

```bash
# 1. Set the shared secret on the worker (use the SAME value that's in
#    Secret Manager as `origin-shared-secret`).
cd workers/edge-proxy
echo "${ORIGIN_SHARED_SECRET}" | wrangler secret put BACKEND_ORIGIN_SECRET

# 2. In the Cloudflare dashboard → Workers & Pages → syrabit-edge →
#    Settings → Variables, set BACKEND_URL (encrypted) to the Cloud Run
#    service URL, e.g. https://syrabit-backend-abc123-as.a.run.app
#    (Encrypted vars are not overwritten by wrangler.toml — see the
#    comment block in wrangler.toml for why.)

# 3. Redeploy the worker so it picks up the new secret + URL.
wrangler deploy
```

### Parallel validation

Before you flip the dashboard `BACKEND_URL`, run both origins in parallel:

```bash
# Direct hit Digital Ocean App Platform (today's prod, control — Task #336)
curl -fsS https://syrabit-backend-app.ondigitalocean.app/api/health

# Direct hit Cloud Run with the shared secret (proves the new origin works)
curl -fsS -H "X-Origin-Auth: ${ORIGIN_SHARED_SECRET}" \
  https://syrabit-backend-abc123-as.a.run.app/api/health
# expected: 200  ({"status":"ok",...})

# Health endpoint is intentionally open without the secret so Cloud Run's
# own startup/liveness probes succeed. This must return 200:
curl -fsS -o /dev/null -w '%{http_code}\n' \
  https://syrabit-backend-abc123-as.a.run.app/api/health
# expected: 200

# Now prove every OTHER endpoint is locked down without the secret.
# Pick any non-health route — admin/me is a good probe — and confirm 403:
curl -fsS -o /dev/null -w '%{http_code}\n' \
  https://syrabit-backend-abc123-as.a.run.app/api/auth/me
# expected: 403  (proof the run.app origin only exposes /health publicly)

# Same route WITH the secret should bypass the middleware (you'll then
# see whatever that endpoint normally returns, e.g. 401 unauth — that's
# fine; the point is it's no longer 403):
curl -fsS -o /dev/null -w '%{http_code}\n' \
  -H "X-Origin-Auth: ${ORIGIN_SHARED_SECRET}" \
  https://syrabit-backend-abc123-as.a.run.app/api/auth/me
# expected: NOT 403 (typically 401 because no JWT was supplied)

# Smoke chat through the worker once you flip BACKEND_URL.
curl -fsS https://api.syrabit.ai/api/health
```

A short parity script lives at `bench/retriever_bench.py` (use it to
sample chat / RAG / SEO endpoints against both origins and diff the JSON).

For a one-shot regression check of the shared-secret enforcement itself
(handy as a Cloud Build post-deploy step), use:

```bash
BACKEND_URL=https://syrabit-backend-abc123-as.a.run.app \
ORIGIN_SHARED_SECRET=$(gcloud secrets versions access latest --secret=origin-shared-secret) \
./scripts/smoke_origin_secret.sh
```

It exits non-zero if `/api/health` is not 200, if a protected route is
not 403 without the header, or if the header is not honoured — exactly
the three conditions cutover relies on.

---

## 7 — Logs, errors, latency

- **Logs** — the JSON formatter in `server.py` (`_JSONFormatter`) writes
  each entry as a single line on stdout. Cloud Run forwards stdout to
  Cloud Logging automatically, and Logs Explorer parses the JSON fields
  natively (filter by `jsonPayload.request_id`, `jsonPayload.level`).
- **Errors** — Cloud Logging entries with `severity>=ERROR` are picked up
  by Error Reporting automatically. No code changes needed.
- **Latency baseline** — capture once after the first deploy and again
  before cutover:

  ```bash
  for i in 1 2 3 4 5; do
    curl -o /dev/null -s -w '%{time_total}\n' \
      -H "X-Origin-Auth: ${ORIGIN_SHARED_SECRET}" \
      https://syrabit-backend-abc123-as.a.run.app/api/health
  done
  ```

  Record cold-start (first request after `--min-instances=0`) and steady
  state (warm). Document in `RUNBOOK.md` — chat p95 must be no worse than
  the Railway baseline before you flip `BACKEND_URL`.

---

## 8 — Rollback

```bash
# List revisions
gcloud run revisions list --service=${SERVICE} --region=${REGION}

# Pin 100% traffic back to a previous revision (zero-downtime)
gcloud run services update-traffic ${SERVICE} \
  --region=${REGION} \
  --to-revisions=syrabit-backend-00042-abc=100
```

If the worker is the broken layer (e.g. wrong `BACKEND_URL`), revert via
the dashboard or `wrangler rollback`.

---

## 9 — Decommissioning Railway

Out of scope for Task #606. Cloud Run runs in parallel until parity is
proven; cutover (flipping the dashboard `BACKEND_URL` from Railway →
Cloud Run) is a follow-up task.
