# GitHub Deploy SSH Key Setup for Cloud Build

Wires up Step 0 of `cloudbuild.yaml` so Cloud Build can authenticate to GitHub
over SSH using a read-only deploy key stored in GCP Secret Manager.

Run **once** per project. After this, every Cloud Build triggered by a push will
complete Step 0 without manual intervention.

---

## Prerequisites

- `gcloud` CLI authenticated with an account that has:
  - `roles/secretmanager.admin` on project `blissful-acumen-495019-t6`
  - `roles/iam.securityAdmin` (or `roles/secretmanager.admin`) to set IAM bindings
- Access to GitHub: `founder24/-kalukaliya` → Settings → Deploy keys

---

## Option A — Automated (recommended): run the setup script

The script `infra/scripts/setup-github-deploy-key.sh` does every GCP step
automatically (key generation, Secret Manager create/version, IAM grants,
policy verification) and prints the public key for you to paste into GitHub.

```bash
# From repo root in Cloud Shell:
bash infra/scripts/setup-github-deploy-key.sh
```

Then follow the printed instructions to add the public key to GitHub (Step 3 below).

---

## Option B — Manual step-by-step

### Step 1 — Generate the ed25519 key pair

```bash
ssh-keygen -t ed25519 -C "cloud-build@syrabit" \
  -f /tmp/syrabit_deploy_key -N ""

# Outputs:
#   /tmp/syrabit_deploy_key       ← private key  (goes to Secret Manager)
#   /tmp/syrabit_deploy_key.pub   ← public key   (goes to GitHub)
```

### Step 2 — Store the private key in GCP Secret Manager

```bash
gcloud secrets create GITHUB_DEPLOY_SSH_KEY \
  --replication-policy=automatic \
  --data-file=/tmp/syrabit_deploy_key \
  --project=blissful-acumen-495019-t6

# If the secret already exists, add a new version instead:
# gcloud secrets versions add GITHUB_DEPLOY_SSH_KEY \
#   --data-file=/tmp/syrabit_deploy_key \
#   --project=blissful-acumen-495019-t6
```

### Step 3 — Add the public key to GitHub (UI step)

```bash
# Print the public key:
cat /tmp/syrabit_deploy_key.pub
```

1. Go to **GitHub → `founder24/-kalukaliya` → Settings → Deploy keys**
2. Click **Add deploy key**
3. Title: `Cloud Build (read-only)`
4. Key: paste the output of the command above
5. Leave **Allow write access** unchecked
6. Click **Add key**

### Step 4 — Grant both Cloud Build SAs access to the secret

Two service accounts need `roles/secretmanager.secretAccessor` on this secret:

1. **Default Cloud Build SA** — used when the trigger has no custom service account
2. **`syrabit-backend-sa@...`** — used when the trigger is configured with this
   custom SA (the hardened setup for this project)

```bash
# Resolve the project number for the default Cloud Build SA:
PROJECT_NUMBER=$(gcloud projects describe blissful-acumen-495019-t6 \
  --format="value(projectNumber)")

# Grant the default Cloud Build SA:
gcloud secrets add-iam-policy-binding GITHUB_DEPLOY_SSH_KEY \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" \
  --project=blissful-acumen-495019-t6

# Grant the custom trigger SA (syrabit-backend-sa):
gcloud secrets add-iam-policy-binding GITHUB_DEPLOY_SSH_KEY \
  --member="serviceAccount:syrabit-backend-sa@blissful-acumen-495019-t6.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" \
  --project=blissful-acumen-495019-t6
```

### Step 4a — Verify the IAM bindings were applied

```bash
gcloud secrets get-iam-policy GITHUB_DEPLOY_SSH_KEY \
  --project=blissful-acumen-495019-t6 \
  --format="table(bindings.role,bindings.members)"
```

Both service accounts should appear under `roles/secretmanager.secretAccessor`.

---

## Step 5 — Pre-flight check: verify GCP prerequisites before triggering a build

Before triggering a build, run the verification script in Cloud Shell to confirm
every GCP-side prerequisite is in place:

```bash
# From repo root in Cloud Shell:
bash infra/scripts/verify-github-deploy-key.sh
```

The script checks:
1. The secret `GITHUB_DEPLOY_SSH_KEY` exists in Secret Manager
2. The `latest` version is in `ENABLED` state
3. The secret value starts with a valid SSH private key header
4. The default Cloud Build SA (`{project_number}@cloudbuild.gserviceaccount.com`) has `secretAccessor`
5. The custom trigger SA (`syrabit-backend-sa@...`) has `secretAccessor`

All 5 checks must pass before proceeding. The script prints fix commands for any
failures and exits non-zero if any check fails.

Also confirm the GitHub deploy key is registered:
- Go to **https://github.com/founder24/-kalukaliya/settings/keys**
- "Cloud Build (read-only)" should appear in the list

## Step 6 — Trigger a build and verify Step 0

Once the pre-flight check passes, trigger a build:

```bash
gcloud builds submit --no-source \
  --config=cloudbuild.yaml \
  --project=blissful-acumen-495019-t6
```

Or just push a commit to `main` to let the Cloud Build trigger fire.

In the Cloud Build logs, Step 0 should end with:
```
=== SSH setup: configuring deploy key for GitHub ===
  ✓ GitHub SSH connectivity verified   ← or the ⚠ variant (both are OK)
=== SSH setup complete ===
```

### Option A — Automated log verification (recommended)

After the build completes, run the verification script in Cloud Shell to get a
machine-readable pass/fail verdict without manual log browsing:

```bash
bash infra/scripts/verify-step0-passed.sh
```

The script:
1. Resolves the most recent build ID automatically (or accepts `BUILD_ID=<id>` env var)
2. Fetches Step 0 log lines via `gcloud builds log`
3. Confirms `=== SSH setup complete ===` is present
4. Confirms no fatal error patterns (permission-denied, secret-not-found, etc.)
5. Prints a direct link to the Cloud Build console for the build

Exit 0 = Step 0 passed. Exit 1 = failed or inconclusive.

### Option B — Manual log check

```bash
# View full log for the most recent build:
BUILD_ID=$(gcloud builds list --project=blissful-acumen-495019-t6 --limit=1 --format="value(id)")
gcloud builds log "${BUILD_ID}" --project=blissful-acumen-495019-t6 \
  | grep -A 5 "SSH setup"

# Expected output:
#   === SSH setup: configuring deploy key for GitHub ===
#     ✓ GitHub SSH connectivity verified
#   === SSH setup complete ===
```

A hard failure (non-zero exit before that line) means either:
- The secret `GITHUB_DEPLOY_SSH_KEY` doesn't exist yet → check Step 2
- The SA running the build lacks `secretAccessor` → check Step 4/4a
- The public key wasn't added to GitHub → check Step 3

Run `bash infra/scripts/verify-github-deploy-key.sh` again to identify the specific gap.

---

## Rotation

To rotate the key (e.g. after a suspected compromise):

```bash
# 1. Generate a new key pair:
ssh-keygen -t ed25519 -C "cloud-build@syrabit" \
  -f /tmp/syrabit_deploy_key_new -N ""

# 2. Add a new version to the existing secret:
gcloud secrets versions add GITHUB_DEPLOY_SSH_KEY \
  --data-file=/tmp/syrabit_deploy_key_new \
  --project=blissful-acumen-495019-t6

# 3. Update the deploy key on GitHub:
#    GitHub → founder24/-kalukaliya → Settings → Deploy keys
#    Delete the old "Cloud Build (read-only)" key, add the new public key.

# 4. Disable the old secret version (optional but recommended):
# gcloud secrets versions disable <OLD_VERSION> \
#   --secret=GITHUB_DEPLOY_SSH_KEY \
#   --project=blissful-acumen-495019-t6
```

The next Cloud Build run picks up the new `:latest` version automatically.
IAM bindings carry over automatically — no need to re-run the setup script.
