# Azure Container Apps cutover runbook (Task #347)

This runbook walks an operator through cutting the Syrabit FastAPI
backend over from Digital Ocean App Platform onto Azure Container Apps.
The motivation is co-location with the Azure OpenAI primary so every
chat request stops paying the cross-cloud egress + RTT it currently
pays from `blr1` → Azure `eastus2`.

The Bicep template (`infra/azure/aca-syrabit-backend.bicep`) and the
`.github/workflows/azure-container-apps-deploy.yml` workflow are the
two artifacts CI uses; this doc is the human-readable runbook.

---

## 0. One-time bootstrap (do once)

1. Provision the resource group, Container Registry, managed
   environment and Key Vault:

   ```bash
   az group create -n syrabit-prod -l eastus2
   az acr create -g syrabit-prod -n syrabitacr --sku Standard --admin-enabled false
   az containerapp env create -g syrabit-prod -n syrabit-aca-env -l eastus2
   az keyvault create -g syrabit-prod -n syrabit-prod-kv -l eastus2
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

The `digitalocean-deploy.yml` workflow remains intact while the cutover
is in progress so DO can serve as the immediate rollback floor for the
first 14 days post-cutover.
