## Task #551 §A — S3 Glacier Deep Archive cold-compliance storage.
##
## Three buckets, one lifecycle policy each. All four-cloud delegation
## §A row "Object storage (cold compliance)" → AWS S3 Glacier Deep
## Archive at ~$0.00099 / GB-month. Frees ~$3-5/mo on Cloudflare R2 by
## moving the never-touched compliance tail off warm storage.
##
## Buckets:
##   • razorpay-receipts  — payment receipts + audit trail (90d → DA)
##   • content-snapshots  — chapter / notes / formatter outputs (180d → DA)
##   • cw-logs-archive    — CloudWatch Logs export tail (30d → DA)
##
## All three expire at 7 years (DPDP / income-tax retention ceiling).
## Restore path: `POST /admin/archive/restore` → S3 RestoreObject Standard
## tier (12 h SLA, $0.02/GB egress charge), documented in
## `artifacts/syrabit/docs/infra/glacier-restore-runbook.md`.

# ── Compliance bucket: Razorpay receipts + payment audit logs ────────────────
resource "aws_s3_bucket" "razorpay_receipts" {
  bucket = "${local.lz_project}-razorpay-receipts-${local.lz_env}"

  tags = merge(local.lz_common_tags, {
    Name        = "${local.lz_project}-razorpay-receipts-${local.lz_env}"
    purpose     = "razorpay-receipts-compliance"
    retention   = "7y"
    storage_tier = "deep-archive-after-90d"
  })
}

resource "aws_s3_bucket_versioning" "razorpay_receipts" {
  bucket = aws_s3_bucket.razorpay_receipts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "razorpay_receipts" {
  bucket                  = aws_s3_bucket.razorpay_receipts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "razorpay_receipts" {
  bucket = aws_s3_bucket.razorpay_receipts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "razorpay_receipts" {
  bucket = aws_s3_bucket.razorpay_receipts.id

  rule {
    id     = "razorpay-receipts-90d-to-deep-archive"
    status = "Enabled"

    filter { prefix = "" }

    transition {
      days          = 90
      storage_class = "DEEP_ARCHIVE"
    }

    # 7-year expiry (DPDP + Indian income-tax audit retention).
    expiration {
      days = 2555
    }

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "DEEP_ARCHIVE"
    }

    noncurrent_version_expiration {
      noncurrent_days = 2555
    }
  }
}

# ── Content snapshots: chapter / notes / formatter outputs ───────────────────
resource "aws_s3_bucket" "content_snapshots" {
  bucket = "${local.lz_project}-content-snapshots-${local.lz_env}"

  tags = merge(local.lz_common_tags, {
    Name         = "${local.lz_project}-content-snapshots-${local.lz_env}"
    purpose      = "content-snapshots-cold"
    retention    = "7y"
    storage_tier = "deep-archive-after-180d"
  })
}

resource "aws_s3_bucket_public_access_block" "content_snapshots" {
  bucket                  = aws_s3_bucket.content_snapshots.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "content_snapshots" {
  bucket = aws_s3_bucket.content_snapshots.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "content_snapshots" {
  bucket = aws_s3_bucket.content_snapshots.id

  rule {
    id     = "content-snapshots-180d-to-deep-archive"
    status = "Enabled"

    filter { prefix = "" }

    transition {
      days          = 180
      storage_class = "DEEP_ARCHIVE"
    }

    expiration {
      days = 2555
    }
  }
}

# ── CloudWatch Logs export tail: 30d hot → Deep Archive ──────────────────────
resource "aws_s3_bucket" "cw_logs_archive" {
  bucket = "${local.lz_project}-cw-logs-archive-${local.lz_env}"

  tags = merge(local.lz_common_tags, {
    Name         = "${local.lz_project}-cw-logs-archive-${local.lz_env}"
    purpose      = "cloudwatch-logs-cold-tail"
    retention    = "7y"
    storage_tier = "deep-archive-after-30d"
  })
}

resource "aws_s3_bucket_public_access_block" "cw_logs_archive" {
  bucket                  = aws_s3_bucket.cw_logs_archive.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cw_logs_archive" {
  bucket = aws_s3_bucket.cw_logs_archive.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "cw_logs_archive" {
  bucket = aws_s3_bucket.cw_logs_archive.id

  rule {
    id     = "cw-logs-30d-to-deep-archive"
    status = "Enabled"

    filter { prefix = "" }

    transition {
      days          = 30
      storage_class = "DEEP_ARCHIVE"
    }

    expiration {
      days = 2555
    }
  }
}

# ── Live archive flow: attach a Deep Archive lifecycle to the existing ──────
#    `var.s3_finals_bucket` (declared in `s3-to-r2-sync.tf`). Reviewer
#    note (Task #551 round-2): the three new compliance buckets above
#    cover NEW writes (Razorpay receipts, content snapshots, CW logs),
#    but the already-live S3 → R2 finals pipeline also accumulates
#    cold objects (PDFs, generated notes) under the `finals/` prefix
#    that should transition to Deep Archive once they age out of the
#    R2 hot mirror. Bucket creation is owned by `s3-to-r2-sync.tf`
#    (input variable, may be pre-existing); we only add the lifecycle.
# NOTE (deferred 2026-05-09): bucket `var.s3_finals_bucket` (default
# syrabit-prod-finals-staging) is not yet provisioned in the new account.
# Re-enable after the s3-to-r2-sync migration creates / imports the
# bucket. The 3 purpose-built Glacier compliance buckets above (Razorpay
# receipts / content snapshots / CW logs) already cover Task #551 7-yr
# WORM retention; this rule is only for the additive cold-archive
# optimization on the legacy `finals/` prefix.
# resource "aws_s3_bucket_lifecycle_configuration" "finals_to_deep_archive" {
#   bucket = var.s3_finals_bucket
#   rule {
#     id     = "finals-180d-to-deep-archive"
#     status = "Enabled"
#     filter { prefix = "finals/" }
#     transition {
#       days          = 180
#       storage_class = "DEEP_ARCHIVE"
#     }
#     expiration {
#       days = 2555
#     }
#     noncurrent_version_transition {
#       noncurrent_days = 30
#       storage_class   = "DEEP_ARCHIVE"
#     }
#   }
# }

# ── Outputs consumed by the FastAPI restore endpoint (admin_archive.py) ──────
output "glacier_archive_buckets" {
  description = "Names of the three Glacier Deep Archive compliance buckets, surfaced via SSM for the FastAPI admin archive-restore endpoint."
  value = {
    razorpay_receipts = aws_s3_bucket.razorpay_receipts.id
    content_snapshots = aws_s3_bucket.content_snapshots.id
    cw_logs_archive   = aws_s3_bucket.cw_logs_archive.id
  }
}

# Publish the bucket names to SSM so the ACA backend can resolve them
# at runtime without a Terraform apply on every config tweak.
resource "aws_ssm_parameter" "glacier_buckets" {
  name        = "/${local.lz_project}/${local.lz_env}/glacier/archive-buckets"
  type        = "String"
  value       = jsonencode({
    razorpay_receipts = aws_s3_bucket.razorpay_receipts.id
    content_snapshots = aws_s3_bucket.content_snapshots.id
    cw_logs_archive   = aws_s3_bucket.cw_logs_archive.id
  })
  description = "Task #551 §A — Glacier Deep Archive bucket names. Read by routes/admin_archive.py."

  tags = merge(local.lz_common_tags, {
    Name = "/${local.lz_project}/${local.lz_env}/glacier/archive-buckets"
  })
}
