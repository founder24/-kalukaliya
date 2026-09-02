# Google Cloud backend decommission

## Production state

Syrabit production is Cloudflare-native:

- `api.syrabit.ai` routes through `syrabitworker-prod`.
- The edge Worker reaches `syrabit-api-prod` through the `API_WORKER` service binding.
- The API Worker stores live application data in D1 and uses R2, KV, Vectorize, and Workers AI.
- Missing Worker bindings fail closed. There is no Cloud Run or public backend fallback.

## Retained data and logs

- D1 is the authoritative production application database. The MongoDB-to-D1 migration included strict source/target count and ID-sample checks.
- MongoDB Atlas is retained for now because `.github/workflows/agent-accuracy-report.yml` still reads it. Do not remove the MongoDB repository secret or Atlas data until that report is retired or moved to D1.
- No Cloud Run application log export is required for continued operation. Google Cloud Logging remains the historical source for the retired service and follows the GCP project's configured log-bucket retention. Export logs before changing that retention if audit history is required.

## Decommission record

On 2026-09-02:

- The `syrabit-backend` Cloud Run service in `asia-south1` was deleted.
- The service had `minScale=1`; deleting it removed the residual baseline compute risk.
- Current GitHub schedules were checked. Native translation, subscription expiry, analytics, and uptime jobs call `https://api.syrabit.ai`; none call Cloud Run or use a GCP deployment credential.
- The obsolete `GCP_SA_KEY` GitHub Actions secret was deleted.
- The checked-in Cloud Run manifests and redeploy scripts were removed to prevent accidental recreation.
- The production edge and API Workers had no `GOOGLE_SA_KEY` secret, so no Cloudflare-side Google credential needed removal.

The project has GCP billing disabled. Artifact Registry consequently rejects list and delete operations with `BILLING_DISABLED`. The related repositories were identified from the retired service's revisions as:

- `asia-south1-docker.pkg.dev/blissful-acumen-495019-t6/syrabit`
- `asia-south1-docker.pkg.dev/blissful-acumen-495019-t6/cloud-run-source-deploy`

If billing is ever temporarily enabled for cleanup, delete both repositories immediately and disable billing again:

```bash
gcloud artifacts repositories delete syrabit \
  --project=blissful-acumen-495019-t6 \
  --location=asia-south1 \
  --quiet

gcloud artifacts repositories delete cloud-run-source-deploy \
  --project=blissful-acumen-495019-t6 \
  --location=asia-south1 \
  --quiet
```

Do not re-enable billing solely to run the application; production has no GCP runtime dependency.

## Post-decommission checks

Verify all of the following:

1. `gcloud run services list --project=blissful-acumen-495019-t6 --region=asia-south1` has no `syrabit-backend` entry.
2. `https://syrabit-api-prod.axomxplain.workers.dev/health` returns `200` with `X-Syrabit-Route: worker-native`.
3. `https://api.syrabit.ai/health` returns `200` with `X-Syrabit-Health-Backend: api-worker`.
4. Protected routes such as `/api/v1/auth/me`, `/api/v1/users/me`, and `/api/v1/payments/history` return the expected authentication response with `X-Syrabit-Route: worker-native`.
