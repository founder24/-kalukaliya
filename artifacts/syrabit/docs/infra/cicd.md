# CI/CD Runbook — DO + AWS + Azure

Phase 1d (Task #330) replaces the old GCP Cloud Build pipelines with
GitHub Actions workflows that deploy across the new four-way provider
mix. Cloudflare Pages CI for the frontend is unchanged.

## Surface map

| Surface              | Provider                | Workflow                                | Trigger paths                              |
|----------------------|-------------------------|-----------------------------------------|--------------------------------------------|
| Python FastAPI API   | DO App Platform         | `.github/workflows/do-deploy-backend.yml`   | `services/backend/**`, `infra/do/app-syrabit-backend.yaml` |
| Rust core (HTTP+gRPC)| DO App Platform         | `.github/workflows/do-deploy-rust-core.yml` | `services/rust-core/**`, `infra/do/app-rust-core.yaml`     |
| Async workers        | AWS (Lambda + SQS)      | `.github/workflows/aws-deploy-workers.yml`  | `services/workers/**`, `infra/aws/**`      |
| Cron jobs + obs.     | Azure Container Apps Jobs | `.github/workflows/azure-deploy-jobs.yml` | `services/cron/**`, `infra/azure/**`       |
| Frontend             | Cloudflare Pages        | (unchanged — Pages auto-build)          | `src/**`, `public/**`, etc.                |
| PR aggregation       | GitHub                  | `.github/workflows/pr-build-required.yml`   | every PR                                   |

## Authentication model

| Provider | Mechanism | Stored in GitHub as |
|----------|-----------|---------------------|
| DO       | API token (DO does not yet support GitHub OIDC) | env secret `DO_API_TOKEN` |
| AWS      | OIDC → assumed deploy role (`infra/aws/iam-github-oidc.tf`) | env secret `AWS_DEPLOY_ROLE_ARN` (role ARN, not credentials) |
| Azure    | OIDC → federated credential (`infra/azure/iam-github-oidc.tf`) | env secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` |

The only long-lived credential anywhere in CI is the DO API token.
AWS and Azure mint short-lived STS / AAD tokens per workflow run.

## GitHub environments

Two environments, both scoped per workflow:

- `non-prod` — auto-deploys on push to `master`. No reviewers required.
- `prod` — manual `workflow_dispatch` only. Required reviewers:
  - one infra owner
  - one engineering lead

Per-environment secrets prevent a `non-prod` run from ever touching
`prod` credentials.

## Repository variables (non-secret)

| Variable                       | Used by                    |
|--------------------------------|----------------------------|
| `DO_APP_ID_SYRABIT_BACKEND`    | `do-deploy-backend.yml`    |
| `DO_APP_ID_RUST_CORE`          | `do-deploy-rust-core.yml`  |
| `DO_REGISTRY_NAME` (optional)  | both DO workflows (default `syrabit`) |

## Image tag convention

Every image is pushed with two tags:

- `sha-<first 12 chars of GITHUB_SHA>` — the immutable deploy tag.
- `latest` — convenience pointer for local pulls; never used for the
  actual deploy step.

Deploy steps always pin to the SHA tag so a redeploy is fully
reproducible.

## PR status checks

The `pr-build-required` workflow exposes a single check name
(`build-required`) that aggregates the four surface workflows. Branch
protection on `master` requires this check + a reviewer approval.

## Rollback

| Surface | Rollback command |
|---------|------------------|
| DO backend / rust-core | `doctl apps create-deployment $APP_ID --force-rebuild=false` against the previous spec, or `doctl apps update $APP_ID --spec <previous spec>.yaml --wait`. Each `app.yaml` change is in git, so `git checkout <prev SHA> -- infra/do/app-<svc>.yaml` is the source of truth. |
| AWS workers | `aws lambda update-alias --function-name syrabit-<worker> --name live --function-version <prev>` — versions are immutable, no image rebuild needed. |
| Azure cron jobs | `az containerapp job update --name syrabit-<job> --resource-group syrabit-cron-obs --image <prev image>` — ACR retains all SHA-tagged images. |
| Frontend | Cloudflare Pages deployment list → "Rollback to this deployment". |

## Deprecation: GCP Cloud Build

The Cloud Build configs under `infra/gcp/` carry a `DEPRECATED (Task
#330)` header pointing at this runbook. They are retained read-only
until the dispatch / CDN tier is fully cut over and the
decommissioning task removes them.

## Onboarding checklist

1. Set the DO API token: `gh secret set DO_API_TOKEN --env non-prod`.
2. Set the DO app IDs as repo variables (after running
   `doctl apps create` per `infra/do/README.md`).
3. Apply the AWS landing-zone Terraform (`infra/aws/`) and copy the
   `github_deploy_role_arn` output into `AWS_DEPLOY_ROLE_ARN`.
4. Apply the Azure landing-zone Terraform (`infra/azure/`) and copy
   the three `github_deploy_*` outputs into the Azure secrets.
5. Configure `non-prod` and `prod` GitHub environments with the
   reviewer and secret rules above.
6. Add the `build-required` check to branch protection on `master`.
