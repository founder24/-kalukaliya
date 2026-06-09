---
name: CF_ACCOUNT_ID is a core secret for Workers AI
description: CF_ACCOUNT_ID must be a core (not optional) secret on Cloud Run for Workers AI to work
---

## Rule
`CF_ACCOUNT_ID` must be in the core `--update-secrets` block of every deploy, not in the optional guarded block.

**Why:**
Cloudflare Workers AI API URL is `https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run`.
Without `CF_ACCOUNT_ID`, `cloudflare_client.py` raises `RuntimeError("CF_ACCOUNT_ID missing")` on every call → English chat falls back to Sarvam silently → user sees 9s latency instead of <1s.

## How to apply
In `deploy.yml` and manual `gcloud run deploy`, always include:
```
CF_ACCOUNT_ID=CF_ACCOUNT_ID:latest,CLOUDFLARE_ACCOUNT_ID=CF_ACCOUNT_ID:latest
```
alongside `CF_API_TOKEN=CF_API_TOKEN:latest` in `--update-secrets`.

Never include `CF_ACCOUNT_ID` or `CLOUDFLARE_ACCOUNT_ID` in `--remove-secrets`.

## GCP Secret Manager name
Secret is named `CF_ACCOUNT_ID` (uppercase) in SM project `blissful-acumen-495019-t6`.
