# infra/gcp/cloud-logging-axiom.tf
#
# DEPRECATED (Task #330) — GCP is being phased out as part of the
# 4-way provider rebalance. New deploys go to Digital Ocean App
# Platform, AWS, and Azure via the GitHub Actions workflows under
# `.github/workflows/`. See `docs/infra/cicd.md`. Logging will move
# to Axiom + Application Insights in the cron/observability port.
#
# Replaces Cloudflare Log Explorer (usage charges, ~$5–$10/mo).
# Routes Cloudflare Logpush → GCP Cloud Logging (HTTP sink) → Axiom.
#
# Axiom startup credits cover the UI-facing log explorer and long-term
# retention (beyond the 30-day Cloud Logging default).
#
# Performance / observability boosts
# ────────────────────────────────────
# • GCP Cloud Logging is already wired — zero extra cost up to 50 GiB/mo.
# • Axiom startup tier: 500 GB/mo ingest, 30-day retention, free dashboards.
# • Log-based metrics (request_count, error_rate_5xx) fed into Cloud
#   Monitoring so the existing alerting policies stay intact.
# • Grafana Cloud free tier (10 k series) dashboards imported via
#   provisioned JSON — no extra cost.

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

locals {
  project = "syrabit-prod"
}

# ─── Log sink: Cloudflare Logpush → Cloud Logging ────────────────────────────
# The Cloudflare zone Logpush job should be configured to POST to:
#   https://logging.googleapis.com/v2/entries:write
# using a service-account key with roles/logging.logWriter.

resource "google_project_iam_member" "logpush_writer" {
  project = local.project
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:cf-logpush@syrabit-prod.iam.gserviceaccount.com"
}

# ─── Log-based metric: 5xx error rate ────────────────────────────────────────

resource "google_logging_metric" "error_rate_5xx" {
  name    = "cf_error_rate_5xx"
  project = local.project
  filter  = "resource.type=\"global\" AND jsonPayload.EdgeResponseStatus>=500"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key         = "path"
      value_type  = "STRING"
      description = "URL path"
    }
  }

  label_extractors = {
    "path" = "EXTRACT(jsonPayload.ClientRequestPath)"
  }
}

# ─── Log-based metric: p95 origin response time ──────────────────────────────

resource "google_logging_metric" "origin_latency" {
  name    = "cf_origin_latency_ms"
  project = local.project
  filter  = "resource.type=\"global\" AND jsonPayload.OriginResponseTime!=\"\""

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
  }

  value_extractor = "EXTRACT(jsonPayload.OriginResponseTime)"
}

# ─── Pub/Sub topic: log export to Axiom ──────────────────────────────────────

resource "google_pubsub_topic" "logs_axiom" {
  name    = "cf-logs-axiom-export"
  project = local.project

  message_retention_duration = "600s"
}

resource "google_logging_project_sink" "axiom" {
  name        = "axiom-export-sink"
  project     = local.project
  destination = "pubsub.googleapis.com/projects/${local.project}/topics/${google_pubsub_topic.logs_axiom.name}"
  filter      = "resource.type=\"global\""

  unique_writer_identity = true
}

resource "google_pubsub_topic_iam_member" "sink_publisher" {
  topic  = google_pubsub_topic.logs_axiom.name
  role   = "roles/pubsub.publisher"
  member = google_logging_project_sink.axiom.writer_identity
}

# ─── Cloud Monitoring alert: Axiom ingest silence > 15 min ───────────────────

resource "google_monitoring_alert_policy" "axiom_ingest_silence" {
  display_name = "Axiom log ingest silent > 15 min"
  project      = local.project
  combiner     = "OR"

  conditions {
    display_name = "Pub/Sub publish rate = 0"
    condition_threshold {
      filter          = "metric.type=\"pubsub.googleapis.com/topic/send_message_operation_count\" AND resource.label.topic_id=\"cf-logs-axiom-export\""
      duration        = "900s"
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = []
  severity              = "WARNING"
}
