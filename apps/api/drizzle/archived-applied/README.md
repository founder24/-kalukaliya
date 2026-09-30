# Archived applied D1 migrations

These SQL files preserve historical migration identities found in the production
`d1_migrations` ledger. They are tracked so the release preflight can verify the
ledger, but they are deliberately outside `drizzle/migrations` and must never be
passed to Wrangler as replay input.

Some archived statements overlap with the active clean-install history. In
particular, `0034_referral_access_rewards.sql` adds
`chat_request_claims.monthly_period`, which is also created by the active
`0036_monthly_chat_claim_fields.sql`. Replaying both histories on an empty
database would fail.

The archived SQL was copied from these Git history entries:

| Archived file | Source commit |
| --- | --- |
| `0033_bilingual_subject_metadata.sql` | `de7e50e2a` |
| `0034_referral_access_rewards.sql` | `604fb2c63` |
| `0035_chapter_slug_redirects.sql` | `ea2734a26` |
| `0035_consumer_referral_points.sql` | `2cc406331` |
| `0037_consumer_referral_identity.sql` | `2cc406331` |
| `0038_consumer_referral_visits.sql` | `2cc406331` |

Keep active migrations in `drizzle/migrations`. Since production has an applied
historical migration with prefix `0038`, assign future migration prefixes from
`0039` onward; do not reuse archived prefixes.