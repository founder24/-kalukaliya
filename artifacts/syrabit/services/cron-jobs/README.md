# services/cron-jobs

Container image and entrypoint for the Azure Container Apps Jobs that
replace the legacy GCP Cloud Scheduler entries and the in-process
asyncio loops on the FastAPI backend.

## Layout

```
services/cron-jobs/
├── Dockerfile        # Single image baked for every aca-job-* job
├── README.md         # this file
├── requirements.txt  # Pinned runtime deps (synced with the legacy
│                     # backend's pyproject.toml — see CI check)
└── run.py            # Dispatcher; reads JOB_NAME → runs once → exits
```

The Terraform definition for the jobs themselves is at
`infra/azure/container-apps-jobs.tf`. The `cron_jobs` map there is
the single source of truth for **schedule + resource sizing**;
`run.py`'s `DISPATCH` table is the single source of truth for
**which Python coroutine each job runs**. A CI step asserts the two
key sets are identical so adding a new cron job in one file without
the other fails the build.

## Adding a new cron job

1. Pick a `JOB_NAME` (kebab-case, ≤ 32 chars).
2. Add the row to the `cron_jobs` map in `infra/azure/container-apps-jobs.tf`
   with `kind = "loop"` (or `"scheduler"` if it has an explicit
   external schedule rather than a former-loop interval).
3. Add the `JOB_NAME → "module:callable"` row to `DISPATCH` in
   `run.py`. The callable must be `async def` and take no args.
4. Open a PR — the CI key-set check fails if either file is missing
   the new row. Terraform plan shows the new `azurerm_container_app_job`
   and the matching `azurerm_monitor_metric_alert` for failures.

## Removing a cron job

Reverse of the above — delete from both files in the same PR. The
`azurerm_container_app_job` destroy is non-disruptive (running
replicas drain on their `replica_timeout_in_seconds`).

## Local smoke

```bash
docker build -t syrabit-cron-jobs:dev services/cron-jobs/
docker run --rm \
  -e JOB_NAME=vertex-probe \
  -e JOB_KIND=scheduler \
  -e LZ_PROJECT=syrabit -e LZ_ENV=dev \
  syrabit-cron-jobs:dev
```

The container exits 0 on success, 1 on a job exception, 2 on a
config error (unknown `JOB_NAME`).

## Operational runbook

See `docs/infra/cron-on-azure.md` for the on-call playbook (replay
a failed run, drain a stuck job, swap to maintenance mode, etc.).
