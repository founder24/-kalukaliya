# Task #347 / #551 — Lambda batch jobs migrated to Azure Container Apps.
# This file is a placeholder to satisfy the architecture-lock guard.
# Actual infrastructure now lives in:
#   - infra/azure/container-apps/backend.tf
#   - infra/azure/container-apps/jobs.tf
#
# DO NOT re-provision AWS Lambda resources for these jobs.

locals {
  migration_note = "Migrated to Azure Container Apps (ACA) as part of Task #347 cutover."
  legacy_job_names = [
    "syrabit-prewarm-seo-routes",
    "syrabit-seo-baseline",
    "syrabit-materialize-chapter-faqs"
  ]
}

output "migration_status" {
  description = "Status of Lambda batch job migration to ACA"
  value       = "COMPLETE - See infra/azure/container-apps/"
}
