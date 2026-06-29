---
name: Admin panel audit findings
description: Durable findings and decisions from the admin panel frontend audit and fix session
---

## AWS-Native section removed
`AdminAwsNativePanel` hit `/admin/aws-native/status` and `/admin/aws-native/toggle` which have no backend endpoints. The section was a frontend-only design stub. Removed from SECTIONS array, SECTION_COMPONENTS map, and lazy import in AdminPage.jsx. A `SECTION_REDIRECTS` entry (`awsnative → gcp`) was added so any stale deep links redirect to the implemented GCP panel instead of 404-ing.

**Why:** Every visit to the AWS section generated a silent 404, confusing operators who expected a live status view.

## URL-based section routing
`activeSection` is now mirrored to the URL via `useSearchParams` (`?s=<sectionId>&t=<tab>&st=<subTab>`). `handleNavigate` calls `setSearchParams({ replace: true })` so back/forward work and deep links land on the correct section+tab. One-time restoration on mount via `urlRestoredRef` reads params and sets state without re-running on subsequent renders.

**Why:** Previously all navigation state was ephemeral; reload or a shared URL always landed on Dashboard.

## Shell debugger overlay (AdminShellDebug)
Toggled by `Ctrl+Shift+D` or the Bug icon in the sidebar footer. Rendered inside `<SyraProvider>` so it can call `useSyraContext()` and read `selectedEntity`. Shows: `activeSection`, `sysStatus`, `adminEmail`, `adminName`, auth mode, `navContext` (JSON), `selectedEntity` (JSON), current URL params.

**Why:** No prior introspection tool existed; diagnosing state bugs required devtools.

## Content editor null fallback
- Added `dataLoaded` boolean state (set `true` in `load()` finally block) to distinguish "loading" from "no data."
- Added `selSubject && !dataLoaded` branch rendering a spinner before the chapter list appears.
- Replaced the logically-dead `: null` else branch at the end of the content/stream/subject conditional chain with a visible "No selection active" panel with a "Reset navigation" button.

## GCP panel kept
`AdminGcpPanel` covers Vertex AI, Discovery Engine, and WSS — these are all active parts of the stack. Keep it.

## Section registry — all verified OK
All 16 SECTIONS + roadmap have valid SECTION_COMPONENTS entries. All lazy import paths resolve to existing files. `useSyraContext` and `SyraProvider` are both exported from `syra/SyraContext.jsx`. All API functions used by AdminDashboard and AdminAnalytics are confirmed exported in `api.jsx`.
