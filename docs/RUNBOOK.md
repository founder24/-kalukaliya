# Syrabit Operations Runbook

## Restart the Backend

```bash
az containerapp revision restart \
  --name syrabit-backend \
  --resource-group rg-syrabit \
  --revision <revision-name>

# List revisions to find the active one:
az containerapp revision list --name syrabit-backend --resource-group rg-syrabit -o table
```

## Rotate API Keys

### Sarvam AI Key

1. Generate new key in Sarvam dashboard
2. Update Azure KeyVault: `az keyvault secret set --vault-name kv-syrabit --name SARVAM-API-KEY --value <new-key>`
3. Restart backend to pick up new secret

### Vertex AI Credentials

1. Create new service account key in GCP Console
2. Update KeyVault: `az keyvault secret set --vault-name kv-syrabit --name GOOGLE-CREDENTIALS --value <base64-encoded-json>`
3. Restart backend

### Razorpay Keys

1. Generate new keys in Razorpay Dashboard
2. Update KeyVault secrets: RAZORPAY-KEY-ID and RAZORPAY-KEY-SECRET
3. Restart backend
4. Verify webhook signature verification still works

## Rollback a Deploy

```bash
# List recent revisions
az containerapp revision list --name syrabit-backend --resource-group rg-syrabit -o table

# Activate previous revision
az containerapp revision activate --name syrabit-backend --resource-group rg-syrabit --revision <previous-revision>

# Route 100% traffic to previous revision
az containerapp ingress traffic set --name syrabit-backend --resource-group rg-syrabit --revision-weight <previous-revision>=100
```

## Check Logs

```bash
# Stream live logs
az containerapp logs show --name syrabit-backend --resource-group rg-syrabit --follow

# Query Log Analytics
az monitor log-analytics query --workspace law-syrabit \
  --analytics-query "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(1h) | order by TimeGenerated desc" \
  --out table
```

## Manually Trigger CI/CD

```bash
# Trigger backend deploy
gh workflow run deploy-all.yml --ref main

# Trigger specific workflow
gh workflow run ci-backend.yml --ref main
```

## Emergency Contacts / Escalation

- **P1 (site down)**: Page on-call immediately via PagerDuty/Opsgenie
- **P2 (degraded)**: Slack #alerts, respond within 15 minutes
- **P3 (non-urgent)**: Create GitHub issue, triage next business day
- **Cloud provider support**: Azure Support (Standard tier), Cloudflare Enterprise
