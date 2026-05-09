# Azure Landing Zone Runbook — Cron Jobs & Observability Sink

> ⚠️ **V4 cross-reference (2026-05-06).** The locked source of truth for the
> overall Syrabit architecture is [`infra/v4-locked-architecture.md`](../../../../infra/v4-locked-architecture.md).
> If anything below disagrees with V4, V4 wins. This doc is preserved as the
> operator runbook for the Azure landing zone — the live HTTP backend on
> ACA `syrabit-backend` (`eastus2`) is an explicit accepted SPOF per V4 §8.
> Regions, namespaces, providers, and failover semantics are governed by V4.

**Status:** Live (Phase 1c of ADR-0001)
**Owner:** infra
**Task:** #329
**Companion:** [`ADR-0001-four-way-hosting-rebalance.md`](ADR-0001-four-way-hosting-rebalance.md), [`aws-landing-zone.md`](aws-landing-zone.md), [`provider-credit-matrix.md`](provider-credit-matrix.md)
**Terraform root:** [`../../infra/azure/`](../../infra/azure/)

---

## 1. What this subscription hosts

This Azure subscription is the foundation for two surfaces in the
four-way split:

- **Cron / scheduled jobs** — every Cloud Scheduler job from
  `inventory/cloud-scheduler.json` and every `aca-job`-classified loop
  from `inventory/asyncio-loops.md` (38 loops) lands on Azure
  Container Apps Jobs.
- **Unified observability sink** — Log Analytics workspace +
  Application Insights, fed by Cloudflare Logpush, DO container OTEL
  exporters, AWS CloudWatch metric stream, and Azure-native diagnostic
  settings. Sentry, PostHog, and Axiom remain as parallel destinations.

It is **not** the home of:

- The synchronous API tier (FastAPI) or the Rust core — both go to
  Azure Container Apps.
- Async fan-out queues / workers — those go to AWS (SQS + Lambda).
- Azure Cache for Redis — Upstash stays as the Redis surface.
- Production DNS — Cloudflare keeps the apex and `api.syrabit.ai`.

The pre-existing **Azure Front Door** + **Cosmos DB cache** resources
(`infra/azure/front-door.tf`, `cosmos-db-cache.tf`) live in a separate
resource group (`syrabit-prod-rg`) and are not part of this landing
zone — they predate the four-way rebalance and stay untouched.

## 2. Subscription & regions

| Item                       | Value                                                  |
|----------------------------|--------------------------------------------------------|
| Tenant ID                  | _populated post-apply; see `terraform output`_         |
| Subscription ID            | _populated post-apply; see `terraform output`_         |
| Subscription display name  | `syrabit-prod`                                         |
| Billing contact            | `ops@syrabit.ai`                                       |
| Azure for Startups credits | $2 500 (applied 2026-04; balance tracked in [`provider-credit-matrix.md`](provider-credit-matrix.md)) |
| Primary region             | `centralindia` (closest to majority of users)          |
| Secondary / DR region      | `eastasia` (documented; not actively replicated)       |
| Resource group             | `syrabit-cron-obs-rg`                                  |
| Monthly cost budget        | $200 (50 % actual / 80 % forecast / 100 % actual → email alert) |

The DR region is documented but not actively replicated to today.
Cross-region replication of the Log Analytics workspace is deferred to
Phase 5 once real ingest volume is known.

## 3. Network baseline (`network.tf`)

```
VNet syrabit-cron-obs-vnet     10.50.0.0/16   (centralindia)
├── cron-jobs-subnet            10.50.0.0/23   (Container Apps env, /23 minimum)
└── private-endpoints-subnet    10.50.4.0/24   (KV / ACR / AI private link, Phase 5)
```

- Cron-jobs subnet is delegated to `Microsoft.App/environments` so the
  Container Apps environment provisioned in Phase 4 can attach.
- Service endpoints for Key Vault, Container Registry, and Storage are
  enabled on the cron-jobs subnet so jobs reach those services over
  the Microsoft backbone.
- NSGs:
  - `syrabit-cron-jobs-nsg` — no inbound; outbound 443 + DNS only;
    explicit deny-all-outbound at priority 4096 below the allow rules.
  - `syrabit-private-endpoints-nsg` — 443 inbound from `10.50.0.0/23`
    only.

Outputs (consumed by Phase 4 Terraform):
`cron_obs_vnet_id`, `cron_jobs_subnet_id`, `private_endpoints_subnet_id`.

## 4. Identity (`iam-github-oidc.tf`)

Three things live here, deliberately split:

1. **GitHub OIDC federated credentials** — one credential per allowed
   subject. Azure does not allow wildcard `subject` matching, so each
   ref is declared explicitly:
   - `repo:syrabit/syrabit:ref:refs/heads/master`
   - `repo:syrabit/syrabit:environment:prod`
   - `repo:syrabit/syrabit:environment:staging`
2. **`syrabit-github-deploy` service principal** — assumed by the
   `azure-deploy-jobs.yml` workflow. RBAC scope:
   - `Contributor` on the cron-obs RG (NOT subscription-wide).
   - `AcrPush` on the cron-obs ACR.
   - `Log Analytics Reader` on the workspace.
3. **`syrabit-cron-jobs-runtime` user-assigned managed identity** —
   attached to every Container Apps Job in Phase 4. RBAC scope:
   - `AcrPull` on the cron-obs ACR.
   - `Key Vault Secrets User` on the cron-obs Key Vault.
   - `Monitoring Metrics Publisher` on Application Insights.

A compromised CI runner cannot read application secrets; a compromised
cron job cannot redeploy itself or push images.

### GitHub setup steps

```bash
# After `terraform apply`, copy the deploy SP coordinates into the
# repo's GH env.
gh secret set AZURE_CLIENT_ID       --env prod --body "$(terraform output -raw github_deploy_client_id)"
gh secret set AZURE_TENANT_ID       --env prod --body "$(terraform output -raw github_deploy_tenant_id)"
gh secret set AZURE_SUBSCRIPTION_ID --env prod --body "$(terraform output -raw github_deploy_subscription_id)"
```

The workflow then logs in via OIDC:

```yaml
permissions:
  id-token: write
  contents: read
- uses: azure/login@v2
  with:
    client-id:       ${{ secrets.AZURE_CLIENT_ID }}
    tenant-id:       ${{ secrets.AZURE_TENANT_ID }}
    subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

## 5. Container registry (`container-registry.tf`)

| Item             | Value                                       |
|------------------|---------------------------------------------|
| Registry name    | `syrabitcronobsacr`                         |
| Login server     | `syrabitcronobsacr.azurecr.io` (output)     |
| SKU              | `Standard` (required for Terraform-managed retention)                       |
| Admin user       | Disabled — managed identity is the only auth path                          |
| Retention policy | 14 days for untagged manifests (Terraform-managed `retention_policy` block) |

The `aca-job-*` images defined in §4.3 of ADR-0001 are pushed here in
Phase 4. Tag immutability is **not** enforced at the registry level
(repository immutability policies require the Premium SKU); the
Phase 4 cron-jobs deploy workflow uses git-SHA-pinned tags
(`<service>-<git-sha>`) and pulls by digest so a re-pushed tag can
never silently swap a running revision's image. A follow-up task
will revisit Premium + repository immutability if the cron tier
grows past a handful of images.

## 6. Secrets (`key-vault.tf`)

All secrets live in the `syrabit-cron-obs-kv` Key Vault, RBAC-only
(legacy access policies disabled). Plaintext values are populated
**out of band** from 1Password — Terraform only declares the secret
container and a `_placeholder` initial value.
`lifecycle.ignore_changes` on `value` means rotations don't drift
state.

| Secret name                  | Env var in cron jobs        | Source of truth          |
|------------------------------|-----------------------------|--------------------------|
| `supabase-service-role-key`  | `SUPABASE_SERVICE_ROLE_KEY` | 1Password `Supabase`     |
| `upstash-redis-rest-token`   | `UPSTASH_REDIS_REST_TOKEN`  | 1Password `Upstash`      |
| `sentry-dsn-cron`            | `SENTRY_DSN`                | 1Password `Sentry`       |
| `axiom-ingest-token`         | `AXIOM_INGEST_TOKEN`        | 1Password `Axiom`        |
| `slack-ops-webhook`          | (action group; see §7)      | 1Password `Slack`        |
| `pinecone-api-key`           | `PINECONE_API_KEY`          | 1Password `Pinecone`     |
| `cohere-api-key`             | `COHERE_API_KEY`            | 1Password `Cohere`       |
| `mongodb-atlas-uri`          | `MONGODB_URI`               | 1Password `MongoDB Atlas`|
| `cf-logpush-shared-secret`   | (CF Logpush config)         | LAW primary shared key   |
| `vertex-service-account`     | `GOOGLE_APPLICATION_CREDENTIALS_JSON` | 1Password `GCP Vertex SA` |
| `bing-webmaster-api-key`     | `BING_WEBMASTER_API_KEY`    | 1Password `Bing Webmaster` |
| `indexnow-key`               | `INDEXNOW_KEY`              | 1Password `IndexNow`     |

Resource IDs are emitted by the `cron_secret_ids` Terraform output.

### Populating secrets

The landing zone is **not considered live** until every secret in the
manifest has been populated from 1Password. Run the bootstrap script
from an operator laptop with `az login` + `op signin` active:

```bash
./scripts/populate-azure-secrets.sh
```

The script reads each entry from the in-file manifest (one row per
Key Vault secret name → 1Password URI) and pushes the value via
`az keyvault secret set`. It then runs a verification pass that
fails loudly if any secret still holds the `_placeholder` sentinel
that Terraform initialised it with — this is the **enforceable
completion check** for the "Key Vault populated" requirement of
Task #329.

Re-verify at any time without writing:

```bash
./scripts/populate-azure-secrets.sh --verify-only
```

Rotate a single secret:

```bash
./scripts/populate-azure-secrets.sh --secret sentry-dsn-cron
```

Network ACL note: the Key Vault is created with
`network_acls.default_action = "Allow"` so the bootstrap script can
run from any operator machine. Phase 5 (the observability rewire
task) tightens this to `Deny` + a private endpoint in the
`private-endpoints` subnet — at that point the populate script must
be re-run from a jumpbox inside the VNet, or the operator's IP must
be added to `network_acls.ip_rules` for the duration of the rotation.

### Container Apps Job reference

Cron jobs reference these via Key Vault references in their secret
spec — the runtime managed identity resolves them at job-start:

```yaml
secrets:
  - name: sentry-dsn-cron
    keyVaultUrl: https://syrabit-cron-obs-kv.vault.azure.net/secrets/sentry-dsn-cron
    identity: /subscriptions/<sub>/resourceGroups/syrabit-cron-obs-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/syrabit-cron-jobs-runtime
```

Note (Task #556 retirement): the previous third-party transactional email
vendor secret has been retired from this Key Vault — Amazon SES is the
sole transactional path and reads its credentials from the existing
`AWS_*` mirrors. Do not re-add the legacy secret without a V5 spec
change.

### Rotation

90-day reminder per secret (calendar invite owned by infra). Rotate
via `az keyvault secret set`; cron jobs pick up the new value on the
next job-start because Key Vault references resolve fresh every run
(no in-process cache).

If a secret is suspected leaked: rotate first, **then** disable the
old version with `az keyvault secret set-attributes --enabled false`.

## 7. Observability (`observability.tf`)

- **Log Analytics workspace** `syrabit-cron-obs-law`
  (PerGB2018, 30-day retention, 5 GB/day quota). Terminus for
  Cloudflare Logpush, DO OTEL stdout, AWS CloudWatch metric stream,
  and Azure diagnostic settings.
- **Application Insights** `syrabit-cron-obs-ai` (workspace-based on
  the LAW above). Fed by the OTEL exporter in DO + AWS + Azure
  workloads.
- **Data Collection Endpoint** `syrabit-unified-ingest-dce` plus a
  day-one `syrabit-unified-logs-dcr` Data Collection Rule that routes
  a generic `Custom-SyrabitUnifiedLogs` stream to the
  `SyrabitUnifiedLogs_CL` table on the LAW. This makes the DCE
  immediately usable — operators and ACA Jobs can POST to the
  endpoint and see rows in LAW today, and the runtime managed
  identity has the `Monitoring Metrics Publisher` role on the DCR.
  Phase 5 layers on per-exporter DCRs (CF Logpush schema, CloudWatch
  metric-stream schema, OTEL collector) without touching the day-one
  resources, so the cutover from the GCP sink can stage cleanly.
- **Primary application telemetry path** is App Insights via the
  workspace-based connection string output (`app_insights_connection_string`).
  DO + AWS + Azure workloads point their OTEL exporters at this
  string today; that path is fully functional on landing-zone apply
  and does not depend on the DCE/DCR.
- **Action group** `syrabit-ops-alerts` — fan-out target for every
  Azure Monitor alert. Email → `ops@syrabit.ai`; Slack via the
  `null_resource.slack_action_group_wiring` post-apply step in
  `observability.tf` (see below).
- **Starter alert rules** wired to the action group on day one:
  - `syrabit-ai-ingest-stalled` — fires if App Insights receives no
    traces for 30 minutes.
  - `syrabit-law-daily-quota-hit` — fires when the LAW workspace hits
    90 % of the 5 GB daily quota.

### Wiring DO + AWS exporters

```bash
# DO API + Rust core OTEL collector (sidecar):
APPLICATIONINSIGHTS_CONNECTION_STRING="$(terraform output -raw application_insights_connection_string)"

# CF Logpush job destination (one-time setup via the CF API):
LAW_WORKSPACE_ID="$(terraform output -raw log_analytics_workspace_customer_id)"
LAW_SHARED_KEY="$(terraform output -raw log_analytics_primary_shared_key)"
INGEST_URL="$(terraform output -raw unified_ingest_endpoint)"

# AWS CloudWatch metric stream destination:
#   - Use the Firehose → HTTP destination pattern with INGEST_URL above.
#   - Auth header is the LAW shared key (mirrored into KV as
#     `cf-logpush-shared-secret` for rotation tracking).
```

### Slack action-group wiring (Terraform-managed, URL not in state)

The Slack receiver is provisioned by the
`null_resource.slack_action_group_wiring` block in
`observability.tf`. It runs automatically on every `terraform apply`
that follows a successful population of `slack-ops-webhook` in Key
Vault. Mechanics:

1. The `azurerm_monitor_action_group` resource declares only the
   email receiver and uses `lifecycle.ignore_changes =
   [webhook_receiver]` so Terraform does not fight the post-apply
   patch.
2. The `null_resource.slack_action_group_wiring` resource has **no**
   `data "azurerm_key_vault_secret"` reference — that data source
   would materialise the secret value into Terraform state. Instead
   the `local-exec` provisioner shells out to `az keyvault secret
   show` at run time so the URL only ever exists in the operator's
   shell environment, never in `terraform.tfstate`.
3. The provisioner patches the action group with `az monitor
   action-group update` and verifies the receiver count is ≥ 1.
   After rotating the `slack-ops-webhook` secret in Key Vault, bump
   `var.slack_webhook_rotation_marker` (e.g. to today's ISO date)
   and re-apply — that variable is the only thing that lives in
   state, and it is just an opaque tag.
4. The `destroy` provisioner clears the receiver list on teardown so
   the URL does not linger on a half-destroyed action group.

Verification:

```bash
# After `terraform apply`, confirm the Slack receiver landed and
# the URL is NOT in state.
az monitor action-group show \
  --resource-group syrabit-cron-obs-rg \
  --name syrabit-ops-alerts \
  --query "{email: emailReceivers[].emailAddress, slack: webhookReceivers[].name}"

terraform state pull | jq -r 'recurse | strings' | grep -F 'hooks.slack.com' \
  && { echo "FAIL: Slack URL leaked into state"; exit 1; } \
  || echo "OK: Slack URL is not in Terraform state."
```

End-to-end test (do this once at landing-zone bring-up):

```bash
# Force the AI-ingest-stalled rule to fire by pausing telemetry in
# staging for >30 min, or send a synthetic alert directly to the
# action group:
az monitor action-group test-notifications create \
  --resource-group syrabit-cron-obs-rg \
  --action-group-name syrabit-ops-alerts \
  --notification-type Webhook \
  --alert-type metricstaticthreshold
# → expect the alert in #infra-alerts within ~60 s.
```

## 8. Apply order

A clean first bring-up is a single `terraform apply`. The Slack
post-apply patch in `observability.tf` is **placeholder-tolerant**:
if `slack-ops-webhook` still holds the bootstrap sentinel, the
provisioner logs a skip message and exits 0 so the foundation lands
without `-target` choreography. The Slack receiver is then wired by a
trivial second apply once the secret is populated.

```bash
cd artifacts/syrabit/infra/azure
terraform init

# Step 1 — full apply. Lands every resource. The Slack wiring
# null_resource sees the placeholder secret and skips cleanly.
terraform apply

# Step 2 — populate every cron-tier secret from 1Password and verify.
../../../../scripts/populate-azure-secrets.sh

# Step 3 — bump the rotation marker and re-apply to wire Slack.
# (Only required the first time and after each Slack URL rotation.)
terraform apply -var "slack_webhook_rotation_marker=$(date -u +%Y-%m-%dT%H:%MZ)"
```

If the provisioner ever errors with "slack-ops-webhook in Key Vault
is populated but has no .url field", the 1Password entry is missing
the `url` key — fix it, re-run
`scripts/populate-azure-secrets.sh --secret slack-ops-webhook`, and
re-apply.

## 9. Access

- **Portal:** Microsoft Entra ID SSO via `syrabit.onmicrosoft.com` →
  `Owner` (humans, locked behind Conditional Access + MFA).
- **CLI:** `az login --tenant syrabit.onmicrosoft.com`. Default
  subscription:
  ```bash
  az account set --subscription syrabit-prod
  ```
- **CI:** GitHub Actions only, via the OIDC federated credential in
  §4. No long-lived Azure client secrets exist in this subscription.

## 10. What's intentionally not here

| Thing                                   | Lives where                                |
|-----------------------------------------|--------------------------------------------|
| Container Apps environment + cron jobs  | Phase 4 Terraform (downstream task)        |
| Azure Cache for Redis                   | Upstash (unchanged)                        |
| API ingress, Rust core gRPC             | Azure Container Apps                       |
| Async workers / SQS                     | AWS                                        |
| Azure Front Door, Cosmos DB cache       | `front-door.tf` / `cosmos-db-cache.tf` (predate this LZ; separate RG) |
| Azure OpenAI / AI Speech / Translator   | "Azure-native advanced features" task      |
| Production DNS apex (`syrabit.ai`)      | Cloudflare                                 |
| OCR scratch storage                     | **Not persisted** — OCR is in-memory Vertex Vision round-trip (`routes/ai_chat.py /ai/ocr-image`, `routes/pyq.py /pyq/process`). Warm media lives on Cloudflare R2 (`r2_storage.py`). See `docs/architecture/decisions.md` Task #46. |

## 11. Decommission notes

If this landing zone ever has to be torn down:

1. Drain every Container Apps Job to "no running replicas"; delete
   the jobs first.
2. `terraform destroy` in reverse-dependency order. Key Vault has
   `purge_protection_enabled = true` — purge requires a separate
   `az keyvault purge` call after destroy and a 7-day soft-delete
   window.
3. Revoke the GitHub OIDC federated credentials **before** deleting
   the service principal (so any in-flight workflow run fails fast
   instead of silently using a stale credential).
4. Cancel the Azure for Startups credit assignment with Microsoft
   support so the subscription doesn't leave a credit balance
   unmanaged.
