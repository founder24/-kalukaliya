// Task #347 — Azure Container Apps spec for the Syrabit FastAPI backend.
//
// Azure Container Apps spec for the syrabit-backend FastAPI service —
// production deploy target. This Bicep file is the single source of
// truth for the syrabit-backend Container App resource — the
// .github/workflows/azure-container-apps-deploy.yml workflow only swaps
// the image tag on an already-provisioned revision; structural changes
// (scale rules, env vars, ingress) flow through this template.
//
// Region: eastus — same region as the Azure OpenAI deployment (`syrabit-openai`) so the
// chat hot-path stays intra-region (zero cross-region egress).
//
// Cutover runbook: artifacts/syrabit/docs/infra/aca-cutover.md
//
// Apply with:
//   az deployment group create \
//     --resource-group syrabit-prod \
//     --template-file infra/azure/aca-syrabit-backend.bicep \
//     --parameters acrName=syrabitacr image=syrabit/backend:bootstrap

@description('Azure Container Apps managed environment name.')
param envName string = 'syrabit-aca-env'

@description('Azure Container Registry login server (without the .azurecr.io suffix).')
param acrName string

@description('Initial container image tag to bootstrap the revision with. CI swaps this on every deploy.')
param image string

@description('Region — must match the Azure OpenAI deployment to keep the chat hot-path intra-region.')
param location string = 'eastus'

@description('Azure Key Vault name that holds the runtime secrets (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AZURE_OPENAI_API_KEY, MONGO_URI, …). SendGrid was retired by Task #400 — Tier-2 email is now Amazon SES via boto3.')
param keyVaultName string = 'syrabit-prod-kv'

resource managedEnv 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: envName
}

resource backend 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'syrabit-backend'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: managedEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        traffic: [
          { latestRevision: true, weight: 100 }
        ]
        corsPolicy: {
          allowedOrigins: [
            'https://syrabit.ai'
            'https://www.syrabit.ai'
            'https://admin.syrabit.ai'
          ]
          allowedMethods: [ 'GET', 'POST', 'PUT', 'DELETE', 'OPTIONS' ]
          allowCredentials: true
        }
      }
      registries: [
        { server: '${acrName}.azurecr.io', identity: 'system' }
      ]
      // Key Vault references — the actual values live in keyVaultName.
      // The Container App's system-assigned identity needs the
      // `Key Vault Secrets User` role on keyVaultName (assigned out of
      // band; not part of this template).
      secrets: [
        // Task #400 — Tier-2 email migrated from SendGrid → Amazon SES.
        // The IAM principal these creds belong to needs `ses:SendEmail`
        // on the EMAIL_FROM identity (`noreply@syrabit.ai`). SES region
        // is set as a plain env (AWS_SES_REGION) below, not a secret.
        { name: 'aws-access-key-id',     keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/AWS-ACCESS-KEY-ID',     identity: 'system' }
        { name: 'aws-secret-access-key', keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/AWS-SECRET-ACCESS-KEY', identity: 'system' }
        { name: 'azure-openai-api-key',  keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/AZURE-OPENAI-API-KEY',  identity: 'system' }
        { name: 'mongo-uri',             keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/MONGO-URI',             identity: 'system' }
        { name: 'jwt-secret',            keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/JWT-SECRET',             identity: 'system' }
        { name: 'admin-jwt-secret',      keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/ADMIN-JWT-SECRET',       identity: 'system' }
        { name: 'razorpay-key-secret',   keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/RAZORPAY-KEY-SECRET',    identity: 'system' }
        // Task #400 — shared secret the backend sends as `X-Embed-Secret` to the
        // Cloudflare embed worker (`embed.syrabit.ai`). Same value as the worker's
        // `EMBED_SHARED_SECRET` binding; keep them in lock-step on rotation.
        { name: 'workers-embed-secret', keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/WORKERS-EMBED-SECRET', identity: 'system' }
      ]
    }
    template: {
      containers: [
        {
          name:  'syrabit-backend'
          image: '${acrName}.azurecr.io/${image}'
          resources: {
            cpu:     json('1.0')
            memory: '2.0Gi'
          }
          env: [
            // Task #400 — Tier-2 email is Amazon SES (boto3). The
            // `EMAIL_PROVIDER` flag drives `email_templates._send_sync`
            // / `send_admin_email`; flip to `sendgrid` to roll back
            // without redeploying code (the legacy path is preserved).
            { name: 'AWS_ACCESS_KEY_ID',     secretRef: 'aws-access-key-id' }
            { name: 'AWS_SECRET_ACCESS_KEY', secretRef: 'aws-secret-access-key' }
            { name: 'AWS_SES_REGION',        value: 'us-east-1' }
            { name: 'EMAIL_PROVIDER',        value: 'ses' }
            { name: 'AZURE_OPENAI_API_KEY',  secretRef: 'azure-openai-api-key' }
            { name: 'MONGO_URL',             secretRef: 'mongo-uri' }
            { name: 'JWT_SECRET',            secretRef: 'jwt-secret' }
            { name: 'ADMIN_JWT_SECRET',      secretRef: 'admin-jwt-secret' }
            { name: 'RAZORPAY_KEY_SECRET',   secretRef: 'razorpay-key-secret' }
            { name: 'EMAIL_FROM',            value: 'Syrabit.ai <noreply@syrabit.ai>' }
            { name: 'ENV',                   value: 'production' }
            // Task #400 — route embeddings through the new Cloudflare worker
            // (Gemma-300M + Qwen3-0.6B fused → 1024-dim) instead of Cohere.
            // The provider is selected by `EMBED_PROVIDER_PRIMARY`; rolling
            // back is a single env-var revert to `cohere`.
            { name: 'WORKERS_EMBED_URL',     value: 'https://embed.syrabit.ai' }
            { name: 'WORKERS_EMBED_SECRET',  secretRef: 'workers-embed-secret' }
            { name: 'EMBED_PROVIDER_PRIMARY', value: 'workers_ai_custom' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/api/health', port: 8000 }
              initialDelaySeconds: 60
              periodSeconds: 15
              failureThreshold: 10
            }
            {
              type: 'Readiness'
              httpGet: { path: '/api/health', port: 8000 }
              initialDelaySeconds: 30
              periodSeconds: 10
              failureThreshold: 6
            }
          ]
        }
      ]
      scale: {
        minReplicas: 2
        maxReplicas: 10
        rules: [
          {
            name: 'http-concurrency'
            http: { metadata: { concurrentRequests: '50' } }
          }
        ]
      }
    }
  }
}

output backendUrl string = 'https://${backend.properties.configuration.ingress.fqdn}'
