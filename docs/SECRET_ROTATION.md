# Secret Rotation — V4 Locked

> **Authoritative against:** [`infra/v4-locked-architecture.md`](../infra/v4-locked-architecture.md) §6.
> **Source of truth:** Azure Key Vault `syrabit-prod-kv`.
> **Replicas (read-only):** AWS Secrets Manager `ap-south-1`,
> Cloudflare Secrets per Worker.
> **Sync:** Terraform + GitHub Actions `secrets-sync.yml` runs daily
> at 02:00 UTC and on-demand on AKV rotation webhook.

---

## §1 — Topology

```
                        ┌──────────────────────┐
                        │  Azure Key Vault     │
                        │  syrabit-prod-kv     │  ← SoT (rotated first)
                        │  (eastus2 + geo-rep) │
                        └─────────┬────────────┘
                                  │ Terraform-CI (daily + on rotation)
                ┌─────────────────┼─────────────────┐
                ▼                 ▼                 ▼
       ┌────────────────┐ ┌──────────────┐ ┌──────────────┐
       │ AWS Secrets    │ │ Cloudflare   │ │ GitHub Actions│
       │ Manager        │ │ Secrets      │ │ Repo Secrets  │
       │ (ap-south-1)   │ │ (per Worker) │ │ (deploy only) │
       └────────────────┘ └──────────────┘ └──────────────┘
                │                 │                 │
                └─────────────────┴─────────────────┘
                                  ▼
                        SHA-256 hash check
                        (fails CI on any mismatch)
```

---

## §2 — Rotation cadence

| Secret class | Cadence | Trigger |
|---|---|---|
| `MONGO_URI_ATLAS` | Quarterly | Calendar + DR drill. |
| `JWT_SECRET`, `ADMIN_JWT_SECRET` | Quarterly | Calendar; rotation forces session re-issue. |
| `AZURE_OPENAI_API_KEY` | Per fabric-auth-policy expiry (~90 d) | Azure portal expiry warning. |
| `WORKERS_EMBED_SECRET` | Per-incident or quarterly | Manual; high blast radius if leaked (allows arbitrary embed-worker calls). |
| `RAZORPAY_KEY_SECRET` | Per-incident only | Razorpay dashboard. |
| `SENDGRID_API_KEY` | Per-incident or annual | SendGrid console. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Quarterly | IAM rotation. |
| `PINECONE_API_KEY` | Per-incident only | Pinecone console. |
| `GEMINI_API_KEY` | Per fabric-auth-policy expiry | GCP console. |

---

## §3 — Rotation procedure (canonical)

For any secret in the table above:

1. **Generate the new value** in the upstream provider (Razorpay /
   Pinecone / Azure OpenAI / etc.).
2. **Write the new value to Azure Key Vault** (the SoT). Use a new
   secret version — never overwrite without a version bump:
   ```bash
   az keyvault secret set \
     --vault-name syrabit-prod-kv \
     --name <SECRET_NAME> \
     --value "<new-value>"
   ```
3. **Trigger the sync workflow** (or wait for the daily run):
   ```bash
   gh workflow run secrets-sync.yml --ref main
   ```
4. The workflow:
   - Pulls the latest version from AKV.
   - Pushes to AWS Secrets Manager (`ap-south-1`) under the same key name.
   - Pushes to Cloudflare via `wrangler secret put` for each worker / each env.
   - Pushes to GitHub Actions repo secrets for any deploy-time secret.
   - **SHA-256 hash check:** computes the hash of each value across
     all three stores; **fails the workflow** on any pair mismatch.
5. **Validate** by hitting the affected health endpoint:
   - Mongo: `GET /api/health` returns 200.
   - Embed: `GET /admin/health/embed-stack` returns `ok: true`.
   - Azure OpenAI: `GET /admin/health/llm` returns 200 for the
     `chat_default` slug.
6. **Restart the relevant ACA revision** if the secret is consumed via
   `secretRef` (env vars only refresh on revision restart):
   ```bash
   az containerapp revision restart \
     --name syrabit-backend \
     --resource-group <rg> \
     --revision <latest-revision-name>
   ```
7. **Revoke the old upstream value** in the provider console after
   confirming the new value is live.

---

## §4 — Hash-validation test

The CI step that fails on mismatch:

```yaml
- name: Validate secret hashes across stores
  run: |
    for SECRET in MONGO_URI_ATLAS JWT_SECRET ADMIN_JWT_SECRET WORKERS_EMBED_SECRET ...; do
      AKV_HASH=$(az keyvault secret show --vault-name syrabit-prod-kv --name "$SECRET" --query value -o tsv | sha256sum | awk '{print $1}')
      AWS_HASH=$(aws secretsmanager get-secret-value --region ap-south-1 --secret-id "$SECRET" --query SecretString --output text | sha256sum | awk '{print $1}')
      CF_HASH=$(wrangler secret list --env production | jq -r ".[] | select(.name==\"$SECRET\") | .hash // empty")
      [ "$AKV_HASH" = "$AWS_HASH" ] || { echo "MISMATCH AKV vs AWS for $SECRET"; exit 1; }
      # CF hash format differs; compare only when wrangler exposes it
    done
```

The full list of validated secrets lives in
`infra/secrets-sync/managed-secrets.txt` (consumed by the workflow).

---

## §5 — Removed secrets (Task #347 — never re-add)

`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`,
`BEDROCK_PROXY_AUTH_TOKEN`, `RESEND_API_KEY`, `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, `RAILWAY_TOKEN`, `QUGE5_*`.

The rotation pipeline actively asserts these names are **absent** from
all three stores (the workflow fails if any of them reappear).

---

## §6 — Incident response (suspected leak)

1. Immediately rotate the affected secret per §3.
2. Force-restart the ACA revision so the new value is loaded.
3. Inspect Cloudflare AI Gateway / CloudWatch / Sentry for unexpected
   usage of the leaked credential between the suspected leak time
   and the rotation timestamp.
4. File a post-mortem in `artifacts/syrabit/docs/incidents/`
   referencing the incident's Sentry trace ID.
