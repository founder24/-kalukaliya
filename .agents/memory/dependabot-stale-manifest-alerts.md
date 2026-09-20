---
name: Dependabot alerts survive manifest deletion
description: Dependabot open alerts are not automatically dismissed just because the manifest file (e.g. a deleted pyproject.toml) no longer exists on the default branch; they must be dismissed explicitly via the API.
---

Deleting a dependency manifest (e.g. `some-dir/pyproject.toml` removed in a repo restructure) does **not** auto-close the Dependabot alerts that reference it, even long after the deletion is pushed to the default branch and even if new alerts keep getting created against that same stale path afterward. Confirmed case: a manifest deleted months earlier still had dozens of "open" alerts, some with `created_at` timestamps *after* the deletion date.

**Why:** unknown internal reason (possibly a lagging/cached dependency-graph snapshot rather than a fresh git-tree scan), but the practical effect is that stale alerts do not self-heal.

**How to apply:** before treating a large batch of Dependabot alerts as real, check whether the referenced `manifest_path` still exists on the default branch (`GET /repos/{owner}/{repo}/contents/{path}?ref={default_branch}` → 404 means gone). For alerts against a confirmed-deleted manifest, dismiss them explicitly:

```
PATCH /repos/{owner}/{repo}/dependabot/alerts/{alert_number}
{ "state": "dismissed", "dismissed_reason": "inaccurate", "dismissed_comment": "<manifest path> was removed in <commit>; no longer exists on default branch." }
```

Valid `dismissed_reason` values: `fix_started`, `inaccurate`, `no_bandwidth`, `not_used`, `tolerable_risk`. Use `inaccurate` for the "file doesn't exist anymore" case.

Also note: `GET /repos/{owner}/{repo}/dependabot/alerts` does **not** support the `page` query parameter (400 error) — paginate by following the `Link: rel="next"` response header instead.
