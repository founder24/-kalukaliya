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

@description('Azure region for the Container App. Vertex Gemini is now the chat HEAD (Task #554); Task #552 §G-R retired the surviving Azure Speech + Translator surfaces, so the backend no longer has any Azure data-plane dependency on a specific region.')
param location string = 'eastus'

@description('Azure Key Vault name that holds the runtime secrets (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, MONGO_URI, GOOGLE_APPLICATION_CREDENTIALS_JSON, …). Task #556 retired the prior dual-provider transactional email shape — Amazon SES via boto3 is now the sole transactional path (no fallback, no break-glass; V4 §12 "no silent fallbacks"). Task #552 §G-R retired AZURE_SPEECH_KEY + AZURE_TRANSLATOR_KEY alongside the rest of the Azure AI surfaces.')
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
        // Task #556 — Amazon SES is the SOLE transactional email path
        // (legacy dual-provider shape from Task #400 fully retired).
        // The IAM principal these creds belong to needs `ses:SendEmail`
        // on the EMAIL_FROM identity (`noreply@syrabit.ai`). SES region
        // is set as a plain env (SES_REGION) below, not a secret.
        { name: 'aws-access-key-id',     keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/AWS-ACCESS-KEY-ID',     identity: 'system' }
        { name: 'aws-secret-access-key', keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/AWS-SECRET-ACCESS-KEY', identity: 'system' }
        // Task #554 — Azure OpenAI retired. Chat now routes Vertex Gemini
        // 2.5 Flash → Workers-AI Llama-3.2-3B; the legacy chat-tenant
        // Key Vault secret is no longer mounted into ACA.
        { name: 'mongo-uri',             keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/MONGO-URI',             identity: 'system' }
        { name: 'jwt-secret',            keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/JWT-SECRET',             identity: 'system' }
        { name: 'admin-jwt-secret',      keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/ADMIN-JWT-SECRET',       identity: 'system' }
        { name: 'razorpay-key-secret',   keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/RAZORPAY-KEY-SECRET',    identity: 'system' }
        // Task #400 — shared secret the backend sends as `X-Embed-Secret` to the
        // Cloudflare embed worker (`embed.syrabit.ai`). Same value as the worker's
        // `EMBED_SHARED_SECRET` binding; keep them in lock-step on rotation.
        { name: 'workers-embed-secret', keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/WORKERS-EMBED-SECRET', identity: 'system' }
        // Task #472 follow-up — closes the OriginGate gap. ORIGIN-SHARED-SECRET
        // value MUST equal the syrabitworker `BACKEND_ORIGIN_SECRET` binding;
        // the worker injects it as `X-Origin-Auth` on every backend fetch and
        // `OriginSharedSecretMiddleware` (artifacts/syrabit-backend/middleware.py)
        // 403s any request that is missing or has a stale value. Rotate both
        // sides in lock-step. D1-SYNC-SECRET is the matching secret read by
        // the D1 sync handler when the worker mirrors edge writes back to the
        // Cloudflare D1 syllabus DB.
        { name: 'origin-shared-secret', keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/ORIGIN-SHARED-SECRET', identity: 'system' }
        { name: 'd1-sync-secret',       keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/D1-SYNC-SECRET',       identity: 'system' }
        // Task #558 — Sentry Developer free tier DSN (errors-only sink).
        // Tracing lives in OTEL → GCP Cloud Trace; this DSN is what the
        // FastAPI `observability/sentry_setup.py` initializer consumes.
        { name: 'sentry-dsn',           keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/SENTRY-DSN',           identity: 'system' }
        // Task #553 — Sarvam AI is the canonical Assamese-chat
        // primary (`sarvam-m` model, chain `[sarvam, workers_ai_indic]`).
        // Source of truth lives in Azure Key Vault as `SARVAM-API-KEY`;
        // the same value MUST be replicated read-only into AWS Secrets
        // Manager (`syrabit/prod/sarvam-api-key`) and Cloudflare
        // Secrets (`SARVAM_API_KEY` binding on the embed worker) per
        // the secrets-management policy in replit.md ("Azure Key Vault
        // is the source of truth, with AWS Secrets Manager and
        // Cloudflare Secrets as read-only replicas"). Rotation must
        // update KV first, then mirror to the two replicas.
        { name: 'sarvam-api-key',       keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/SARVAM-API-KEY',       identity: 'system' }
        // Task #557 — self-hosted web-push (pywebpush + py-vapid).
        // PEM-encoded EC P-256 private key. The matching public key
        // is *derived* from this on every `/push/vapid-public-key`
        // request, so there is no second secret to keep in sync.
        // Source of truth lives in Azure Key Vault as
        // `WEB-PUSH-VAPID-PRIVATE-KEY`; mirror read-only into AWS
        // Secrets Manager (`syrabit/prod/web-push-vapid-private-key`)
        // and Cloudflare Secrets (`WEB_PUSH_VAPID_PRIVATE_KEY`
        // binding) per the secrets-management policy. Rotation
        // invalidates every browser subscription on file — only
        // rotate during a planned `pushManager.subscribe` re-prompt
        // window.
        { name: 'web-push-vapid-private-key', keyVaultUrl: 'https://${keyVaultName}.vault.azure.net/secrets/WEB-PUSH-VAPID-PRIVATE-KEY', identity: 'system' }
      ]
    }
    template: {
      containers: [
        {
          name:  'syrabit-backend'
          image: '${acrName}.azurecr.io/${image}'
          // Task #513 §G — right-size for browser-heavy traffic. The
          // FastAPI hot path is I/O-bound (Mongo + Pinecone + LLM
          // dispatch); 0.25 vCPU / 0.5 GiB per replica is the smallest
          // ACA SKU that still keeps a Gunicorn worker responsive
          // under 30 concurrent requests (see scale rule below). The
          // monthly compute cost drops from ~$60 (1.0 vCPU × 2 min
          // replicas, 24×7) to ~$15 (0.25 vCPU × 2 min replicas) —
          // a 75 % saving on idle baseline. Burst headroom is provided
          // by `maxReplicas: 30` so the platform scales out instead of
          // running fewer-but-fatter pods.
          resources: {
            cpu:     json('0.25')
            memory: '0.5Gi'
          }
          env: [
            // Task #556 — Amazon SES is the SOLE transactional email
            // path. There is no fallback (V4 §12 "no silent fallbacks").
            // `SES_REGION=us-east-1` is the primary; flip to
            // `ap-south-1` (warm secondary, identity verified +
            // DKIM/SPF/DMARC aligned) and restart the revision to
            // fail over. SendGrid + Resend are fully retired — the
            // `EMAIL_PROVIDER` / `EMAIL_FALLBACK` flags no longer exist.
            { name: 'AWS_ACCESS_KEY_ID',     secretRef: 'aws-access-key-id' }
            { name: 'AWS_SECRET_ACCESS_KEY', secretRef: 'aws-secret-access-key' }
            { name: 'SES_REGION',            value: 'us-east-1' }
            // Task #554 — Azure OpenAI chat env removed. Vertex Gemini
            // 2.5 Flash auths via GOOGLE_APPLICATION_CREDENTIALS_JSON
            // (mounted from Key Vault elsewhere in the GCP cred chain).
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
            // Task #472 follow-up — see secrets[] block above for context.
            // Setting ORIGIN_SHARED_SECRET activates OriginSharedSecretMiddleware:
            // requests without a matching X-Origin-Auth header (besides the
            // small open-paths allow-list in middleware.py) get a 403. The
            // syrabitworker `BACKEND_ORIGIN_SECRET` binding MUST hold the same
            // value or all proxied traffic breaks.
            { name: 'ORIGIN_SHARED_SECRET',  secretRef: 'origin-shared-secret' }
            { name: 'D1_SYNC_SECRET',        secretRef: 'd1-sync-secret' }
            // Task #489 / Task #558 — OTEL → Cloud Trace exporter wiring.
            // Task #558 — observability narrowing: GCP Cloud Trace is
            // now the SOLE OTEL trace destination. The previous
            // `googlecloud,sentry` dual-export was retired (Sentry
            // Performance / tracing is fully removed; Sentry stays
            // only as the errors-only sink wired via SENTRY_DSN). Per
            // V4 §12 there is no silent fallback: if the GCP exporter
            // fails the backend leaves tracing disabled instead of
            // silently routing through a second sink.
            { name: 'OTEL_TRACES_EXPORTER',           value: 'googlecloud' }
            { name: 'OTEL_EXPORTER_GCP_PROJECT_ID',   value: 'syrabit-prod' }
            { name: 'OTEL_SERVICE_NAME',              value: 'syrabit-backend' }
            // Task #558 — Sentry Developer free tier (errors-only).
            { name: 'SENTRY_DSN',                     secretRef: 'sentry-dsn' }
            { name: 'SENTRY_ENVIRONMENT',             value: 'production' }
            // Task #553 — Sarvam AI Assamese-chat key. The
            // `providers/sarvam.py` facade reads this via
            // `config.SARVAM_API_KEY`; absence flips
            // `/api/admin/health/sarvam` to `not_configured` and
            // the assamese_rag_chat chain falls through to
            // `workers_ai_indic` immediately (V4 §12 — loud).
            { name: 'SARVAM_API_KEY',                 secretRef: 'sarvam-api-key' }
            // Task #557 — self-hosted web-push. The private key is
            // mounted from Key Vault (see secrets[] above);
            // WEB_PUSH_CONTACT is the RFC-8292 `sub` claim sent on
            // every outbound webpush request and surfaces in
            // gateway abuse reports — keep it pointed at a real,
            // monitored mailbox.
            { name: 'WEB_PUSH_VAPID_PRIVATE_KEY',     secretRef: 'web-push-vapid-private-key' }
            { name: 'WEB_PUSH_CONTACT',               value: 'mailto:admin@syrabit.ai' }
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
      // Task #513 §G — scale-out (not scale-up) for browser-heavy
      // traffic. `concurrentRequests: '30'` (down from 50) keeps each
      // tiny pod from queueing requests behind slow LLM round-trips,
      // and `maxReplicas: 30` (up from 10) gives 3× the burst headroom
      // we previously had — total peak concurrency 30 × 30 = 900,
      // matched to the chat cap headroom (Cap: 30/month + 3/day per
      // anon-id at the edge worker).
      scale: {
        minReplicas: 2
        maxReplicas: 30
        rules: [
          {
            name: 'http-concurrency'
            http: { metadata: { concurrentRequests: '30' } }
          }
        ]
      }
    }
  }
}

output backendUrl string = 'https://${backend.properties.configuration.ingress.fqdn}'
