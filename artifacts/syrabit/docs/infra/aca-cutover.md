# Azure Container Apps cutover runbook (Task #347)

This runbook walks an operator through cutting the Syrabit FastAPI
backend over from the legacy DigitalOcean App Platform onto Azure Container Apps. (DigitalOcean files purged from the repo on 2026-05-06; this runbook is preserved as the historical cutover record.)
The motivation is co-location with the Azure OpenAI primary so every
chat request stops paying the cross-cloud egress + RTT it currently
pays from `blr1` → Azure `eastus`.

The Bicep template (`infra/azure/aca-syrabit-backend.bicep`) and the
`.github/workflows/azure-container-apps-deploy.yml` workflow are the
two artifacts CI uses; this doc is the human-readable runbook.

---

## 0b. Resume — finish the half-deployed Task #400 cutover

> Status as of 2026-05-05: an earlier autonomous attempt provisioned **all
> Azure base infra + Container App shell** but could not finish the image
> build/deploy from inside the Replit sandbox because of three independent
> Azure control-plane restrictions (see "Why this section exists" below).
>
> If you're restarting from a clean slate, skip to **section 0a** instead.
> This section picks up from the half-deployed state.

### What is already provisioned

| Resource                                                          | Name                                                                       | State      |
| ----------------------------------------------------------------- | -------------------------------------------------------------------------- | ---------- |
| Resource group (`eastus`, **not** eastus2 — co-located with AOAI) | `syrabit-prod`                                                             | Succeeded  |
| Log Analytics workspace                                           | `syrabit-prod-law`                                                         | Succeeded  |
| Azure Container Registry (Standard SKU, admin user **enabled**)   | `syrabitacr`                                                               | Succeeded  |
| Key Vault (RBAC mode, 40 secrets seeded)                          | `syrabit-prod-kv`                                                          | Succeeded  |
| ACA managed environment                                           | `syrabit-aca-env`                                                          | Succeeded  |
| Container App (placeholder hello-world image, system identity)    | `syrabit-backend`                                                          | Succeeded  |
| ACA FQDN (where DNS will eventually point)                        | `syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io`         | live       |

Role assignments already in place:

* SP → KV: `Key Vault Secrets Officer` (so CI can rotate secrets later).
* ACA managed identity → ACR: `AcrPull` (so the next image pulls succeed).
* ACA managed identity → KV: `Key Vault Secrets User` (so KV refs resolve at runtime).

### Why this section exists (and what it needs from you)

The autonomous attempt hit three blockers, **any one** of which the operator
can resolve to unblock the rest:

1. **ACR Tasks blocked at the subscription level** —
   `scheduleRun` returned `TasksOperationsNotAllowed`. This is a default
   restriction on new Pay-As-You-Go subscriptions. Filing an Azure support
   request unlocks it (free, ~24 h SLA).

2. **Service-principal lacks Graph `Application.ReadWrite.OwnedBy`** —
   without it the SP cannot add a federated credential to its own App
   Registration, which is what the existing
   `azure-container-apps-deploy.yml` GH workflow uses for OIDC login.
   Add it once in Azure Portal → App registrations → that app → API
   permissions → Microsoft Graph → Application → grant admin consent.

3. **No rootless container build tool in the Replit sandbox** —
   `docker` binary is present but the daemon needs root, so a local
   `docker buildx build --push` is not possible from this environment.

### Five-minute resume — recommended path (option B above)

In Azure Portal: App registrations → "syrabit-deploy" (or whatever name
holds `AZURE_CLIENT_ID`) → **Certificates & secrets** → **Federated
credentials** → **Add credential** →

* Scenario: **GitHub Actions deploying Azure resources**
* Organisation: `founder24`
* Repository: `-kalukaliya`
* Entity: **Branch**
* Branch: `main` (the repo's default branch)
* Name: `github-main`

Then from any shell with `gh` (GitHub CLI) authenticated to this repo:

```bash
gh secret set AZURE_CLIENT_ID       --body "$AZURE_CLIENT_ID"
gh secret set AZURE_TENANT_ID       --body "$AZURE_TENANT_ID"
gh secret set AZURE_SUBSCRIPTION_ID --body "$AZURE_SUBSCRIPTION_ID"
gh variable set AZURE_RESOURCE_GROUP --body "syrabit-prod"
gh variable set AZURE_ACR_NAME       --body "syrabitacr"
gh variable set AZURE_ACA_ENV        --body "syrabit-aca-env"

gh workflow run azure-container-apps-deploy.yml \
    --ref main \
    -f app=syrabit-backend \
    -f mode=deploy \
    -f health_url=https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io/health
```

The workflow will build the image with `docker buildx`, push to
`syrabitacr.azurecr.io/syrabit/backend:<tag>`, then `az containerapp
update --image …` swaps the placeholder for the real image.

### After the workflow goes green — finish the cutover

The workflow only ships the image. The 40 KV-backed secrets and 27 plain
env vars **still need to be wired onto the Container App**. Use the
`az containerapp secret set` + `az containerapp update --set-env-vars`
helpers. The full classified list lives in the bicep template under
`secrets:` and `env:` (see `infra/azure/aca-syrabit-backend.bicep`).

Final two steps:

```bash
# Verify the embed stack is wired correctly
curl -fsS https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io/admin/health/embed-stack \
  -H "X-Admin-Token: $ADMIN_JWT" | jq '.embed'
# expect: { "healthy": true, "provider": "workers_ai_custom" }

# Flip api.syrabit.ai DNS in Cloudflare to the ACA FQDN (proxied)
# CNAME api.syrabit.ai -> syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io
```

---

## 0a. From-scratch bootstrap — single-paste (Task #400 cutover)

Run this once in **Azure Cloud Shell (bash)**. It provisions the
resource group, ACR, ACA managed environment, Key Vault, seeds the
six runtime secrets (you'll be prompted for each value), then deploys
the Bicep template. After it finishes, the only remaining manual
steps are: (1) build & push the first image to ACR, (2) point
`api.syrabit.ai` DNS at the new ACA FQDN.

```bash
set -euo pipefail

# --- edit if you ever rename the resource group / ACR ---
RG="syrabit-prod"
LOC="eastus"           # MUST match the Azure OpenAI deployment region (syrabit-openai is in eastus)
ACR="syrabitacr"
ACA_ENV="syrabit-aca-env"
KV="syrabit-prod-kv"

# --- 1. register the resource providers (idempotent, ~30s) ---
for prov in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry Microsoft.KeyVault; do
  az provider register -n "$prov" --wait
done

# --- 2. base infra ---
az group create -n "$RG" -l "$LOC" -o none
az acr create  -g "$RG" -n "$ACR" --sku Standard --admin-enabled false -o none
az containerapp env create -g "$RG" -n "$ACA_ENV" -l "$LOC" -o none
az keyvault create -g "$RG" -n "$KV" -l "$LOC" --enable-rbac-authorization true -o none

# --- 3. grant YOUR user the right to seed secrets ---
ME=$(az ad signed-in-user show --query id -o tsv)
KV_ID=$(az keyvault show -n "$KV" --query id -o tsv)
az role assignment create --assignee "$ME" --role "Key Vault Secrets Officer" --scope "$KV_ID" -o none

# --- 4. seed the 6 runtime secrets (you will be prompted for each) ---
for s in SENDGRID-API-KEY AZURE-OPENAI-API-KEY MONGO-URI JWT-SECRET RAZORPAY-KEY-SECRET WORKERS-EMBED-SECRET; do
  read -srp "Value for $s : " v; echo
  az keyvault secret set --vault-name "$KV" --name "$s" --value "$v" -o none
done
unset v

# --- 5. push a placeholder bootstrap image so Bicep has something to point at ---
# (CI replaces this on the next deploy.)
az acr import -n "$ACR" --source mcr.microsoft.com/mcr/hello-world:latest \
  --image syrabit/backend:bootstrap -o none

# --- 6. deploy the Bicep template ---
az deployment group create \
  --resource-group "$RG" \
  --template-file infra/azure/aca-syrabit-backend.bicep \
  --parameters acrName="$ACR" image=syrabit/backend:bootstrap

# --- 7. grant the ACA managed identity read access to the vault ---
APP_PRINCIPAL=$(az containerapp show -g "$RG" -n syrabit-backend --query identity.principalId -o tsv)
az role assignment create --assignee "$APP_PRINCIPAL" --role "Key Vault Secrets User" --scope "$KV_ID" -o none

# --- 8. print the FQDN to point api.syrabit.ai at ---
FQDN=$(az containerapp show -g "$RG" -n syrabit-backend --query properties.configuration.ingress.fqdn -o tsv)
printf '\n────────── DONE ──────────\n'
printf 'ACA FQDN  : %s\n' "$FQDN"
printf 'Next     : update Cloudflare so api.syrabit.ai → CNAME %s (proxied)\n' "$FQDN"
printf '            then run the GH Actions "Azure Container Apps deploy" workflow\n'
printf '            with app=syrabit-backend, mode=deploy to ship the real image.\n'
printf '──────────────────────────\n\n'
```

After this finishes the Container App is live but serving the
placeholder `hello-world` image. Section 1 below is then the per-deploy
flow that builds and ships the real backend image.

## 0. One-time bootstrap (do once)

1. Provision the resource group, Container Registry, managed
   environment and Key Vault:

   ```bash
   az group create -n syrabit-prod -l eastus
   az acr create -g syrabit-prod -n syrabitacr --sku Standard --admin-enabled false
   az containerapp env create -g syrabit-prod -n syrabit-aca-env -l eastus
   az keyvault create -g syrabit-prod -n syrabit-prod-kv -l eastus
   ```

2. Configure the GitHub OIDC federation so the deploy workflow can
   `az login` without a client secret. Set the following repo secrets:

   | Secret | Where to get it |
   |---|---|
   | `AZURE_CLIENT_ID` | Federated identity app registration |
   | `AZURE_TENANT_ID` | `az account show --query tenantId -o tsv` |
   | `AZURE_SUBSCRIPTION_ID` | `az account show --query id -o tsv` |

   And the following repo *vars* (non-secret):

   | Variable | Value |
   |---|---|
   | `AZURE_RESOURCE_GROUP` | `syrabit-prod` |
   | `AZURE_ACR_NAME` | `syrabitacr` |
   | `AZURE_ACA_ENV` | `syrabit-aca-env` |

3. Seed the Key Vault with the secrets the Container App reads via
   `secretRef`:

   ```bash
   az keyvault secret set --vault-name syrabit-prod-kv --name SENDGRID-API-KEY     --value '...'
   az keyvault secret set --vault-name syrabit-prod-kv --name AZURE-OPENAI-API-KEY --value '...'
   az keyvault secret set --vault-name syrabit-prod-kv --name MONGO-URI            --value '...'
   az keyvault secret set --vault-name syrabit-prod-kv --name JWT-SECRET           --value '...'
   az keyvault secret set --vault-name syrabit-prod-kv --name RAZORPAY-KEY-SECRET  --value '...'
   # Task #400 — shared secret the backend sends to the Cloudflare embed worker.
   # Must equal the worker's EMBED_SHARED_SECRET binding (set via
   # `wrangler secret put EMBED_SHARED_SECRET --env production`).
   az keyvault secret set --vault-name syrabit-prod-kv --name WORKERS-EMBED-SECRET --value '...'
   ```

4. Deploy the Bicep template once to materialize the Container App:

   ```bash
   az deployment group create \
     --resource-group syrabit-prod \
     --template-file infra/azure/aca-syrabit-backend.bicep \
     --parameters acrName=syrabitacr image=syrabit/backend:bootstrap
   ```

5. Grant the Container App's system-assigned managed identity the
   `Key Vault Secrets User` role on `syrabit-prod-kv`. The Bicep
   template references the secrets by `keyVaultUrl`; without this
   grant the revision will fail to start.

---

## 1. Per-deploy cutover

Once the bootstrap above is done, every release is one workflow click.

1. From GitHub, **Actions → Azure Container Apps deploy (manual)**, set
   `app=syrabit-backend`, `mode=deploy`, run.
2. The workflow:
   - Builds `artifacts/syrabit-backend/Dockerfile` into ACR.
   - Calls `az containerapp update --image ...` so a new revision rolls
     in (single-revision mode, 100 % traffic to the new revision).
   - Probes `https://api.syrabit.ai/health` until it returns 200.
3. Watch the existing `Cloudflare → BACKEND_URL` proxy. As soon as the
   new revision is healthy, flip `BACKEND_URL` (CF Worker var) from
   the DO origin to the ACA FQDN.

## 2. Roll back

If the new revision misbehaves:

```bash
# List recent revisions, pick the previous good one
az containerapp revision list -g syrabit-prod -n syrabit-backend -o table
# Send 100 % traffic back to that revision
az containerapp ingress traffic set \
  -g syrabit-prod -n syrabit-backend \
  --revision-weight <prev-revision>=100
```

The legacy `digitalocean-deploy.yml` workflow was removed on 2026-05-06 once the cutover
is in progress so DO can serve as the immediate rollback floor for the
first 14 days post-cutover.
