# API Key Rotation Guide

This document provides step-by-step procedures for rotating all secrets used by Syrabit.

---

## 1. Sarvam API Key

### Pre-requisites
- Azure CLI authenticated (`az login`)
- Wrangler CLI authenticated (`wrangler login`)
- Access to Sarvam dashboard to generate new key

### Steps
1. Generate a new API key from the Sarvam dashboard.
2. Update the Azure Container App secret:
   ```bash
   az containerapp secret set \
     --name syrabit-backend \
     --resource-group syrabit-prod-rg \
     --secrets sarvam-api-key=<NEW_KEY>
   ```
3. Update the Cloudflare Worker secret:
   ```bash
   wrangler secret put SARVAM_API_KEY
   # Paste the new key when prompted
   ```
4. Restart the container app to pick up the new secret:
   ```bash
   az containerapp revision restart \
     --name syrabit-backend \
     --resource-group syrabit-prod-rg \
     --revision <LATEST_REVISION>
   ```

### Verification
- Send a test chat message in Assamese and confirm Sarvam responds.
- Check application logs for authentication errors.

### Rollback
- Re-set the old key using the same `az containerapp secret set` and `wrangler secret put` commands.
- Restart the container app.

---

## 2. Vertex AI Credentials (Service Account JSON)

### Pre-requisites
- GCP Console access with IAM Admin role
- Azure CLI authenticated
- Access to Azure Key Vault

### Steps
1. In GCP Console, navigate to IAM & Admin > Service Accounts.
2. Select the Vertex AI service account (`syrabit-vertex@<project>.iam.gserviceaccount.com`).
3. Create a new JSON key (Keys tab > Add Key > Create new key > JSON).
4. Upload the new JSON to Azure Key Vault:
   ```bash
   az keyvault secret set \
     --vault-name syrabit-prod-kv \
     --name vertex-ai-credentials \
     --file /path/to/new-key.json
   ```
5. Restart the container app to reload credentials:
   ```bash
   az containerapp revision restart \
     --name syrabit-backend \
     --resource-group syrabit-prod-rg \
     --revision <LATEST_REVISION>
   ```
6. Delete the old key from GCP Console (after verifying the new one works).

### Verification
- Send a test chat message in English and confirm Vertex AI responds.
- Monitor logs for `google.auth` errors for 15 minutes.

### Rollback
- Re-upload the old JSON key to Azure Key Vault using the same `az keyvault secret set` command.
- Restart the container app.
- Delete the new (broken) key from GCP Console if needed.

---

## 3. Razorpay Keys (Key ID + Key Secret)

### Pre-requisites
- Razorpay Dashboard access (owner role)
- Azure CLI authenticated
- Access to Azure Key Vault

### Steps
1. In Razorpay Dashboard, navigate to Settings > API Keys > Generate Key.
2. Copy both the Key ID and Key Secret (Secret is shown only once).
3. Update Azure Key Vault:
   ```bash
   az keyvault secret set \
     --vault-name syrabit-prod-kv \
     --name razorpay-key-id \
     --value "<NEW_KEY_ID>"

   az keyvault secret set \
     --vault-name syrabit-prod-kv \
     --name razorpay-key-secret \
     --value "<NEW_KEY_SECRET>"
   ```
4. Update the container app environment variables:
   ```bash
   az containerapp update \
     --name syrabit-backend \
     --resource-group syrabit-prod-rg \
     --set-env-vars \
       RAZORPAY_KEY_ID=secretref:razorpay-key-id \
       RAZORPAY_KEY_SECRET=secretref:razorpay-key-secret
   ```
5. Verify webhook signature validation still works (Razorpay uses Key Secret for HMAC).

### Verification
- Create a test subscription and verify payment flow completes.
- Check webhook endpoint returns 200 for incoming Razorpay events.
- Monitor `/api/webhooks/razorpay` logs for signature validation errors.

### Rollback
- Re-set the old Key ID and Key Secret in Azure Key Vault.
- Update the container app env vars to point to old secrets.
- Note: The old key remains valid in Razorpay until explicitly deactivated.

---

## 4. JWT Secret

### Pre-requisites
- Azure CLI authenticated
- Access to Azure Key Vault

### Steps
1. Generate a new secret (minimum 32 characters):
   ```bash
   openssl rand -base64 48
   ```
2. Update Azure Key Vault:
   ```bash
   az keyvault secret set \
     --vault-name syrabit-prod-kv \
     --name jwt-secret \
     --value "<NEW_SECRET>"
   ```
3. Update the container app:
   ```bash
   az containerapp update \
     --name syrabit-backend \
     --resource-group syrabit-prod-rg \
     --set-env-vars JWT_SECRET=secretref:jwt-secret
   ```

> **WARNING**: Changing the JWT secret invalidates ALL active user sessions immediately.
> All users will be logged out and must re-authenticate.
> Coordinate with the frontend team to show a "session expired" message.

### Verification
- Confirm old tokens return 401 Unauthorized.
- Log in with a test account and verify new tokens are issued and accepted.
- Check that refresh token flow works correctly.

### Rollback
- Re-set the old JWT secret in Key Vault and update the container app.
- Note: Users who already received new tokens will be invalidated again on rollback.

---

## 5. Supabase Anon Key

### Pre-requisites
- Supabase Dashboard access (project owner)
- GitHub repository write access
- Cloudflare Pages access

### Steps
1. In Supabase Dashboard, navigate to Settings > API > Project API keys.
2. Regenerate the `anon` (public) key.
3. Update GitHub Secrets:
   - Go to Repository Settings > Secrets and variables > Actions.
   - Update `SUPABASE_ANON_KEY` with the new value.
4. Update Cloudflare Pages environment variables:
   - Go to Cloudflare Dashboard > Pages > syrabit-frontend > Settings > Environment variables.
   - Update `VITE_SUPABASE_ANON_KEY` for both Production and Preview environments.
5. Trigger a redeploy of the frontend:
   ```bash
   # Trigger GitHub Actions workflow
   gh workflow run deploy-all.yml

   # Or redeploy via Cloudflare
   wrangler pages deploy dist --project-name=syrabit-frontend
   ```
6. Redeploy the backend (if it references the anon key for service-role operations).

### Verification
- Open the frontend and verify user login/signup works.
- Check browser Network tab for 401 errors on Supabase calls.
- Verify real-time subscriptions still connect.

### Rollback
- The old anon key is invalidated immediately upon regeneration in Supabase.
- If the new key is not working, check for typos in the environment variables.
- There is no way to restore the old key; you must fix the new key deployment.

---

## 6. Cloudflare API Token

### Pre-requisites
- Cloudflare Dashboard access (Super Administrator)
- GitHub repository write access

### Steps
1. In Cloudflare Dashboard, navigate to My Profile > API Tokens.
2. Find the existing token used for CI/CD (typically named "Syrabit CI/CD").
3. Click "Roll" to regenerate, or create a new token with the same permissions:
   - Zone: DNS Edit
   - Zone: Zone Read
   - Account: Cloudflare Pages Edit
   - Account: Workers Scripts Edit
4. Copy the new token value.
5. Update GitHub Secrets:
   - Go to Repository Settings > Secrets and variables > Actions.
   - Update `CLOUDFLARE_API_TOKEN` with the new value.
6. If using Wrangler locally, update your local `.env` or run:
   ```bash
   wrangler login
   ```

### Verification
- Trigger the CI/CD pipeline (`deploy-all.yml`) and verify it completes successfully.
- Check that DNS records are still resolving correctly.
- Verify Cloudflare Pages deployments work.

### Rollback
- If using "Roll", the old token is immediately invalidated. No rollback possible.
- If you created a new token instead, delete it and ensure the old token is still active.
- Update GitHub Secrets back to the old token value if it was not rolled.

---

## General Best Practices

1. **Never rotate all keys at once.** Rotate one at a time and verify before proceeding.
2. **Schedule rotations during low-traffic periods** (e.g., 2-4 AM IST on weekdays).
3. **Keep rotation logs** - document when each key was last rotated in your team's runbook.
4. **Set calendar reminders** for quarterly rotation of all secrets.
5. **Test in staging first** when possible (especially for JWT secret and Razorpay keys).
6. **Securely delete old keys** from local machines after rotation is complete.
