# infra/gcp

GCP is retained **only** for the auxiliary AI-API + observability +
billing-telemetry surface. **All GCP hosting / cron / CI / queueing
workloads are forbidden** — Task #347 decommissioned them and Task #489
deleted the leftover Cloud Run + Cloud Tasks + Cloud Scheduler client
modules. See:

- [`infra/four-cloud-delegation.md`](../../../../infra/four-cloud-delegation.md) §B "GCP / Vertex must NOT"
- [`docs/infra/gcp-landing-zone.md`](../docs/infra/gcp-landing-zone.md) §1 "What this project hosts"
- [`infra/v4-locked-architecture.md`](../../../../infra/v4-locked-architecture.md) §0, §15 amendment

## What's in this directory

| File | Purpose |
|---|---|
| `main.tf` | Provider, variables, project-API enablement (whitelist only). |
| `iam.tf` | Single least-privilege service account `syrabit-ai-apis@…`. |
| `budget.tf` | Monthly budget + 50/80/100 % threshold alerts feeding V4 §10 Rule C. |

## What is forbidden

This directory must **never** declare any of:

- `google_cloud_run_*`, `google_cloudfunctions_*`
- `google_cloud_tasks_*`, `google_cloud_scheduler_*`
- `google_compute_*`, `google_container_cluster`
- `google_cloudbuild_*`, `google_artifact_registry_*`
- `google_storage_bucket` with a `website` block (no GCS hosting)
- IAM bindings for `roles/run.*`, `roles/cloudtasks.*`,
  `roles/cloudscheduler.*`, `roles/compute.*`, `roles/cloudbuild.*`,
  `roles/cloudfunctions.*`

The CI drift guard
[`.github/workflows/four-cloud-delegation-drift.yml`](../../../../.github/workflows/four-cloud-delegation-drift.yml)
fails the merge if any of the above appear in any `.tf` under this
directory or anywhere else in the repo.

Any new hosting, queue, scheduler, or build configuration belongs under
[`infra/azure/`](../../../../infra/azure/) (compute) or
[`infra/aws/`](../aws/) (events).
