---
name: Syrabit GCP SA Setup
description: Required IAM roles for syrabit-backend-sa and how to set them; one-shot Cloud Shell and Cloudflare Worker secrets scripts
---

## Service Account
`syrabit-backend-sa@blissful-acumen-495019-t6.iam.gserviceaccount.com`

Used by:
- Cloud Run backend (via `GOOGLE_APPLICATION_CREDENTIALS_JSON` secret)
- Cloudflare Edge Worker (via `GOOGLE_SA_KEY` Worker secret — same JSON — for OIDC identity token to call Cloud Run)

## Required IAM Roles (project-level)
| Role | Why |
|---|---|
| `roles/aiplatform.user` | Vertex AI Gemini text + vision + TTS inference |
| `roles/discoveryengine.admin` | Vertex AI Search index + search |
| `roles/storage.objectAdmin` | GCS content bucket read/write |
| `roles/secretmanager.secretAccessor` | Read secrets at Cloud Run runtime |
| `roles/run.invoker` | Allow Edge Worker OIDC identity token to invoke Cloud Run |
| `roles/logging.logWriter` | Cloud Logging |
| `roles/cloudtrace.agent` | Cloud Trace |
| `roles/monitoring.metricWriter` | Cloud Monitoring |
| `roles/serviceusage.serviceUsageConsumer` | API calls against the project |

Cloud Build SA (`{PROJECT_NUM}@cloudbuild.gserviceaccount.com`) also needs:
- `roles/iam.serviceAccountUser` on the backend SA (to deploy with that SA)
- `roles/run.admin` on project
- `roles/secretmanager.secretAccessor` on project

## Setup Scripts (run in Cloud Shell from repo root)
- **All IAM roles + secret creation**: `bash infra/scripts/gcp-full-setup.sh`
- **Cloudflare Worker secrets**: `bash infra/scripts/cloudflare-worker-secrets.sh`
  - Auto-pulls `JWT_SECRET` + `EDGE_SHARED_SECRET` from Secret Manager
  - Auto-fetches `BACKEND_URL` from `gcloud run services describe`
  - Offers to use `GOOGLE_APPLICATION_CREDENTIALS_JSON` as `GOOGLE_SA_KEY`

## Replit SA limitations
The Replit SA (`GOOGLE_APPLICATION_CREDENTIALS_JSON` env) can access GCS, Vertex AI, Cloud Run, Cloud Build, Artifact Registry. It gets 403 on IAM policy reads, Secret Manager, and Service Usage APIs. Always use Cloud Shell for IAM grants.

## GCS buckets (both exist)
- `gs://syrabit-content` (ASIA-SOUTH1) — set via `GCS_CONTENT_BUCKET` secret in SM
- `gs://blissful-acumen-495019-t6-syrabit-content` (ASIA-SOUTH1) — fallback in code when `GCS_CONTENT_BUCKET` not set

**Why:** `gcs_store.py` fallback = `{VERTEX_PROJECT_ID}-syrabit-content`. Keep `GCS_CONTENT_BUCKET=syrabit-content` in SM to use the shorter name.
