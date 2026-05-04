# infra/azure/container-registry.tf
#
# Phase 1c — Azure landing zone (Task #329).
#
# Azure Container Registry for the cron-job container images. The
# Phase 4 cron port (downstream task: "Port async workers to AWS
# (SQS+Lambda) and cron jobs to Azure Container Apps") will push the
# `aca-job-*` images defined in §4.3 of ADR-0001 here.
#
# A single multi-tenant repository per environment keeps the credit
# burn low (Basic tier is included in Azure for Startups; Standard
# would only matter once we exceed 100 GB of images, which the cron
# tier will not).
#
# Tag immutability is *not* enforced at the registry level here —
# `azurerm_container_registry` does not expose a global immutable-tag
# toggle, and Azure's repository-level immutability policy is a Premium
# SKU feature. The Phase 4 cron-jobs deploy workflow uses immutable
# digest-pinned tags (`<service>-<git-sha>`) instead, and a follow-up
# task will move the registry to Premium + repository immutability
# policies if/when the cron-jobs surface justifies the SKU bump.

resource "azurerm_container_registry" "cron_obs" {
  name                = "${local.lz_project}cronobsacr" # ACR names: lowercase, alphanumeric, 5–50 chars
  resource_group_name = azurerm_resource_group.cron_obs.name
  location            = azurerm_resource_group.cron_obs.location
  # Standard SKU is required for the Terraform-managed
  # `retention_policy` block below (untagged-manifest cleanup is a
  # Standard/Premium feature). The Azure for Startups credit absorbs
  # the ~$5/mo difference vs Basic; the cost guard in
  # account-billing.tf catches any drift.
  sku           = "Standard"
  admin_enabled = false # admin user disabled — managed identity is the only auth path

  # Untagged manifests older than 14 days are deleted automatically.
  # Tagged images are immutable in practice (deploy workflow uses
  # git-SHA-pinned tags) so this only ever sweeps abandoned layers
  # left behind by failed pushes.
  retention_policy {
    days    = 14
    enabled = true
  }

  tags = local.lz_common_tags
}

# Token-based pull scope-maps are a Premium-only feature. On Basic,
# pull access is granted via the `AcrPull` RBAC role assignment on the
# runtime managed identity (see `cron_runtime_acr_pull` in
# iam-github-oidc.tf). That covers the cron-jobs path; the deploy SP
# uses `AcrPush` separately.

# ─── Outputs ─────────────────────────────────────────────────────────────────

output "acr_login_server" {
  value       = azurerm_container_registry.cron_obs.login_server
  description = "Push URL for cron-job images; consumed by azure-deploy-jobs.yml."
}

output "acr_id" {
  value       = azurerm_container_registry.cron_obs.id
  description = "Resource ID of the ACR (referenced by IAM role assignments)."
}
