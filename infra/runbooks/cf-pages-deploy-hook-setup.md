# CF Pages Deploy Hook Setup

Activating auto-rebuild of syrabitfrontend when content is published.

## Step 1 — Create the deploy hook in Cloudflare dashboard

1. Go to **Cloudflare Dashboard → Workers & Pages → syrabitfrontend → Settings → Build triggers**
2. Click **Add deploy hook**
3. Name it `syrabit-backend-publish` (or any descriptive name)
4. Branch: `main`
5. Copy the generated hook URL — it looks like:
   `https://api.cloudflare.com/client/v4/pages/webhooks/deploy_hooks/<uuid>`

## Step 2 — Store the hook URL in GCP Secret Manager

```bash
# Replace <HOOK_URL> with the URL copied from the CF dashboard
echo -n "<HOOK_URL>" | gcloud secrets create cf-pages-deploy-hook \
  --data-file=- \
  --project=blissful-acumen-495019-t6

# If the secret already exists, add a new version instead:
echo -n "<HOOK_URL>" | gcloud secrets versions add cf-pages-deploy-hook \
  --data-file=- \
  --project=blissful-acumen-495019-t6
```

## Step 3 — Grant Cloud Run access to the secret

The Cloud Run service account needs `secretmanager.versions.access` on this secret.
This is typically granted project-wide already, but run this if the deploy fails:

```bash
gcloud secrets add-iam-policy-binding cf-pages-deploy-hook \
  --member="serviceAccount:syrabit-backend-sa@blissful-acumen-495019-t6.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" \
  --project=blissful-acumen-495019-t6
```

## Step 4 — Deploy the backend

The `cloudbuild.yaml` already includes `CF_PAGES_DEPLOY_HOOK=cf-pages-deploy-hook:latest`
in `--update-secrets`, so a normal Cloud Build deploy will pick it up automatically.

Push to trigger Cloud Build, or trigger manually:

```bash
gcloud builds submit --config cloudbuild.yaml \
  --project=blissful-acumen-495019-t6
```

## Verification

After deploy, publish any chapter or knowledge object from the admin panel.
The backend logs will show one of:

- `Cloudflare Pages rebuild triggered` — hook fired successfully
- `CF_PAGES_DEPLOY_HOOK not set, skipping rebuild trigger` — secret not mounted (check Step 3)
- `Failed to trigger Pages rebuild: ...` — hook URL invalid or network error

CF Pages usually starts the rebuild within 30 seconds and completes within 3–5 minutes.

## How it works

Both content publish paths call `trigger_pages_rebuild()` from `content_publisher_service`:

- **Chapter publish** (`POST /api/v1/admin/content/chapters/{id}/publish`):
  fires rebuild after GCS write + Vertex AI index + IndexNow

- **KnowledgeObject publish** (`POST /api/v1/admin/content/knowledge/{slug}/publish`):
  fires rebuild after the content pipeline completes

- **Bulk KO publish** (`POST /api/v1/admin/content/knowledge/bulk-publish`):
  fires a **single** rebuild after all items in the batch have been processed
  (added as the final FastAPI background task, so it runs last)

The hook call is fire-and-forget with a 10-second timeout and fail-soft logging —
a hook failure never blocks or rolls back a content publish.
