---
name: Admin panel silent-failure fixes
description: Four admin panel components had silent blank states on API failure; specific patterns and fixes applied.
---

## Rule
Every admin component that fetches data must surface either a loading indicator or an error state — never a blank/empty render with no feedback.

**Why:** In local dev (and transiently in prod), admin API calls return 401/404/500. Without explicit error UI the admin sees a blank panel and has no way to diagnose the cause.

## Fixes applied (June 2026)

### AdminDashboard
- `loading` starts as `true`; `lastRefresh` starts as `null`
- Added `if (loading && !lastRefresh) return <skeleton>` guard at the top of render
- This distinguishes initial load (no skeleton was shown) from background refresh (keep stale data visible)

### AdminConversations
- `error` state existed but was only passed to `useSyraVisibleError()` — never rendered
- Added a red error banner with Retry button inside `{tab === 'conversations' && error && ...}`
- Required adding `AlertTriangle` to lucide imports

### AdminLogsExplorer
- Table `<tbody>` had `{!loading && logs.length === 0}` empty state but no loading indicator
- Added `{loading && logs.length === 0}` loading row with `Loader2` spinner
- Required adding `Loader2` to lucide imports (it wasn't imported)

### AdminContentEditor — chapter flash
- `refreshChapters()` was a plain promise chain with no loading state
- On subject change, `chapters=[]` but ChapterList rendered immediately (empty flash)
- Added `chaptersLoading` state; `refreshChapters` now calls `setChaptersLoading(true/false)`
- useEffect on `selSubject` now explicitly clears `chapters` before fetching
- Render shows spinner (`Loader2`) instead of ChapterList while `chaptersLoading=true`

## Pattern: `adminToken` is always `'cookie'`
`adminToken` = `'cookie'` (literal string) after admin verify — never a real JWT.
`authHeaders('cookie')` returns `{ withCredentials: true }` (no Bearer header) — correct for cookie auth.
The `adminHdr()` helper in `dashboard/shared.jsx` does the same JWT-check and also correctly omits the header.
