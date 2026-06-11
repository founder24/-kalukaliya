---
name: Upstash must stay optional in cloudbuild.yaml
description: Upstash Redis secrets don't exist in Secret Manager; mandatory --update-secrets reference causes Cloud Run revision to fail with SecretsAccessCheckFailed
---

## The Rule
UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN must NEVER be in the mandatory `--update-secrets` block of Step 4 in `cloudbuild.yaml`. They belong ONLY in the Step 5 optional probe block.

Also include them in Step 4's `--remove-secrets` list so stale refs from accidental prior deployments are cleaned up.

**Why:** These secrets do not exist in Secret Manager (blissful-acumen-495019-t6). Cloud Run's pre-flight check validates all `--update-secrets` references exist before creating the revision. A missing secret causes `SecretsAccessCheckFailed` and the revision stays `Ready: False` permanently, while traffic stays on the previous (older) revision — silently serving stale code.

**How to apply:** If you ever see `UPSTASH_REDIS_REST_URL=upstash-redis-rest-url:latest` inside the long `--update-secrets=...` line in cloudbuild.yaml Step 4, that is the bug. Move it to the Step 5 `_check` pattern:
```bash
if gcloud secrets describe upstash-redis-rest-url --project="$$PROJECT" >/dev/null 2>&1; then
  UPDATES="$${UPDATES:+$$UPDATES,}UPSTASH_REDIS_REST_URL=upstash-redis-rest-url:latest"
else
  echo "  ⚠ upstash-redis-rest-url not in SM — Redis rate limiting disabled"
fi
```
And ensure `--remove-secrets=...,UPSTASH_REDIS_REST_URL,UPSTASH_REDIS_REST_TOKEN,...` is in Step 4 to clean old refs.
