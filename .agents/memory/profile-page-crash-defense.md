---
name: Profile page crash defensive fixes
description: What was done to fix the "Something went wrong" ErrorBoundary crash on /profile, and why static analysis couldn't confirm the exact crash point.
---

## The symptom
`syrabit.ai/profile` showed the top-level React ErrorBoundary ("Something went wrong") for authenticated users. The crash was a React render error (not a network error). No production error trace was accessible (Sentry/PostHog only, no console).

## What static analysis found
- No obvious null-dereference in any profile sub-component: all use `profile?.X` optional chaining.
- `isDegreeBoard(undefined)` is safe — `(boardName || '').trim()...` guards nullish input.
- `p.status` (line 66 of old ProfilePage.jsx) was accessed without optional chaining inside `.then()`, but thrown errors inside `.then()` are caught by `.catch()` — so this alone cannot cause a React render crash.
- `subscription_tier: Literal["free", "pro"]` in the User model did NOT include "starter" or "premium", which would cause a Pydantic v2 ValidationError when loading users with those tiers from MongoDB → 500 from `/users/me` → user=null → AuthGuard redirects to /login. Not a render crash.
- Root cause was never confirmed — the exact JS error message needed Sentry/PostHog access.

## What was fixed (defensive)
1. **`loadProfile` useCallback** — extracted fetch logic so both the initial `useEffect` and the retry button share the same code path.
2. **`profileError` state + error UI** — when `loading=false` and `profile=null/invalid`, renders "Couldn't load profile" + "Try Again" button instead of falling through to the full render with null profile. This prevents ANY sub-component crash on null data.
3. **Null guard on `profileRes.data`** — checks `if (!p || typeof p !== 'object')` before calling `setProfile(p)`.
4. **Optional chaining on `p?.status` / `p?.deletion_hard_at`** — defensive guard.
5. **`statsRes.data` null-guard** — `statsRes.data || {...defaults}` in both fetch paths.
6. **`refreshData` null-guard** — validates `r.data` before calling `setProfile` / `setStats`.
7. **`subscription_tier` Literal type** — widened to `Literal["free", "starter", "pro", "premium"]` so Beanie/Pydantic v2 doesn't reject documents with those tiers.

**Why:** If ANY profile sub-component has a latent null-dereference that only triggers on certain user data shapes (e.g., missing board_name, unusual subscription tier), the new error state catches it cleanly instead of crashing to the global ErrorBoundary.

## Next debugging step if crash recurs
Check PostHog `error_boundary_triggered` events for `page=/profile`. The `error_message` and `component_stack` fields there will pinpoint the exact file and line.
