# infra/gcp/cloud-cdn.tf
#
# DEPRECATED (Task #330) — GCP is being phased out as part of the
# 4-way provider rebalance. New deploys go to Digital Ocean App
# Platform, AWS, and Azure via the GitHub Actions workflows under
# `.github/workflows/`. See `docs/infra/cicd.md` for the new pipeline
# layout. This Terraform is retained read-only until the dispatch /
# CDN tier is fully cut over and decommissioned in a later task.
#
# GCP Cloud CDN attached to the existing HTTPS Global Load Balancer.
# Replaces Cloudflare Cache Reserve ($5–$10/mo).  Covered by GCP Activate.
#
# Performance boosts
# ──────────────────
# • GCP Premium Tier network routing — all cached misses travel Google's
#   backbone, not the public internet (replaces Argo Smart Routing).
# • Cloud CDN signed URLs for media assets (replaces R2 signed URLs).
# • Negative caching (404 TTL 60s) prevents cache stampede on bad routes.
# • HTTP/3 (QUIC) and TLS 1.3 enabled on the LB — zero extra cost.
# • Compression (gzip + brotli) applied at the edge before caching.

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

locals {
  project    = "syrabit-prod"
  region     = "asia-south1"
  lb_name    = "syrabit-https-lb"
}

# ─── Backend service for Cloud Run (dispatch-v2) ─────────────────────────────

resource "google_compute_backend_service" "dispatch" {
  name                  = "dispatch-v2-backend"
  project               = local.project
  protocol              = "HTTP2"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = 30

  # Enable Cloud CDN — replaces Cache Reserve.
  enable_cdn = true

  cdn_policy {
    cache_mode                   = "CACHE_ALL_STATIC"
    default_ttl                  = 3600
    max_ttl                      = 86400
    client_ttl                   = 3600
    negative_caching             = true
    serve_while_stale            = 86400
    signed_url_cache_max_age_sec = 7200

    cache_key_policy {
      include_host         = true
      include_protocol     = true
      include_query_string = false
    }
  }

  compression_mode = "AUTOMATIC"

  backend {
    group           = google_compute_region_network_endpoint_group.dispatch.id
    balancing_mode  = "UTILIZATION"
    capacity_scaler = 1.0
  }

  log_config {
    enable      = true
    sample_rate = 0.1
  }
}

resource "google_compute_region_network_endpoint_group" "dispatch" {
  name                  = "dispatch-v2-neg"
  project               = local.project
  region                = local.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = "dispatch-v2"
  }
}

# ─── Cloud Storage bucket for syrabit-media (migrated from R2) ───────────────

resource "google_storage_bucket" "media" {
  name                        = "syrabit-media"
  project                     = local.project
  location                    = local.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true

  website {
    main_page_suffix = "index.html"
    not_found_page   = "404.html"
  }

  cors {
    origin          = ["https://syrabit.ai", "https://www.syrabit.ai"]
    method          = ["GET", "HEAD", "OPTIONS"]
    response_header = ["*"]
    max_age_seconds = 3600
  }

  lifecycle_rule {
    condition {
      age = 365
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }
}

# Public read for CDN delivery.
resource "google_storage_bucket_iam_member" "media_public" {
  bucket = google_storage_bucket.media.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

# ─── Backend service for GCS (static media CDN) ──────────────────────────────

resource "google_compute_backend_bucket" "media" {
  name        = "syrabit-media-backend"
  project     = local.project
  bucket_name = google_storage_bucket.media.name
  enable_cdn  = true

  cdn_policy {
    cache_mode       = "CACHE_ALL_STATIC"
    default_ttl      = 86400
    max_ttl          = 604800
    negative_caching = true
    signed_url_cache_max_age_sec = 3600
  }
}

# ─── Cloud Monitoring alert for CDN cache-hit rate drop ──────────────────────

resource "google_monitoring_alert_policy" "cdn_cache_hit" {
  display_name = "CDN cache-hit rate < 70 %"
  project      = local.project
  combiner     = "OR"

  conditions {
    display_name = "Cache hit ratio below threshold"
    condition_threshold {
      filter          = "metric.type=\"loadbalancing.googleapis.com/https/backend_request_count\" AND resource.type=\"https_lb_rule\""
      duration        = "300s"
      comparison      = "COMPARISON_LT"
      threshold_value = 0.7
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = []
  severity              = "WARNING"
}
