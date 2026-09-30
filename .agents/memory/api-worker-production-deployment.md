---
name: API Worker production deployment
description: Production constraints for deploying and smoke-testing the API Worker.
---

Use the configured `syrabit-api-prod.axomxplain.workers.dev` hostname for
authenticated internal-generation smoke tests; a Cloudflare account ID is not
the `workers.dev` account slug. Direct Wrangler deployment requires Node 22 or
newer with the current Wrangler release.

**Why:** Building a hostname from the Cloudflare account ID produces an
unresolvable address, and the Node 20 workspace runtime is rejected before
Wrangler can deploy.

**How to apply:** Keep CI smoke tests pointed at the configured Worker hostname
and retain Node 22+ when running the API Worker's production deployment flow.

## Disposable staging probes

For a disposable staging AI probe that must not touch production, use a unique
legacy staging environment with only `APP_ENV=staging` and the AI binding.
Wrangler service-environment deployment requires the service to exist; creating
its default environment just to provision a staging probe crosses the
non-staging deployment boundary. A secret appearing in Wrangler's name-only
list is not enough; require an authenticated runtime readiness response before
any AI call.

**Why:** A named service-environment deploy against a new disposable Worker
failed with Cloudflare error 10090 because no base service existed. Secret
metadata could also appear before the running Worker accepted it.

**How to apply:** Generate a run-specific staging config and name, upload the
secret through stdin, verify the resolved Worker name, poll a token-protected
readiness route, run the probes only after it succeeds, and delete the Worker
and temporary config in `finally`.

## API-only production releases

The standard Cloudflare release workflow deploys the API Worker, edge Worker,
and Pages together. For a backend-only release, use a clean checkout based on
the deployed commit and include only the intended API changes. Preserve the
API release safeguards: run tests and type-checking, verify secret names and
the D1 migration ledger/required migrations, run the refresh-token bridge
guard, deploy only the production API Worker, and record successful deployment
evidence in D1. Do not apply migrations when the change has none.

**Why:** Re-running the aggregate workflow for an API-only fix republishes
unrelated edge and frontend assets. Bypassing the API job's checks or rollout
recording loses important production safeguards.

**How to apply:** Keep API-only changes isolated from unrelated workspace
commits; use the aggregate workflow whenever edge, frontend, secrets, or D1
schema changes are part of the release.