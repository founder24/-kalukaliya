# R2 lifecycle rules (IaC)

Version-controlled R2 lifecycle rule definitions. The rules themselves live
on the Cloudflare side (per-bucket); these JSON files + `apply.sh` make the
rules **reproducible from this repo** so a fresh operator can re-apply them
without guessing.

| File | Bucket | Rules |
|---|---|---|
| `syrabit-assets.json` | `syrabit-assets` | `assets-cold-to-ia-30d` (30d → Infrequent Access) |
| `syrabit-media.json`  | `syrabit-media`  | `media-cold-to-ia-30d` (30d → Infrequent Access), `media-logpush-delete-14d` (14d delete on `logpush/` prefix) |

See [`../../docs/cloudflare-r2-lifecycle.md`](../../docs/cloudflare-r2-lifecycle.md)
for the rationale and the cost-map context.

## Apply

```sh
export CLOUDFLARE_API_TOKEN=...   # token with R2 Edit on the prod account
./apply.sh
```

`apply.sh` calls `wrangler r2 bucket lifecycle set <bucket> --file <json>`
for each bucket and then runs `--verify` to print the resulting rules.

## Verify only

```sh
./apply.sh --verify
```

Capture the output in the monthly cost-review PR (see
`docs/cloudflare-monthly-cost-review.md`) as evidence the rules are still
active in the target account.
