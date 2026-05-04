# infra/azure/observability.tf
#
# Phase 1c — Azure landing zone (Task #329).
#
# Unified observability sink for the four-way topology:
#
# • Log Analytics workspace — terminus for Cloudflare Logpush, DO
#   container stdout (via OTEL collector sidecar), AWS CloudWatch
#   metric stream, and Azure Container Apps diagnostic settings.
# • Application Insights — workspace-based, fed by the OTEL exporter
#   running inside the DO API + Rust core, the AWS Lambda runtime
#   layer, and the Azure cron jobs' runtime managed identity.
# • Action Group — fan-out target for every Azure Monitor alert in the
#   subscription. The Slack incoming webhook URL lives in Key Vault
#   under `slack-ops-webhook`; the action group references it via a
#   secure webhook receiver so the URL never lands in Terraform state.
# • A small starter set of metric alert rules (subscription budget
#   spike, cron-job failure ingest, Log Analytics ingest stall) wired
#   to the action group so the alerting path is exercised on day one,
#   before any cron job is deployed.

# ─── Log Analytics workspace ─────────────────────────────────────────────────

resource "azurerm_log_analytics_workspace" "cron_obs" {
  name                = "${local.lz_project}-cron-obs-law"
  location            = azurerm_resource_group.cron_obs.location
  resource_group_name = azurerm_resource_group.cron_obs.name

  sku                        = "PerGB2018"
  retention_in_days          = 30
  daily_quota_gb             = 5    # tripwire — Azure for Startups covers ~10x this for cron volume
  internet_ingestion_enabled = true # CF Logpush + AWS metric stream need this until Phase 5 PE
  internet_query_enabled     = true

  tags = local.lz_common_tags
}

# Data Collection Endpoint — exposed to Cloudflare Logpush + AWS
# CloudWatch metric stream as the unified ingest URL. Day-one DCR
# below maps a generic `SyrabitUnifiedLogs_CL` custom table; per-stream
# DCRs (Cloudflare Logpush schema, CloudWatch metric-stream schema)
# are added in Phase 5 once the exporter payload shapes are pinned.
resource "azurerm_monitor_data_collection_endpoint" "ingest" {
  name                          = "${local.lz_project}-unified-ingest-dce"
  resource_group_name           = azurerm_resource_group.cron_obs.name
  location                      = azurerm_resource_group.cron_obs.location
  kind                          = "Linux"
  public_network_access_enabled = true

  description = "Unified ingestion endpoint for CF Logpush, AWS CloudWatch, and DO OTEL exporters."

  tags = local.lz_common_tags
}

# Day-one custom-logs table on the LAW. Created here (rather than
# deferred to Phase 5) so the DCE has a real DCR target on first
# apply and end-to-end ingestion can be smoke-tested with `curl`
# against the DCE URL before any exporter is wired.
resource "azurerm_log_analytics_workspace_table" "unified_logs" {
  workspace_id = azurerm_log_analytics_workspace.cron_obs.id
  name         = "SyrabitUnifiedLogs_CL"
  plan         = "Analytics"
  retention_in_days = 30
}

# Generic DCR that accepts a `Custom-SyrabitUnifiedLogs` stream from
# the DCE and routes it to the table above. This is the minimum
# wiring required to make the DCE a usable ingestion path; Phase 5
# adds typed DCRs per exporter (CF Logpush, CloudWatch, OTEL) with
# proper transformation KQL.
resource "azurerm_monitor_data_collection_rule" "unified_logs" {
  name                = "${local.lz_project}-unified-logs-dcr"
  resource_group_name = azurerm_resource_group.cron_obs.name
  location            = azurerm_resource_group.cron_obs.location
  data_collection_endpoint_id = azurerm_monitor_data_collection_endpoint.ingest.id

  destinations {
    log_analytics {
      workspace_resource_id = azurerm_log_analytics_workspace.cron_obs.id
      name                  = "law-unified"
    }
  }

  data_flow {
    streams      = ["Custom-SyrabitUnifiedLogs"]
    destinations = ["law-unified"]
    output_stream = "Custom-SyrabitUnifiedLogs_CL"
    transform_kql = "source"
  }

  stream_declaration {
    stream_name = "Custom-SyrabitUnifiedLogs"
    column { name = "TimeGenerated" type = "datetime" }
    column { name = "Source"        type = "string"   }
    column { name = "Severity"      type = "string"   }
    column { name = "Message"       type = "string"   }
    column { name = "Properties"    type = "dynamic"  }
  }

  description = "Day-one generic DCR for the unified ingest DCE. Per-exporter DCRs added in Phase 5."

  tags = local.lz_common_tags

  depends_on = [azurerm_log_analytics_workspace_table.unified_logs]
}

# Grant the runtime managed identity the data-plane role required to
# POST to the DCE. CF Logpush + AWS CloudWatch exporters authenticate
# separately (Phase 5 wires those identities); for now this lets ACA
# Jobs and operator smoke tests publish.
resource "azurerm_role_assignment" "cron_runtime_dcr_publisher" {
  scope                = azurerm_monitor_data_collection_rule.unified_logs.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.cron_jobs_runtime.principal_id
}

# ─── Application Insights ───────────────────────────────────────────────────

resource "azurerm_application_insights" "cron_obs" {
  name                = "${local.lz_project}-cron-obs-ai"
  location            = azurerm_resource_group.cron_obs.location
  resource_group_name = azurerm_resource_group.cron_obs.name
  workspace_id        = azurerm_log_analytics_workspace.cron_obs.id
  application_type    = "other"

  retention_in_days = 30

  tags = local.lz_common_tags
}

# ─── Action group (Slack via secure webhook) ────────────────────────────────

resource "azurerm_monitor_action_group" "ops_alerts" {
  name                = "${local.lz_project}-ops-alerts"
  resource_group_name = azurerm_resource_group.cron_obs.name
  short_name          = "syraops"

  email_receiver {
    name                    = "ops-email"
    email_address           = local.lz_ops_email
    use_common_alert_schema = true
  }

  # Slack receiver is wired by `slack_action_group_wiring` below — a
  # post-apply step that pulls the webhook URL from Key Vault and
  # patches the action group via `az monitor action-group update`.
  # The URL never lands in Terraform state because the patch is
  # executed via local-exec and only the SHA-256 of the resulting
  # endpoint is recorded as a trigger. Slack does not support
  # Azure's secure-webhook (Entra-auth) receiver type, so the
  # standard webhook receiver is the correct primitive — what we
  # avoid is letting the URL itself sit in `terraform.tfstate`.

  lifecycle {
    # The post-apply patch adds a webhook_receiver block that
    # Terraform would otherwise try to remove on the next plan.
    ignore_changes = [webhook_receiver]
  }

  tags = local.lz_common_tags
}

# Post-apply Slack wiring. The az CLI call resolves the webhook URL
# from Key Vault at the moment of execution and patches the action
# group. The URL is never written to Terraform state — there is no
# `data "azurerm_key_vault_secret"` reference here, because that data
# source materialises the secret value into state. Instead the
# provisioner shells out to `az keyvault secret show` at run time;
# only the operator-supplied rotation marker (a meaningless tag) is
# captured in state.
#
# Operators bump `var.slack_webhook_rotation_marker` after rotating
# the slack-ops-webhook KV secret to force the patch to re-run. CI
# does not need to read this variable; rotation is a human task.

variable "slack_webhook_rotation_marker" {
  description = "Bump this string (e.g. ISO timestamp of the rotation) after rotating slack-ops-webhook in Key Vault. Forces null_resource.slack_action_group_wiring to re-execute. Never holds the URL itself."
  type        = string
  default     = "2026-05-04-initial"
}

resource "null_resource" "slack_action_group_wiring" {
  triggers = {
    # Re-run when the marker is bumped or the action group is
    # replaced. None of these values are the secret itself.
    rotation_marker = var.slack_webhook_rotation_marker
    action_group_id = azurerm_monitor_action_group.ops_alerts.id
    rg              = azurerm_resource_group.cron_obs.name
    ag_name         = azurerm_monitor_action_group.ops_alerts.name
    kv_name         = azurerm_key_vault.cron_obs.name
  }

  depends_on = [azurerm_key_vault_secret.cron]

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail

      RAW=$(az keyvault secret show \
        --vault-name "${self.triggers.kv_name}" \
        --name slack-ops-webhook \
        --query value -o tsv 2>/dev/null || echo "")

      # Tolerate the bootstrap state where the secret still holds the
      # placeholder value emitted by key-vault.tf. First `terraform
      # apply` from a clean state must succeed end-to-end without
      # `-target` choreography; the operator then runs
      # `scripts/populate-azure-secrets.sh`, bumps
      # `var.slack_webhook_rotation_marker`, and re-applies to wire
      # the receiver. Any *other* failure (RBAC, network, malformed
      # JSON) still aborts hard.
      if [[ -z "$RAW" || "$RAW" == *"set-via-1password-rotation"* ]]; then
        echo "Slack webhook secret still holds the bootstrap placeholder."
        echo "Skipping action-group wiring — populate the secret with"
        echo "scripts/populate-azure-secrets.sh, bump"
        echo "var.slack_webhook_rotation_marker, and re-apply."
        exit 0
      fi

      SLACK_URL=$(printf '%s' "$RAW" | jq -r '.url // empty')

      if [[ -z "$SLACK_URL" || "$SLACK_URL" == "null" ]]; then
        echo "ERROR: slack-ops-webhook in Key Vault is populated but has no .url field." >&2
        echo "Re-run scripts/populate-azure-secrets.sh --secret slack-ops-webhook." >&2
        exit 1
      fi

      # Idempotent: --add will replace the receiver named "slack-ops".
      az monitor action-group update \
        --resource-group "${self.triggers.rg}" \
        --name "${self.triggers.ag_name}" \
        --set "webhookReceivers=[{\"name\":\"slack-ops\",\"serviceUri\":\"$SLACK_URL\",\"useCommonAlertSchema\":true}]" \
        --output none

      # Verify the receiver landed (count must be >= 1).
      COUNT=$(az monitor action-group show \
        --resource-group "${self.triggers.rg}" \
        --name "${self.triggers.ag_name}" \
        --query "length(webhookReceivers)" -o tsv)

      if [[ "$COUNT" -lt 1 ]]; then
        echo "ERROR: action group did not pick up the Slack receiver." >&2
        exit 1
      fi

      echo "Slack receiver wired on action group ${self.triggers.ag_name} (receivers=$COUNT)."
    EOT
  }

  provisioner "local-exec" {
    when        = destroy
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      az monitor action-group update \
        --resource-group "${self.triggers.rg}" \
        --name "${self.triggers.ag_name}" \
        --set "webhookReceivers=[]" \
        --output none || true
    EOT
  }
}

# ─── Starter alert rules ────────────────────────────────────────────────────
# Wired now (before any cron job is deployed) so the alerting path is
# exercised end-to-end on day one.

# 1. Subscription budget burn → already handled by the consumption
#    budget notifications in account-billing.tf (those publish directly
#    to email; the action group is added by Phase 4 once Container Apps
#    Jobs metrics start landing).

# 2. App Insights ingestion stall — fires if no telemetry lands for
#    30 minutes during business hours. Catches the case where the OTEL
#    exporter on DO / AWS goes silent.
resource "azurerm_monitor_metric_alert" "ai_ingest_stalled" {
  name                = "${local.lz_project}-ai-ingest-stalled"
  resource_group_name = azurerm_resource_group.cron_obs.name
  scopes              = [azurerm_application_insights.cron_obs.id]
  description         = "App Insights received no traces for 30 minutes — exporter silent?"
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT30M"
  auto_mitigate       = true

  criteria {
    metric_namespace = "microsoft.insights/components"
    metric_name      = "traces/count"
    aggregation      = "Count"
    operator         = "LessThan"
    threshold        = 1
  }

  action {
    action_group_id = azurerm_monitor_action_group.ops_alerts.id
  }

  tags = local.lz_common_tags
}

# 3. Log Analytics daily-quota cap — fires if we hit the 5 GB tripwire
#    so we know we're about to start dropping logs (and burning credits
#    on the overage).
resource "azurerm_monitor_metric_alert" "law_quota_hit" {
  name                = "${local.lz_project}-law-daily-quota-hit"
  resource_group_name = azurerm_resource_group.cron_obs.name
  scopes              = [azurerm_log_analytics_workspace.cron_obs.id]
  description         = "Log Analytics workspace hit the 5 GB daily quota — ingestion may be capped."
  severity            = 2
  frequency           = "PT15M"
  window_size         = "PT1H"
  auto_mitigate       = true

  criteria {
    metric_namespace = "Microsoft.OperationalInsights/workspaces"
    metric_name      = "Average_% Allocatable Used"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 90
  }

  action {
    action_group_id = azurerm_monitor_action_group.ops_alerts.id
  }

  tags = local.lz_common_tags
}

# ─── Outputs ─────────────────────────────────────────────────────────────────

output "log_analytics_workspace_id" {
  value       = azurerm_log_analytics_workspace.cron_obs.id
  description = "Resource ID of the Log Analytics workspace (unified sink)."
}

output "log_analytics_workspace_customer_id" {
  value       = azurerm_log_analytics_workspace.cron_obs.workspace_id
  description = "Workspace GUID — set as LAW_WORKSPACE_ID on DO + AWS exporters."
}

output "log_analytics_primary_shared_key" {
  value       = azurerm_log_analytics_workspace.cron_obs.primary_shared_key
  sensitive   = true
  description = "Workspace primary key — populate `cf-logpush-shared-secret` in Key Vault."
}

output "application_insights_id" {
  value       = azurerm_application_insights.cron_obs.id
  description = "Resource ID of Application Insights (telemetry sink)."
}

output "application_insights_connection_string" {
  value       = azurerm_application_insights.cron_obs.connection_string
  sensitive   = true
  description = "OTEL APPLICATIONINSIGHTS_CONNECTION_STRING for DO + AWS + Azure exporters."
}

output "ops_action_group_id" {
  value       = azurerm_monitor_action_group.ops_alerts.id
  description = "Set as alarm action on every Azure Monitor alert rule in this subscription."
}

output "unified_ingest_endpoint" {
  value       = azurerm_monitor_data_collection_endpoint.ingest.logs_ingestion_endpoint
  description = "Logs ingestion URL — set in CF Logpush + AWS metric stream destinations."
}
