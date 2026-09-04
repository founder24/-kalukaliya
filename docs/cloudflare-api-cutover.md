# Cloudflare API cutover runbook

The Cloudflare API Worker is the primary implementation for student auth,
library/chapter content, chat history and quota, subscriptions/payments, staff
content editing, R2 PYQ uploads, Vectorize reindexing, D1 maintenance, and
authenticated Workers AI generation.

The API Worker natively serves student and payment flows plus public search,
analytics beacons, public configuration, IndexNow submission, changelog, and
all sitemap/feed/LLM crawler artifacts. Every native response is marked
`X-Syrabit-Route: worker-native`.

Staff publishing and offline seed work are now Worker-native. The compatibility
routes retain the established `/api/v1/admin/content/*` response envelopes,
admin-session cookie authentication, and separate `TRANSLATE_CRON_SECRET`
authentication for schedulers. Publish jobs persist in D1 and report every
native step; seed runs persist their per-chapter outcome in D1. Seed work is
bounded to two chapters per invocation: the Worker cron checks every five
minutes, resumes queued runs, and reclaims a stale in-progress lease after its
renewable 15-minute expiry. An interrupted request therefore cannot leave
content seeding permanently blocked.

## Cloudflare-native release

`.github/workflows/deploy.yml` is the sole automatic production entrypoint and
delegates to the canonical Cloudflare-native release implementation in
`.github/workflows/deploy-cloudflare.yml`. The latter can also be dispatched
manually when its optional authenticated/reset-delivery cutover checks are
needed. This Cloudflare-only path:

- validates the D1 configuration and runs the API, edge, and frontend quality
  gates;
- applies remote D1 migrations and deploys `syrabit-api-prod`;
- deploys the edge Worker and Cloudflare Pages frontend; and
- runs direct API Worker, public edge, and frontend smoke checks.

It does not authenticate to GCP, deploy Cloud Run, or read Secret Manager.
Any API Worker
secrets supplied through GitHub Actions are provisioned to both Workers
through Wrangler; omitted values are not written and existing Cloudflare
secrets are preserved. Only secret names are subsequently checked. For a
complete native feature set, add these GitHub Actions secrets before running
the workflow:

`JWT_SECRET`, `ADMIN_JWT_SECRET`, `RESET_TOKEN_SECRET`, `EDGE_SHARED_SECRET`,
`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`,
`RESEND_API_KEY`, `INDEXNOW_API_KEY`, `INDEXNOW_INTERNAL_SECRET`,
`R2_PUBLIC_URL`, `TRANSLATE_CRON_SECRET`, `CF_ACCESS_CLIENT_ID`, and
`CF_ACCESS_CLIENT_SECRET`.

`VITE_BACKEND_URL` is read from the GitHub Actions secret when present and
otherwise defaults to `https://api.syrabit.ai`.

The workflow can intentionally run without these GitHub secrets to deploy
code and D1 migrations. Features whose existing Cloudflare secret is absent
will remain unavailable until that secret is configured; the workflow does
not create placeholder values.

The production edge Worker always uses its `API_WORKER` service binding.
There is no activation secret or alternate backend URL. The release smoke
test requires the public edge health check to report its API Worker
service-binding probe.

1. Apply D1 migrations and run the idempotent Mongo→D1 migration.
2. Pause writes or confirm the migration is dual-writing, then run:

   ```bash
   python3 scripts/migrate-mongo-to-d1.py --validate --sample-size 20
   ```

   The command fails on D1 referential errors, source/target count differences,
   or a deterministic leading-ID sample mismatch. This is a bounded sample
   check, not a cryptographic proof of every row, so source writes must remain
   paused or dual-written for the validation window. Collections intentionally
   absent from Mongo are reported as absent rather than treated as migrated.

3. Deploy through `.github/workflows/deploy.yml`. It synchronizes the Worker
   secrets required by native auth, payments, email, R2 upload URLs, and
   internal generation before deploy. Optional Trustpilot display values are
   supplied directly to the Cloudflare release environment. When none are
   configured, the endpoint intentionally returns `null`. The API Worker
   needs `TRANSLATE_CRON_SECRET` for scheduled seed routes.
4. Run the staged Worker and public-edge smoke test:

   ```bash
    API_WORKER_URL=https://syrabit-api-prod.axomxplain.workers.dev \
     PUBLIC_EDGE_URL=https://api.syrabit.ai \
     INDEXNOW_INTERNAL_SECRET=... \
     STUDENT_TOKEN=... STAFF_TOKEN=... ADMIN_SESSION_TOKEN=... \
     EDGE_SHARED_SECRET=... TRANSLATE_CRON_SECRET=... \
   bash scripts/validate-cloudflare-api-cutover.sh
   ```

   Supply disposable, least-privilege student and staff tokens plus the raw
   value of a disposable `syrabit_admin_session` cookie for the admin test
   user. Do not put tokens or secrets in shell history or logs; use the
   workspace secret mechanism in CI. The full check validates public-edge
   markers for student profile/history/credits/content/chat, staff content,
   admin publishing/RAG/translation reads, scheduled seed status, forged
   payment verification, and forged Razorpay webhook rejection. It creates
   no orders, credits, content, seed runs, or publish jobs. The chat and
   internal-generation probes intentionally consume bounded AI capacity, so
   run them only with disposable cutover accounts and the approved worker
   test budget.
   The script fails if any full-stage credential is missing. For a
   deliberately public-only preflight, set `CUTOVER_STAGE=public`; that is not
   sufficient evidence for a production release. `PUBLIC_EDGE_URL` must point
   to the production edge deployment, so root sitemap/feed/LLM artifacts are
   verified through their real delivery path.

   The Cloudflare-only deploy workflow exposes this as an explicit safety
   gate: choose `validate_authenticated=true`. Configure the six GitHub secrets
   `INDEXNOW_INTERNAL_SECRET`, `CUTOVER_STUDENT_TOKEN`,
   `CUTOVER_STAFF_TOKEN`, `CUTOVER_ADMIN_SESSION_TOKEN`,
   `EDGE_SHARED_SECRET`, and `TRANSLATE_CRON_SECRET` first. The authenticated
   gate is intentionally not run for ordinary code-only deployments.

   Native password-reset request and confirmation routes are validated through
   the public edge. For full email-delivery and single-use-token evidence,
   additionally choose `validate_reset_delivery=true`. That post-deploy gate
   sends a new request for a disposable fixture, waits for a protected
   environment approval, and accepts only a delivered link carrying the fresh
   request nonce. It never uses a real student or staff account, rejects stale
   links from prior releases, and fails the release if the proof fails.
   See `docs/cloudflare-authenticated-cutover-validation.md` for the protected
   environment setup.

## Rollback boundary

Cloud Run was retired on 2026-09-02. A release rollback means redeploying a
known-good API Worker, edge Worker, and Pages build; it must never restore a
Google backend or an `API_WORKER_LIVE` activation toggle.

All API, staff, publishing, seed, search, and scheduled-operation routes must
carry `X-Syrabit-Route: worker-native`. Missing service bindings fail closed.
The decommission record and retained-data plan are in
`docs/gcp-backend-decommission.md`.

## Seed-run interruption rehearsal

The lease-recovery proof belongs with the rollback evidence. Do not record a
production pass until the D1 migrations that add `seed_runs.lease_token`,
`seed_runs.lease_expires_at`, and `seed_runs.is_forced` are applied and the
matching API Worker version is deployed.

The automated Worker/D1 rehearsal is
`apps/api/src/routes/admin-content.contract.test.ts`. It uses the actual
scheduled entrypoint with a three-chapter seed run in which the first chapter's
notes have been persisted but its run-log checkpoint is deliberately missing.
It also covers a provider response that arrives after its run lease is
reclaimed.

The guarantee is **exactly-once durable chapter completion**, not
exactly-once Workers AI invocation. Workers AI does not support a D1
transaction or idempotency key, so a crash after a provider receives a request
but before its fenced D1 chapter commit can result in an at-least-once provider
call. The stale owner cannot write its late response, and only the current
lease owner can commit the chapter outcome. While an invocation is active, it
renews its 15-minute lease every 60 seconds so normal long-running AI calls
are not reclaimed by the five-minute cron. That fenced content commit and the
per-run chapter outcome log are one D1 batch transaction for both ordinary and
forced runs, so a recovered forced retry recognizes a committed chapter rather
than regenerating it. An owner whose lease expires before cron observes it
cannot revive the lease, commit its content, or write a `done` outcome; the
cron reclaims that still-running chapter for retry.

It verifies that:

1. A five-minute cron tick reclaims only a lease that has passed its
   renewable 15-minute expiry, leaving an unexpired active lease untouched.
2. The recovered run resumes in bounded two-chapter batches.
3. The persisted chapter is marked complete without a second provider call,
   while a response from an expired lease is fenced from overwriting the
   current owner's durable result; this applies to both ordinary and forced
   retries.
4. The original run reaches `completed`, and a subsequent staff seed run can
   start and complete.

For the staging/production record, run the same three-chapter scenario using
disposable, unpublished chapters, interrupt the active Worker execution, and
save the following beside the Cloud Run rollback rehearsal: the run ID,
request IDs, lease-expiry timestamp, the two cron timestamps, final
`processed=3`/`failed=0` status, the per-chapter log, and the successful ID of
the next run. Delete the disposable chapters and test run only after the
evidence is retained. Never interrupt or regenerate live curriculum chapters
for this rehearsal.

### Recorded rehearsal — 2026-08-22 (disposable Cloudflare Worker + D1)

- A temporary APAC D1 database and a Worker with a one-minute cron were
  created solely for this test. The three rehearsal chapters were unpublished.
- The native seed request returned run
  `04131b09-b1aa-478c-ba97-6fd035ac7595` (request ID
  `ed65c6b7-c11e-4c47-b8a4-2378333b3964`). An immediate Worker redeploy
  interrupted the active `waitUntil` batch. Its D1 row remained `running`
  with `processed=0`, all three log entries queued, and a held lease.
- After that lease was expired, the scheduled Worker claimed it while an
  independently inserted unexpired control lease stayed `running` with its
  original token. A second interruption was applied while the provider call
  was active; persisted rehearsal notes then exercised the durable
  idempotent-completion path. Cron completion timestamps in the run log were
  `05:00:17.705Z` and `05:01:17.547Z`.
- The original run reached `completed`, `processed=3`, `failed=0`; all three
  chapter entries are `done`, and the lease fields were cleared. The staff
  status poll returned the same result (request ID
  `d5692256-8606-4687-918e-ecabfca8e82a`).
- Once the control lease was released, a new staff seed request succeeded
  (request ID `04515e3a-949e-40d7-9c1a-da36c66628e5`) and run
  `bab2157d-76d3-495f-912f-df0c02a1d7bd` finished
  `completed`, `processed=1`, `failed=0`.
- The disposable Worker and D1 database were deleted after this record was
  captured. No production database rows or curriculum chapters were used.
- After the atomic forced-run hardening, a second disposable Worker/D1
  rehearsal seeded `forced-atomic-recovery` as a forced run with an expired
  lease and one atomically committed `done` chapter outcome. Its real cron
  finalized the run after 120 seconds as `completed`, `processed=1`,
  `failed=0`, preserved `Atomically committed forced notes`, and cleared the
  lease. The second disposable Worker and database were then deleted as well.

### Production recovery rollout — 2026-08-22

- The normal GitHub deployment workflow was started from commit
  `493f88733d8f47a967a7f11a67ae35c331c0354f` (run
  `32554393533`). Its dependency gate and frontend deployment passed, but the
  backend job stopped before the API Worker job because the GCP project
  reported `BILLING_DISABLED` while creating the existing
  `cf-worker-ai-token` Secret Manager secret. No API migration or Worker step
  ran in that failed workflow.
- The exact API Worker release steps from that workflow were then run
  directly: the remote D1 migration ledger applied all pending migrations
  through `0011_seed_run_force.sql`, and a subsequent ledger check reported
  no migrations to apply. The matching production Worker deployed as version
  `25571bae-f9ab-4695-a82f-38fff07133ab` at
  `https://syrabit-api-prod.axomxplain.workers.dev`, with the
  `*/5 * * * *` trigger registered.
- Native Worker routing was confirmed before and after the release with
  request IDs `task293-worker-native-status-pre-20260822` and
  `task293-worker-native-status-post-20260822`. Both returned the expected
  authentication response (`401`) with
  `X-Syrabit-Route: worker-native`; neither request accessed curriculum data.
- Disposable unpublished fixture prefix:
  `task293-20260822T053115Z`. It contained three draft chapters and two
  seed rows. The recovery run was
  `task293-20260822T053115Z-recovery-run`, initially `running` with lease
  token `task293-expired-owner` and expiry `2026-08-22T05:31:14Z`. The
  independent control row was
  `task293-20260822T053115Z-control-run`, with token
  `task293-control-owner` and unexpired expiry `2026-08-22T06:01:15Z`.
- The first production cron tick at 05:35 UTC reclaimed only the expired
  recovery lease. It persisted chapter one without regeneration, completed
  chapter two, and left the run queued with `processed=2`, `failed=0`. The
  next tick at 05:40 UTC completed chapter three; the original run finished
  at `05:41:08.289Z` with `completed`, `processed=3`, `failed=0`, and all
  three per-chapter log entries `done`. Its lease fields were cleared. The
  control row stayed `running` with its original token and future expiry for
  the entire recovery proof.
- After that control proof was captured, the control row was released and
  follow-on run `task293-20260822T053115Z-next-run` completed at
  `05:45:32.036Z` with `processed=1`, `failed=0`.
- The disposable board, class, stream, subject, three chapters, and three
  seed rows were deleted after the snapshots were retained. Independent
  post-cleanup counts were `boards=0`, `classes=0`, `streams=0`,
  `subjects=0`, `chapters=0`, and `seed_runs=0` for the fixture prefix.
- At that time, the legacy workflow could not reach its API job because an
  unrelated GCP billing check failed. The production D1/Worker portion was
  therefore completed with the workflow's exact API commands directly.
  Current releases are Cloudflare-native and must not require GCP billing.
