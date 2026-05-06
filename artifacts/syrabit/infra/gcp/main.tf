terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
  }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}

variable "gcp_project_id" {
  description = "GCP project that owns the AI-API + observability surface (Task #489)."
  type        = string
  default     = "syrabit-prod"
}

variable "gcp_region" {
  description = "Default region for AI APIs that accept a region pin (asia-south1 = Mumbai, closest to majority of users)."
  type        = string
  default     = "asia-south1"
}

variable "gcp_billing_account_id" {
  description = "Billing account ID for budget alerts. Read-only role bound on this account."
  type        = string
}

variable "gcp_billing_export_dataset" {
  description = "BigQuery dataset that holds the GCP Billing Export table. roles/bigquery.dataViewer is bound on this dataset."
  type        = string
  default     = "billing_export"
}

variable "gcp_discovery_data_store" {
  description = "Discovery Engine data-store ID for the Syrabit topic ingest pipeline."
  type        = string
  default     = ""
}

variable "ops_alert_email" {
  description = "Email recipient for budget alerts (50/80/100 %)."
  type        = string
  default     = "ops@syrabit.ai"
}

locals {
  project_label = "syrabit"

  common_labels = {
    project = local.project_label
    owner   = "infra"
    task    = "489"
    env     = "prod"
  }

  # APIs we explicitly enable. Intentionally OMITTED (must stay disabled
  # per the four-cloud delegation lock):
  #   run.googleapis.com, cloudbuild.googleapis.com,
  #   artifactregistry.googleapis.com, cloudtasks.googleapis.com,
  #   cloudscheduler.googleapis.com, cloudfunctions.googleapis.com,
  #   compute.googleapis.com, container.googleapis.com.
  enabled_apis = toset([
    # API-key surfaces (no SA needed)
    "webrisk.googleapis.com",
    "kgsearch.googleapis.com",
    "pagespeedonline.googleapis.com",
    "factchecktools.googleapis.com",
    "language.googleapis.com",
    "books.googleapis.com",
    # SA-gated AI surfaces
    "speech.googleapis.com",
    "texttospeech.googleapis.com",
    "translate.googleapis.com",
    "vision.googleapis.com",
    "discoveryengine.googleapis.com",
    # NOTE — `aiplatform.googleapis.com` (Vertex Gemini content-formatter
    # surface) is intentionally NOT enabled here. Sibling task #494 owns
    # the formatter wiring and will append the API + the matching IAM
    # binding in its own PR. Keeping it out of #489 makes the lock-in
    # cleanly auditable: only AI APIs explicitly approved by V4 §0
    # auxiliaries + the Web-Risk row of the matrix are turned on.
    # Observability + billing telemetry
    "cloudtrace.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudbilling.googleapis.com",
    "bigquery.googleapis.com",
    "iamcredentials.googleapis.com",
    "iam.googleapis.com",
  ])
}

resource "google_project_service" "enabled" {
  for_each = local.enabled_apis

  project                    = var.gcp_project_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false
}
