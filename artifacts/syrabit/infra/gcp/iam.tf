# infra/gcp/iam.tf — single least-privilege service account for the
# post-cleanup GCP AI-API + observability + billing-telemetry surface.
#
# Task #489 lock: this SA must NEVER be granted hosting / cron / build
# / queueing roles. The CI drift guard
# (.github/workflows/four-cloud-delegation-drift.yml) fails the merge
# if any forbidden role appears here. See infra/four-cloud-delegation.md
# §B "GCP / Vertex must NOT" and the "no" list at the bottom of §B.

resource "google_service_account" "ai_apis" {
  account_id   = "syrabit-ai-apis"
  display_name = "Syrabit AI APIs (Web Risk, STT/TTS, Vision, Translate, Discovery, Cloud Trace, Billing)"
  description  = "Single SA used by the FastAPI backend for every GCP API call. Rotated quarterly per V4 §6."
  project      = var.gcp_project_id
}

# ─── Project-scope roles ────────────────────────────────────────────────────

resource "google_project_iam_member" "service_usage_consumer" {
  project = var.gcp_project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = "serviceAccount:${google_service_account.ai_apis.email}"
}

resource "google_project_iam_member" "cloud_trace_agent" {
  project = var.gcp_project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.ai_apis.email}"
}

resource "google_project_iam_member" "bigquery_job_user" {
  project = var.gcp_project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.ai_apis.email}"
}

resource "google_project_iam_member" "discovery_engine_editor" {
  project = var.gcp_project_id
  role    = "roles/discoveryengine.editor"
  member  = "serviceAccount:${google_service_account.ai_apis.email}"
}

# NOTE — `roles/aiplatform.user` is intentionally NOT bound here. Sibling
# task #494 owns the Vertex Gemini content-formatter wiring (NOT chat —
# chat is Azure OpenAI per V4 §4 founder lock) and will add both the API
# enablement (in main.tf) and this IAM binding in its own PR. Keeping the
# role out of #489 makes the lock-in cleanly auditable: this SA has zero
# Vertex/AI-Platform reach until #494 explicitly grants it.

# ─── Billing-account-scope role ─────────────────────────────────────────────

resource "google_billing_account_iam_member" "billing_viewer" {
  billing_account_id = var.gcp_billing_account_id
  role               = "roles/billing.viewer"
  member             = "serviceAccount:${google_service_account.ai_apis.email}"
}

# ─── BigQuery dataset-scope role (Billing Export) ───────────────────────────

resource "google_bigquery_dataset_iam_member" "billing_export_viewer" {
  dataset_id = var.gcp_billing_export_dataset
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.ai_apis.email}"
  project    = var.gcp_project_id

  depends_on = [google_project_service.enabled]
}

# ─── Outputs ────────────────────────────────────────────────────────────────

output "ai_apis_service_account_email" {
  description = "Service account email used by the FastAPI backend for every GCP API call."
  value       = google_service_account.ai_apis.email
}
