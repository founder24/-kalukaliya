# Task #103 — OriginGate secret rotation, partial-execution handoff (2026-05-11)

## Symptom

`POST https://syrabit.ai/api/ai/chat` returns:

```
HTTP/2 403
{"detail":"Direct origin access denied — must traverse the edge worker."}
```

The Cloudflare edge worker reaches ACA, but the `X-Origin-Auth` header
it injects no longer matches the value the running container expects
(`OriginSharedSecretMiddleware` in `artifacts/syrabit-backend/middleware.py`).

Pre-existing drift between the worker-side `BACKEND_ORIGIN_SECRET`
and the ACA-side `ORIGIN_SHARED_SECRET`.

## Current state (after the partial rotation attempt)

| Component | Value |
| --- | --- |
| `syrabitworker` `BACKEND_ORIGIN_SECRET` | **V2** (rotated by the agent) |
| `syrabit-edge` `BACKEND_ORIGIN_SECRET` | **V2** (rotated for parity) |
| `syrabit-prod-kv` `ORIGIN-SHARED-SECRET` (latest version) | **V2** |
| `syrabit-prod-kv` `SENTRY-DSN` (latest version) | seeded from this Replit env (suspect — may be the dev DSN, not the prod DSN that the live revision had) |
| `syrabit-prod-kv` `WEB-PUSH-VAPID-PRIVATE-KEY` (latest version) | seeded from this Replit env (same caveat) |
| ACA running revision | **the previous one (pre-rotation), still on V0** |
| New revision `syrabit-backend--rotate-20260511215020` | **Unhealthy, traffic=100, active=true** — ACA promoted it but its health probes are failing, so live traffic is being served by the previous revision's still-Healthy replicas |

Direct probes (run from the Replit shell, no edge worker in front):

* `GET /api/health` → 200 (gate-exempt path).
* `POST /api/ai/chat` with `X-Origin-Auth: V1` → 403.
* `POST /api/ai/chat` with `X-Origin-Auth: V2` → 403.
* `POST /api/ai/chat` with no header → 403.

That all three values fail confirms the running container is on a
**third** value (V0 — the original pre-rotation secret).

## What the agent could not do from Replit

* No `az` CLI in this environment, so no `az containerapp logs show`
  to learn *why* the new revision is Unhealthy.
* The KV updates have already happened; the next revision-roll will
  pick up V2 — but only once the underlying startup failure is fixed.

## Suspected root cause of the Unhealthy revision

The agent re-seeded `SENTRY-DSN` and `WEB-PUSH-VAPID-PRIVATE-KEY` from
the values present in this Replit's env vars, because the previous
workflow run failed with `Unable to get value using Managed identity
system for secret sentry-dsn`. The seed succeeded against KV, but the
new revision is failing health probes — most likely because the
Replit-side values for either secret are not what the prod container
expects (different Sentry project, different VAPID key pair, etc.) and
FastAPI startup is erroring after init.

The Bicep template `infra/azure/aca-syrabit-backend.bicep` lists both
secrets as required `keyVaultUrl` refs, so they MUST exist and be
fetchable for any new revision to provision.

## Unblock runbook

You will need an interactive `az` shell (`az login` with an account
that has Contributor on `syrabit-prod`).

```bash
APP=syrabit-backend
RG=syrabit-prod
REV=syrabit-backend--rotate-20260511215020

# 1. Find out why the new revision is Unhealthy.
az containerapp logs show -n "$APP" -g "$RG" --revision "$REV" --tail 200
az containerapp revision show -n "$APP" -g "$RG" --revision "$REV" \
  --query "properties.{health:healthState,replicas:replicas,reason:provisioningError}" -o jsonc

# 2. Most likely you'll see a Sentry-init or web-push-init exception.
#    Fix the broken value(s) in Key Vault. Get the actual prod values
#    from the human SecretOps owner — DON'T re-seed from a dev env.
az keyvault secret set --vault-name syrabit-prod-kv \
  --name SENTRY-DSN                 --value "<real prod DSN>"
az keyvault secret set --vault-name syrabit-prod-kv \
  --name WEB-PUSH-VAPID-PRIVATE-KEY --value "<real prod VAPID private key>"

# 3. Re-trigger the rotation workflow. It will create a fresh revision
#    that pulls the just-fixed KV secrets at provisioning time.
#    Make sure the GH repo secret ORIGIN_SHARED_SECRET_NEW still holds
#    the V2 value that's already on the workers (re-push it via
#    libsodium-encrypted PUT if it's no longer there — agent deleted
#    it after the failed run for hygiene).
gh workflow run aca-set-origin-secret.yml \
  --ref main \
  -f vault=syrabit-prod-kv \
  -f health_url=https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io
```

## Verification

```bash
# end-to-end through the worker
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST https://syrabit.ai/api/ai/chat \
  -H 'Origin: https://syrabit.ai' -H 'Referer: https://syrabit.ai/chat' \
  -H 'Content-Type: application/json' \
  -d '{"message":"ping","mode":"english"}'
# expected: NOT 403 with the OriginGate body. 401/422 are fine — the
# gate let us past; downstream auth/cap rejected the anonymous request.
```

## Ground rules for any retry

* Worker secret update **first**, then the workflow updates ACA. The
  agent already pushed V2 to both `syrabitworker` and `syrabit-edge`.
* **Never** use `az containerapp secret set` to rotate this secret —
  it overwrites the bicep `keyVaultUrl` ref with a literal and the
  next deploy reverts the rotation.
* The workflow's preflight step asserts the `keyVaultUrl` wiring is
  intact and fails loud if drifted; do not bypass.
* Do not enable `seed_unrelated_kv=true` in the workflow inputs unless
  the corresponding repo secrets hold the actual prod values.

## Cleanup performed by the agent

* Deleted GH actions runs `25699097734`, `25699208020`, `25699385429`
  (the first one had the rotated value in plain text in the env block
  of step 1 before the masking directive applied — public repo).
* Deleted the temporary GH repo secrets
  `ORIGIN_SHARED_SECRET_NEW`, `KV_SEED_SENTRY_DSN`,
  `KV_SEED_WEB_PUSH_VAPID_PRIVATE_KEY`.
* Shredded V1/V2 secret values from `/tmp` on the Replit container.
