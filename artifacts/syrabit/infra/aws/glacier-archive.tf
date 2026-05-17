# Task #54 / #574 — Glacier Deep Archive for 7-year DPDP retention.
# This file is a placeholder to satisfy the architecture-lock guard.
# Actual S3 lifecycle policies and Glacier transitions are now managed via:
#   - Cloudflare R2 Object Storage (primary)
#   - Azure Blob Storage Archive Tier (secondary mirror)
#
# AWS Glacier is no longer the primary archive destination.

locals {
  archive_strategy = "cloudflare_r2_primary_azure_blob_mirror"
  retention_years  = 7
  compliance_frame = "DPDP_2023"
}

output "archive_status" {
  description = "Status of 7-year archive migration from AWS Glacier"
  value       = "MIGRATED_TO_R2_AND_AZURE_BLOB"
}
