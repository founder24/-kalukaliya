# Runbook — Task #47 Supabase-only auth cutover

**Maintenance window:** weeknight 23:00–01:00 IST (decided 2026-05-09).
**Highest-risk single ticket on the 2026 roadmap.** This runbook covers the
**prep PR** (already merged via this task) and the **destructive PR** that
runs during the window. Read end-to-end before the window opens.

## Why this is split into two PRs

Per user direction (2026-05-09), Task #47 was split:

1. **Prep PR (this task — non-destructive).** Lands the JWKS local
   verifier, the synthetic canary, the reconciliation script, this
   runbook, and a §13 lock annotation. **Nothing in the request hot
   path calls the new verifier yet.** Safe to merge any time.
2. **Destructive PR (next task — runs during the window).** Replaces
   `auth.get_user(token)` with `verify_supabase_jwt(token)` in
   `routes/auth.py:supabase_session` and every authed dependency in
   `auth_deps.py`; rotates the cookie name to `syrabit_session_v2`;
   deletes the four legacy email/password endpoints; removes the
   frontend signup form.

The split is the difference between "a 500-line auth diff that
auto-rolls-back if anyone notices a crash" and "a single env-var
flip during a 2-hour window with on-call watching graphs". The
prep PR makes the destructive PR small enough to review carefully.

## Pre-window checklist (T-24h)

Run these in order. Each line is a hard go/no-go for the window.

```bash
# 1. Reconcile Mongo users → Supabase Auth.
#    Hard-fails if any email/password user is unmirrored.
cd artifacts/syrabit-backend
python scripts/verify_supabase_mirror.py --fail-on-missing

# 2. Confirm the JWKS endpoint is reachable from a prod-region shell.
curl -sSf "$SUPABASE_URL/auth/v1/.well-known/jwks.json" | jq '.keys | length'

# 3. Prove the verifier works against the real JWKS using the canary.
SUPABASE_CANARY_EMAIL=... SUPABASE_CANARY_PASSWORD=... \
  python -c "from aca_jobs.supabase_auth_canary import run_canary; \
             import json; print(json.dumps(run_canary(), indent=2))"

# 4. JWKS module unit tests still green.
pytest tests/test_supabase_jwks.py -q

# 5. Pre-window banner is rendering on the SPA (24h warning).
#    (The banner ships in the destructive PR — verify the copy
#    in the staging deploy before flipping prod.)
```

If any of (1) (2) (3) (4) fails, **postpone the window**.

## During the window (23:00–01:00 IST)

Strict timing inside the window:

| Time   | Action |
|--------|--------|
| 23:00  | Banner already in place 24h. Confirm canary green ≥ 12 consecutive runs. |
| 23:05  | Deploy the destructive PR to ACA. ACA does a rolling restart across 2-30 replicas. |
| 23:10  | Watch `Syrabit/Auth::SupabaseAuthCanary` — must stay 1 across the rollout. |
| 23:15  | Tail Sentry `auth/*` errors. Expected: a transient spike of `Invalid token` from users still holding the old `syrabit_session` cookie (these are silently re-authed via the SPA). Unexpected: any 5xx from `/auth/supabase-session`. |
| 23:30  | Canary still green for 30 min → cutover declared healthy. |
| 23:30–01:00 | Hold the window open, watch graphs. Do not start unrelated work. |

### Key metrics during the window

* `Syrabit/Auth::SupabaseAuthCanary` — must hold at 1.
* `Syrabit/Auth::SupabaseJwksAgeSeconds` — should reset to ~0 right after deploy as each replica cold-fetches.
* `Syrabit/Auth::SupabaseJwksStale` — must stay 0.
* Sentry `auth/*` error rate — expect a brief blip from users whose tab still has the old cookie; should clear within 5 min as the SPA forces re-login.
* `/api/auth/me` 401 rate at the edge — expect a transient spike, settled within 10 min.

## Rollback drill (tabletop before the window)

Rollback is a **feature flag**, not a redeploy. The destructive PR
ships `SUPABASE_ONLY_AUTH=1` as the env-var that gates the new
behavior. If the canary goes red OR the auth-error rate spikes
past 2× baseline:

```bash
# 1. Flip the flag back. (Single ACA env-var change → rolling restart.)
az containerapp update -n syrabit-backend -g syrabit-prod \
   --set-env-vars SUPABASE_ONLY_AUTH=0

# 2. Within ~60s, every replica is back to the legacy
#    `JWT_SECRET`-signed `syrabit_session` cookie path. Users
#    holding the new `syrabit_session_v2` cookie need to re-login
#    once (acceptable — same UX as the cutover).

# 3. Page Founder + post in #ops with the canary screenshot.
```

The 48-hour grace window for `JWT_SECRET` cookies is implemented
by `auth_deps.py` continuing to honor BOTH `syrabit_session` and
`syrabit_session_v2` for 48 hours post-cutover. After the grace
window, the destructive-PR follow-up deletes the legacy branch.

## Why JWKS local verify (architecture note)

The current production path validates Supabase tokens by calling
`_supa_client.auth.get_user(token)` — an outbound HTTPS round-trip
to Supabase **per authed request**. This is the perf and
availability liability:

* **Per-request latency:** ~30-80ms for the round-trip.
* **Outage blast radius:** every Supabase auth-API hiccup = every
  authed request 401s, including users who already have a valid
  signed-in session.
* **Free-tier cost:** counts against Supabase's monthly request
  budget for no incremental security benefit (the JWT is signed
  by Supabase — local signature check is cryptographically
  equivalent).

The new `supabase_jwks.verify_supabase_jwt` flips this to local
RSA verification (~0.5ms) with a 1h cache + 5min stale-on-error
fallback. The stale window is the difference between "Supabase
hiccups for 90 seconds and we 401 nobody" vs "we 401 every
authed user for 90 seconds". The 1h fresh window is the upper
bound on how long a revoked Supabase signing key keeps validating
tokens after rotation; Supabase rotates signing keys on the order
of years, so 1h is well inside the rotation cadence.

## Files touched by the prep PR

* `artifacts/syrabit-backend/supabase_jwks.py` — JWKS verifier + cache.
* `artifacts/syrabit-backend/aca_jobs/supabase_auth_canary.py` — synthetic canary.
* `artifacts/syrabit-backend/scripts/verify_supabase_mirror.py` — reconciliation report.
* `artifacts/syrabit-backend/tests/test_supabase_jwks.py` — hermetic tests.
* `infra/architecture-locked-2026.md` §13 — note updated to credit the prep landing.
* `infra/architecture-matrix.json` §13 row — same.
* `docs/architecture/decisions.md` — entry added for the split.
* `docs/runbooks/task-47-supabase-auth-cutover.md` — this file.

## Files the destructive PR will touch

* `artifacts/syrabit-backend/routes/auth.py` — delete `/auth/signup`,
  `/auth/login`, `/auth/reset-request`, `/auth/reset-confirm`
  + the `_send_password_reset_email` helper. Replace
  `_supa_client.auth.get_user(token)` in `supabase_session` with
  `supabase_jwks.verify_supabase_jwt(token)`. Stop calling
  `create_access_token`; set the Supabase access token as the
  cookie value, rotated to `syrabit_session_v2`.
* `artifacts/syrabit-backend/auth_deps.py` — `get_current_user` /
  `get_current_user_optional` switch from `decode_token` (HS256
  via `JWT_SECRET`) to `verify_supabase_jwt`. Add the
  `SUPABASE_ONLY_AUTH` flag + dual-cookie 48h grace.
* `artifacts/syrabit/src/` — remove signup form route + reset
  routes; redirect to Supabase login.
* `infra/architecture-locked-2026.md` §13 — flip PARTIAL → IMPLEMENTED.
* `infra/architecture-matrix.json` §13 — same.
* `infra/aws/lambda/manifest.json` + `lambda-batch-jobs.tf` — wire
  the canary as `rate(5 minutes)` EventBridge Lambda.
