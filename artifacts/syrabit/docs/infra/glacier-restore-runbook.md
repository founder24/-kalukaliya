# S3 Glacier Deep Archive — Restore Runbook

**Status:** Live (Task #551 §A)
**Owner:** infra
**Companion Terraform:** [`artifacts/syrabit/infra/aws/glacier-archive.tf`](../../infra/aws/glacier-archive.tf)
**Companion endpoint:** [`artifacts/syrabit-backend/routes/admin_archive.py`](../../../syrabit-backend/routes/admin_archive.py) — mounted under `/api/admin/archive/*` (the FastAPI app prefixes admin routes with `/api`).
**Four-cloud delegation row:** §A "Object storage (cold compliance)" → AWS S3 Glacier Deep Archive

---

## 1. What lives where

Three buckets, each with its own lifecycle policy, all deliberately
write-rarely / read-almost-never:

| Bucket                              | Contents                                              | Hot → Deep Archive | Expiry  |
|-------------------------------------|-------------------------------------------------------|--------------------|---------|
| `syrabit-razorpay-receipts-prod`    | Razorpay invoices + payment audit trail (DPDP + IT)   | 90 days            | 7 years |
| `syrabit-content-snapshots-prod`    | Chapter / notes / formatter outputs (canonical copy)  | 180 days           | 7 years |
| `syrabit-cw-logs-archive-prod`      | CloudWatch Logs export tail (>14 d)                   | 30 days            | 7 years |

Newly written objects sit in `STANDARD` until the lifecycle rule
transitions them to `DEEP_ARCHIVE`. Restores require an explicit
`s3:RestoreObject` API call and pay an additional egress fee.

---

## 2. SLA and pricing

| Tier        | SLA       | Per-GB cost (request) | Notes                                                   |
|-------------|-----------|-----------------------|---------------------------------------------------------|
| Standard    | 12 hours  | ~$0.02 / GB           | The default. Used by the admin restore endpoint.        |
| Bulk        | 48 hours  | ~$0.0025 / GB         | Cheaper for >100 GB hauls; same end result.             |
| Expedited   | n/a       | n/a                   | **Not supported** for Deep Archive (Glacier Flexible only). |

Storage itself costs **~$0.00099 / GB-month** at rest, which is why
moving the never-touched compliance tail off Cloudflare R2 saves
~$3-5/mo at current data volume (~2 GB receipts + ~50 GB content +
~10 GB log archive).

---

## 3. How to initiate a restore

### Option A — admin endpoint (preferred, audit-logged)

```bash
curl -fsS -X POST https://api.syrabit.ai/api/admin/archive/restore \
  -H "Authorization: Bearer ${ADMIN_JWT}" \
  -H "Content-Type: application/json" \
  -d '{
        "items": [
          {"bucket": "syrabit-razorpay-receipts-prod", "key": "2025/03/inv_PqRsT.pdf"},
          {"bucket": "syrabit-razorpay-receipts-prod", "key": "2025/03/audit-2025-03-12.jsonl"}
        ],
        "tier": "Standard",
        "days_available": 7
      }'
```

Response (per-item):

```json
{
  "ok": true,
  "initiated": 2,
  "failed": 0,
  "results": [
    {"bucket": "...", "key": "...", "status": "restore_initiated",
     "tier": "Standard", "available_for_days": 7}
  ],
  "sla_hours": 12,
  "next_step": "Poll s3:HeadObject for x-amz-restore=ongoing-request=\"false\"; ..."
}
```

The endpoint writes an audit row to the `admin_archive_restore_log`
Mongo collection (admin email + item list + per-item outcome). Recent
requests are visible at `GET /admin/archive/restore/log?limit=50`.

### Option B — direct AWS CLI (DR / out-of-band)

```bash
aws s3api restore-object \
  --bucket syrabit-razorpay-receipts-prod \
  --key 2025/03/inv_PqRsT.pdf \
  --restore-request '{"Days":7,"GlacierJobParameters":{"Tier":"Standard"}}'
```

---

## 4. Polling and download

Glacier Deep Archive restores are async on the AWS side. The admin
endpoint only initiates the thaw; you must poll for completion.

```bash
# Status check — restored copy is ready when ongoing-request="false".
aws s3api head-object \
  --bucket syrabit-razorpay-receipts-prod \
  --key 2025/03/inv_PqRsT.pdf | jq -r '.Restore'

# In progress:  ongoing-request="true"
# Ready:        ongoing-request="false", expiry-date="..."
```

Once ready (typically inside the 12 h Standard SLA), download with a
normal `get-object` / `s3 cp`:

```bash
aws s3 cp \
  s3://syrabit-razorpay-receipts-prod/2025/03/inv_PqRsT.pdf \
  ./inv_PqRsT.pdf
```

The restored copy lives in S3 Standard for `days_available` days
(default 7) before re-archiving automatically — no cleanup required.

---

## 5. Acceptance test (synthetic)

Run after every Terraform apply that touches `glacier-archive.tf`:

```bash
BUCKET=syrabit-content-snapshots-prod
KEY=acceptance/glacier-restore-test-$(date +%s).txt

# 1) Upload directly into Deep Archive (skip the lifecycle wait).
echo "glacier restore acceptance probe" | \
  aws s3 cp - s3://${BUCKET}/${KEY} --storage-class DEEP_ARCHIVE

# 2) Restore via the admin endpoint.
curl -fsS -X POST https://api.syrabit.ai/api/admin/archive/restore \
  -H "Authorization: Bearer ${ADMIN_JWT}" \
  -H "Content-Type: application/json" \
  -d "{\"items\":[{\"bucket\":\"${BUCKET}\",\"key\":\"${KEY}\"}]}"

# 3) Poll s3:HeadObject every 10 min until ongoing-request="false"
#    (≤12 h for Standard tier).
# 4) Download and assert byte-for-byte equality with the upload.
# 5) Cleanup: aws s3 rm s3://${BUCKET}/${KEY}
```

A passing run proves: lifecycle rule live, admin endpoint authorised
+ scoped to the allowlist, Standard-tier restore SLA met, and the
audit log row was written.

---

## 6. Cost guardrails

- Restoring >100 GB in a single calendar month inside the
  `$100 / month MeterD ceiling` (Task #549) requires an
  explicit `# COST-CAP-OVERRIDE: <reason>` comment in any code that
  triggers the bulk operation, plus a Sentry-annotated changelog
  entry. Standard-tier restore at ~$0.02/GB means 100 GB ≈ $2 in
  request fees and a similar egress charge.
- Use `Bulk` tier (~$0.0025/GB) for any restore >50 GB unless the
  12 h SLA matters more than cost.

---

## 7. Decommission notes

If a bucket is ever retired:

1. Run a final inventory (`aws s3api list-objects-v2 ... > inventory.json`).
2. Restore everything still in Deep Archive that has not aged past the
   7-year expiry (Bulk tier, batch in 100 GB chunks).
3. Mirror to the destination (R2, GCS, or another archive home).
4. Delete-marker the source bucket and let the lifecycle expiry sweep.
5. `terraform destroy -target=aws_s3_bucket.<name>` only after the
   inventory diff confirms zero unrestored objects remain.
