# R2 Lifecycle Rules — Syrabit.ai

**Owner**: infra · **Last reviewed**: 2026-05-03 · **Referenced by**:
`docs/cloudflare-cost-map.md` (R2 + Logpush rows).

R2 lifecycle rules are configured **dashboard-side** (Cloudflare → R2 →
`<bucket>` → Settings → Object lifecycle rules) or via `wrangler r2 bucket
lifecycle` / the R2 REST API. There is no application code change — the
rules apply to every object regardless of how it was written, so
`r2_storage.py` does not need to know about them.

This doc is the source of truth for **what** the rules should be, so a fresh
operator can re-apply them after a bucket rebuild without guessing.

---

## Rule summary

| Bucket / prefix | Trigger | Action | Why |
|---|---|---|---|
| `syrabit-assets` (entire bucket) | Object age ≥ 30 days since last modification | Transition storage class to **Infrequent Access** | Student PDFs are read heavily for the first week (assignment / revision cycle) and rarely afterwards. IA storage is materially cheaper per GB-month and Class B (read) ops on cold objects are negligible. |
| `syrabit-media` (entire bucket) | Object age ≥ 30 days since last modification | Transition storage class to **Infrequent Access** | Generated images / OG cards. Same access pattern: hot for the first crawl/social-share window, then cold. |
| `syrabit-media`, prefix `logpush/` | Object age ≥ 14 days since creation | **Delete** | Logpush dataset writes accumulate forever otherwise; 14d covers our incident-review window and matches the cap row in the cost map. |

> **Multipart-upload hygiene**: every rule above also has the implicit
> "abort incomplete multipart uploads after 7 days" companion rule enabled.
> Cloudflare adds it automatically when you create the first lifecycle rule
> on a bucket; leave it on.

---

## Apply via `wrangler` (recommended — version-controllable)

The JSON below is committed to the repo at
[`infra/r2-lifecycle/`](../infra/r2-lifecycle/) and applied via
[`infra/r2-lifecycle/apply.sh`](../infra/r2-lifecycle/apply.sh):

```sh
export CLOUDFLARE_API_TOKEN=...   # token with R2 Edit on the prod account
./infra/r2-lifecycle/apply.sh             # apply + verify
./infra/r2-lifecycle/apply.sh --verify    # verify only
```

The script runs `wrangler r2 bucket lifecycle set <bucket> --file <path>`
for each bucket against the production account
(`CF_AI_GATEWAY_ACCOUNT_ID`) and prints the resulting rules.

### `syrabit-assets.json`

```json
{
  "rules": [
    {
      "id": "assets-cold-to-ia-30d",
      "enabled": true,
      "conditions": { "prefix": "" },
      "transitions": [
        {
          "condition": { "type": "Age", "maxAge": 2592000 },
          "storageClass": "InfrequentAccess"
        }
      ],
      "abortMultipartUploadsTransition": {
        "condition": { "type": "Age", "maxAge": 604800 }
      }
    }
  ]
}
```

### `syrabit-media.json`

```json
{
  "rules": [
    {
      "id": "media-cold-to-ia-30d",
      "enabled": true,
      "conditions": { "prefix": "" },
      "transitions": [
        {
          "condition": { "type": "Age", "maxAge": 2592000 },
          "storageClass": "InfrequentAccess"
        }
      ],
      "abortMultipartUploadsTransition": {
        "condition": { "type": "Age", "maxAge": 604800 }
      }
    },
    {
      "id": "media-logpush-delete-14d",
      "enabled": true,
      "conditions": { "prefix": "logpush/" },
      "deleteObjectsTransition": {
        "condition": { "type": "Age", "maxAge": 1209600 }
      }
    }
  ]
}
```

(`maxAge` is in seconds: 30d = 2_592_000, 14d = 1_209_600, 7d = 604_800.)

---

## Apply via dashboard (fallback)

1. Cloudflare dashboard → **R2** → select bucket → **Settings** →
   **Object lifecycle rules** → **Add rule**.
2. For `syrabit-assets` and `syrabit-media`:
   - Rule name: `cold-to-ia-30d`
   - Scope: *Entire bucket*
   - Action: **Transition to Infrequent Access** after **30 days**
     since last modification.
3. For `syrabit-media` only, add a second rule:
   - Rule name: `logpush-delete-14d`
   - Scope: *Prefix* = `logpush/`
   - Action: **Delete** objects after **14 days**.
4. Save. Verify under the rule list that all three appear with status
   *Enabled*.

> If the Logpush destination prefix is not `logpush/` (check the Logpush
> job config in the dashboard), update both this doc and the rule prefix
> in the same PR.

---

## Verification

After applying:

```sh
wrangler r2 bucket lifecycle list syrabit-assets
wrangler r2 bucket lifecycle list syrabit-media
```

Each command should print the rules above. Re-run after any bucket migration
or account change.

The monthly cost review (see
[`docs/cloudflare-monthly-cost-review.md`](./cloudflare-monthly-cost-review.md),
**Step 5**) explicitly confirms the R2 storage line item is split between
*Standard* and *Infrequent Access* classes from month two onward, and that
Logpush-driven R2 storage stays under the 5GB cap. If 100% is still
*Standard* after 30+ days of traffic, the transition rules are not active —
Step 5 contains the diagnose-and-re-apply procedure (run
`./infra/r2-lifecycle/apply.sh --verify`, re-apply if needed, ticket
Cloudflare if the rules are present but not acting).
