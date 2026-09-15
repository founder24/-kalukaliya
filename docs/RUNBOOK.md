# Syrabit Operations Runbook

## Restart the Backend

```bash
az containerapp revision restart \
  --name syrabit-backend \
  --resource-group rg-syrabit \
  --revision <revision-name>

# List revisions to find the active one:
az containerapp revision list --name syrabit-backend --resource-group rg-syrabit -o table
```

## Rotate API Keys

### Sarvam AI Key

1. Generate new key in Sarvam dashboard
2. Update Azure KeyVault: `az keyvault secret set --vault-name kv-syrabit --name SARVAM-API-KEY --value <new-key>`
3. Restart backend to pick up new secret

### Vertex AI Credentials

1. Create new service account key in GCP Console
2. Update KeyVault: `az keyvault secret set --vault-name kv-syrabit --name GOOGLE-CREDENTIALS --value <base64-encoded-json>`
3. Restart backend

## Rollback a Deploy

```bash
# List recent revisions
az containerapp revision list --name syrabit-backend --resource-group rg-syrabit -o table

# Activate previous revision
az containerapp revision activate --name syrabit-backend --resource-group rg-syrabit --revision <previous-revision>

# Route 100% traffic to previous revision
az containerapp ingress traffic set --name syrabit-backend --resource-group rg-syrabit --revision-weight <previous-revision>=100
```

## Check Logs

```bash
# Stream live logs
az containerapp logs show --name syrabit-backend --resource-group rg-syrabit --follow

# Query Log Analytics
az monitor log-analytics query --workspace law-syrabit \
  --analytics-query "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(1h) | order by TimeGenerated desc" \
  --out table
```

## Manually Trigger CI/CD

```bash
# Trigger backend deploy
gh workflow run deploy-all.yml --ref main

# Trigger specific workflow
gh workflow run ci-backend.yml --ref main
```

## Archive AHSEC D1 Import History

The AHSEC D1 importer keeps 90 days of approval, progress, and notes-backup
JSONL records in `apps/backend/.ahsec_d1_state/`. Older records can be moved
into a timestamped archive directory without losing their shared `run_id`.
Archived progress remains part of resume lookup, so a later import does not
repeat chapters only because their old progress was archived.

Preview the operation first:

```bash
cd apps/backend
python3 -m scripts.ahsec_d1_import \
  --archive-history --archive-before-days 90 --dry-run
```

Apply the archive after reviewing the reported run and record counts:

```bash
python3 -m scripts.ahsec_d1_import \
  --archive-history --archive-before-days 90
```

Each archive contains `approvals.jsonl`, `progress.jsonl`,
`notes-backup.jsonl`, and `manifest.json`. The importer writes an
`active-run.json` marker before approving a production run and never archives
that run. If the process is interrupted, the marker intentionally remains so
an operator can inspect and recover the run before archiving again.

## Transfer an AHSEC cleanup preview between runners

Cleanup preview evidence can be transferred explicitly when preview and apply
run on different machines or ephemeral CI runners. The preview JSON contains
the generated-at timestamp, scope fingerprint, filters, and reviewed chapter
IDs. Store it in the approved CI artifact location, then download that exact
file before applying the cleanup:

```bash
# Preview runner
cd apps/backend
python3 -m scripts.ahsec_d1_import \
  --clean-preambles --dry-run \
  --cleanup-preview-report /approved/artifacts/ahsec-cleanup-preview.json

# Apply runner, after the artifact has been reviewed and downloaded
python3 -m scripts.ahsec_d1_import \
  --clean-preambles --confirm-production-write \
  --cleanup-preview-report /approved/artifacts/ahsec-cleanup-preview.json
```

The apply runner does not trust its local state directory or the transferred
file's chapter list by itself. It fetches the current D1 chapter set, rebuilds
the cleanup scope, and requires the report's filters, sorted chapter IDs,
scope fingerprint, change records, and age to match before any write.

### Run the cleanup handoff in GitHub Actions

Use the **AHSEC Cleanup Preview** workflow for an ephemeral preview/apply
handoff between isolated CI runners:

1. Dispatch the workflow with the class, subject, and optional chapter limit.
2. Review the retained `ahsec-cleanup-preview-*` artifact from the completed
   preview job. It contains the JSON report and its SHA-256 checksum and is
   retained for 90 days.
3. For a live apply, set `confirm_production_write` when dispatching the
   workflow. After the preview upload, the apply job pauses at the
   `ahsec-production` environment so the already-uploaded artifact can be
   reviewed before approval; it then downloads that named artifact, verifies
   its checksum, and invokes the importer with
   `--confirm-production-write`.

Configure required reviewers on the `ahsec-production` GitHub environment
before using the workflow for live cleanup. Leaving the confirmation input
disabled produces a review-only run and never starts the apply job.

## Emergency Contacts / Escalation

- **P1 (site down)**: Page on-call immediately via PagerDuty/Opsgenie
- **P2 (degraded)**: Slack #alerts, respond within 15 minutes
- **P3 (non-urgent)**: Create GitHub issue, triage next business day
- **Cloud provider support**: Azure Support (Standard tier), Cloudflare Enterprise
