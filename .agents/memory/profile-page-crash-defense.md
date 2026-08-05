---
name: Profile page crash defensive fixes
description: Root causes confirmed and fixed for the "Couldn't load profile" error on /profile. Two separate bugs.
---

## Root Causes (confirmed Aug 2026)

### Bug 1 — apiClient() never sent the Bearer token → 401 on /user/profile & /user/stats
`apiClient()` in `api.jsx` used `axios.create({ baseURL, withCredentials: true })`.
A freshly-created axios instance does NOT inherit the module-level `_authToken` used by `authConfig()`.
`get_current_user` on the backend reads `Authorization: Bearer` (or `X-User-JWT`) — not cookies.
So every `/user/profile` and `/user/stats` call returned 401, even for authenticated users.
`/users/me` succeeded because `AuthContext` passes the token explicitly on each call.

**Fix:** `apiClient()` now spreads `authConfig()`:
```js
export const apiClient = () =>
  axios.create({ baseURL: API_BASE, ...authConfig() });
```

### Bug 2 — PaymentHistory set state to object, not array → payments.map crash
`getPaymentHistory()` returns `{ data: { payments: [...] } }`.
The component did `setPayments(res.data || [])` — setting state to the wrapper object.
`.map()` on an object throws, crashing the profile page render.

**Fix:** `setPayments(Array.isArray(res.data) ? res.data : (res.data?.payments || []))`.

## Why it was hard to catch before
Both bugs only manifest after authentication is fully established: the user was authenticated (users/me returned 200), but apiClient()-based calls returned 401 immediately after, triggering the "Couldn't load profile" error state before the render crash could be seen.

## Status
Both bugs fixed and pushed. Profile page loads cleanly with no console errors.
