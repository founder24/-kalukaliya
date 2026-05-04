# infra/azure/iam-github-oidc.tf
#
# Phase 1c — Azure landing zone (Task #329).
#
# Identity baseline for the Azure landing zone:
#
# 1. GitHub OIDC federated credential — lets the
#    `azure-deploy-jobs.yml` workflow in the syrabit GitHub repo assume
#    an Azure service principal without a long-lived client secret.
# 2. Deploy service principal — what GitHub Actions assumes; scoped to
#    the resources the cron-jobs deploy actually touches (ACR push,
#    Container Apps Jobs revisions, Log Analytics workspace reads for
#    deploy verification).
# 3. Runtime managed identity — what the Container Apps Jobs assume at
#    run time; scoped to the specific Key Vault secrets, ACR pull, and
#    App Insights ingestion the cron tier uses.
#
# Roles are intentionally split so a compromised CI runner cannot read
# application secrets, and a compromised cron job cannot redeploy
# itself.

variable "github_owner" {
  description = "GitHub org/user that owns the syrabit repo."
  type        = string
  default     = "syrabit"
}

variable "github_repo" {
  description = "GitHub repository name (without org)."
  type        = string
  default     = "syrabit"
}

# ─── 1. Deploy service principal + GitHub OIDC federated credential ──────────

resource "azuread_application" "github_deploy" {
  display_name = "${local.lz_project}-github-deploy"
  description  = "Assumed by azure-deploy-jobs.yml GitHub Actions workflow."
}

resource "azuread_service_principal" "github_deploy" {
  client_id = azuread_application.github_deploy.client_id

  feature_tags {
    enterprise = false
    gallery    = false
  }
}

# One federated credential per allowed GitHub ref. Azure does not allow
# wildcard `subject` matching the way AWS IAM does, so we declare each
# allowed source explicitly.
locals {
  lz_github_federated_subjects = {
    "master"     = "repo:${var.github_owner}/${var.github_repo}:ref:refs/heads/master"
    "env-prod"   = "repo:${var.github_owner}/${var.github_repo}:environment:prod"
    "env-stage"  = "repo:${var.github_owner}/${var.github_repo}:environment:staging"
  }
}

resource "azuread_application_federated_identity_credential" "github" {
  for_each = local.lz_github_federated_subjects

  application_id = azuread_application.github_deploy.id
  display_name   = "github-${each.key}"
  description    = "GitHub OIDC trust for ${each.value}"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  subject        = each.value
}

# Deploy SP gets Contributor on the cron-obs RG only — it cannot touch
# the front-door / cosmos RG or any other subscription resource.
resource "azurerm_role_assignment" "github_deploy_rg_contributor" {
  scope                = azurerm_resource_group.cron_obs.id
  role_definition_name = "Contributor"
  principal_id         = azuread_service_principal.github_deploy.object_id
}

# ACR push for the cron-job container images.
resource "azurerm_role_assignment" "github_deploy_acr_push" {
  scope                = azurerm_container_registry.cron_obs.id
  role_definition_name = "AcrPush"
  principal_id         = azuread_service_principal.github_deploy.object_id
}

# Log Analytics read for deploy verification (querying the most recent
# job execution log without granting blanket workspace contributor).
resource "azurerm_role_assignment" "github_deploy_logs_reader" {
  scope                = azurerm_log_analytics_workspace.cron_obs.id
  role_definition_name = "Log Analytics Reader"
  principal_id         = azuread_service_principal.github_deploy.object_id
}

# ─── 2. Runtime managed identity (assumed by Container Apps Jobs) ────────────

resource "azurerm_user_assigned_identity" "cron_jobs_runtime" {
  name                = "${local.lz_project}-cron-jobs-runtime"
  resource_group_name = azurerm_resource_group.cron_obs.name
  location            = azurerm_resource_group.cron_obs.location

  tags = local.lz_common_tags
}

# Pull cron-job images from ACR (no push).
resource "azurerm_role_assignment" "cron_runtime_acr_pull" {
  scope                = azurerm_container_registry.cron_obs.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.cron_jobs_runtime.principal_id
}

# Read secrets from Key Vault (no list/write — cron jobs name the
# secret they need). Granular per-secret RBAC is enforced by the access
# policy in key-vault.tf.
resource "azurerm_role_assignment" "cron_runtime_kv_user" {
  scope                = azurerm_key_vault.cron_obs.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.cron_jobs_runtime.principal_id
}

# Publish telemetry to Application Insights / Log Analytics. Monitoring
# Metrics Publisher is the minimum role required to write custom
# metrics + traces via the OTEL exporter without granting workspace
# write access at large.
resource "azurerm_role_assignment" "cron_runtime_metrics_publisher" {
  scope                = azurerm_application_insights.cron_obs.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.cron_jobs_runtime.principal_id
}

# ─── Outputs ─────────────────────────────────────────────────────────────────

output "github_deploy_client_id" {
  value       = azuread_application.github_deploy.client_id
  description = "Set as AZURE_CLIENT_ID secret in the syrabit repo's GitHub env."
}

output "github_deploy_tenant_id" {
  value       = data.azurerm_subscription.current.tenant_id
  description = "Set as AZURE_TENANT_ID secret in the syrabit repo's GitHub env."
}

output "github_deploy_subscription_id" {
  value       = data.azurerm_subscription.current.subscription_id
  description = "Set as AZURE_SUBSCRIPTION_ID secret in the syrabit repo's GitHub env."
}

output "cron_jobs_runtime_identity_id" {
  value       = azurerm_user_assigned_identity.cron_jobs_runtime.id
  description = "Pass to Container Apps Job definitions in Phase 4."
}

output "cron_jobs_runtime_client_id" {
  value       = azurerm_user_assigned_identity.cron_jobs_runtime.client_id
  description = "Client ID of the runtime managed identity (used by Key Vault references)."
}
