"""Task #551 §B — Lambda batch-job adapters.

Each module here exposes a `handler(event, context)` entry point that
the EventBridge Scheduler invokes on the cron defined in
`infra/aws/lambda-batch-jobs.tf`. The handlers are thin wrappers that
re-use the existing `aca_jobs/*.py` business logic — Lambda runs the
same code path the in-process ACA loop runs, so during the 7-day
shadow period both paths produce identical output and the
reconciliation script can compare per-document hashes.

Migrated jobs are registered in `infra/aws/lambda/manifest.json` and
enforced by `artifacts/syrabit-backend/scripts/check_dead_providers.py`.
"""
