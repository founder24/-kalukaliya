# infra/azure/key-vault.tf
#
# Phase 1c — Azure landing zone (Task #329).
#
# Azure Key Vault entries that the cron tier needs at runtime.
# Mirrored from the current Railway / GCP Secret Manager / 1Password
# stores; values are populated out-of-band (1Password → Azure CLI).
# This file only declares the secret containers and their access scope
# — never the plaintext values.
#
# Rotation: each secret has `lifecycle.ignore_changes = [value]` so
# out-of-band rotations do not drift Terraform state. Soft-delete is
# enabled with a 7-day retention to match the AWS Secrets Manager
# recovery window.

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "cron_obs" {
  name                = "${local.lz_project}-cron-obs-kv"
  location            = azurerm_resource_group.cron_obs.location
  resource_group_name = azurerm_resource_group.cron_obs.name
  tenant_id           = data.azurerm_client_config.current.tenant_id

  sku_name = "standard"

  enable_rbac_authorization     = true # use Azure RBAC, not legacy access policies
  purge_protection_enabled      = true
  soft_delete_retention_days    = 7
  public_network_access_enabled = true

  # Network ACLs are intentionally permissive at landing-zone bootstrap
  # so:
  #   (a) the operator running `terraform apply` can create the secret
  #       containers via the Key Vault data plane from anywhere, and
  #   (b) the populate-azure-secrets.sh script can be run from a
  #       laptop / 1Password CLI session without first opening a hole.
  # The cron-jobs subnet service endpoint is still attached so cron
  # jobs reach the vault over the Microsoft backbone. Phase 5 (the
  # observability rewire task) tightens this to
  # `default_action = "Deny"` plus a private endpoint in the
  # private-endpoints subnet — at that point the populate script must
  # be run from a jumpbox inside the VNet or via the AzureServices
  # bypass. This is documented in §6 of the runbook.
  network_acls {
    default_action = "Allow"
    bypass         = "AzureServices"

    virtual_network_subnet_ids = [
      azurerm_subnet.cron_jobs.id,
    ]
  }

  tags = local.lz_common_tags
}

# Operator running `terraform apply` gets Key Vault Secrets Officer —
# enough to create / update / delete *secrets* (data-plane writes) but
# NOT to manage keys, certificates, or vault-level access policies.
# Terraform must never be run by the GitHub deploy SP; this assignment
# binds to the human operator's own Entra object ID. CI is granted
# only `Key Vault Secrets User` via the runtime managed identity in
# iam-github-oidc.tf, and the deploy SP gets nothing on the vault at
# all.
resource "azurerm_role_assignment" "kv_secrets_officer_terraform_runner" {
  scope                = azurerm_key_vault.cron_obs.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# ─── Secret containers ───────────────────────────────────────────────────────

locals {
  # name → human description. The runtime managed identity is granted
  # Key Vault Secrets User at the vault level (see iam-github-oidc.tf);
  # tighten to per-secret role assignments in Phase 4 once each cron
  # job's exact secret list is known.
  lz_cron_secrets = {
    "supabase-service-role-key" = "Supabase service-role key (DB writes from cron jobs)."
    "upstash-redis-rest-token"  = "Upstash Redis REST token (rate-limit + 429 counter)."
    "resend-api-key"            = "Resend API key (digest emails sent from cron jobs)."
    "sentry-dsn-cron"           = "Sentry DSN scoped to the cron-jobs project."
    "axiom-ingest-token"        = "Axiom ingest token (parallel log destination)."
    "slack-ops-webhook"         = "Slack incoming webhook for #infra-alerts."
    "pinecone-api-key"          = "Pinecone API key (embedding refresh cron)."
    "cohere-api-key"            = "Cohere API key (re-rank refresh cron)."
    "mongodb-atlas-uri"         = "MongoDB Atlas connection string (cron Mongo writes)."
    "cf-logpush-shared-secret"  = "Shared secret for CF Logpush → Log Analytics ingestion."
    "vertex-service-account"    = "GCP Vertex service-account JSON (vertex-startup-probe job)."
    "bing-webmaster-api-key"    = "Bing Webmaster API key (bing-keyword-refresh job)."
    "indexnow-key"              = "IndexNow shared key (seo-publish-indexnow job)."
  }
}

resource "azurerm_key_vault_secret" "cron" {
  for_each = local.lz_cron_secrets

  name         = each.key
  key_vault_id = azurerm_key_vault.cron_obs.id

  # Placeholder so `terraform apply` does not leave the secret in a
  # "no current version" state. Cron jobs MUST fail loudly if they
  # ever read this value — never silently fall back. The
  # `_placeholder` sentinel is the enforceable completion gate:
  # `scripts/populate-azure-secrets.sh` (verify pass) exits non-zero
  # if any secret still contains the `set-via-1password-rotation`
  # marker. The Slack post-apply provisioner in observability.tf
  # also greps for the same sentinel and skips wiring cleanly when
  # it is present, so a fresh `terraform apply` succeeds end-to-end
  # before the operator runs the populate script.
  value        = jsonencode({ _placeholder = "set-via-1password-rotation" })
  content_type = "application/json"

  lifecycle {
    ignore_changes = [value, content_type]
  }

  tags = merge(local.lz_common_tags, {
    secret      = each.key
    description = each.value
  })

  depends_on = [azurerm_role_assignment.kv_secrets_officer_terraform_runner]
}

# ─── Outputs ─────────────────────────────────────────────────────────────────

output "key_vault_id" {
  value       = azurerm_key_vault.cron_obs.id
  description = "Resource ID of the cron Key Vault."
}

output "key_vault_uri" {
  value       = azurerm_key_vault.cron_obs.vault_uri
  description = "Vault URI used by Container Apps Jobs Key Vault references."
}

output "cron_secret_ids" {
  value       = { for k, s in azurerm_key_vault_secret.cron : k => s.id }
  description = "Secret resource IDs per logical name; mirror into env-var mapping in the runbook."
}
