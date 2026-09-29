---
name: GitHub audit access
description: Authentication limits for GitHub security, protection, and Actions log archives
---

Public GitHub API data is sufficient for pull-request files, comments, reviews, and check-run conclusions. Dependabot alerts, code scanning, secret scanning, and branch-protection endpoints require a valid authenticated token; a failed token must not be treated as an empty result.

The connected GitHub integration may expose Actions run/job metadata but return 403 for log archives. An already-authorized GitHub secret can still retrieve an archive through GitHub's signed redirect; keep both the credential and redirect URL inside the request process, and never log or return them. A 404 means the archive is unavailable even if job metadata remains readable.

**Why:** Unauthenticated requests return HTTP 401 for security endpoints, and the 2026-09-29 Syrabit audit confirmed that run/job metadata can be readable while the connector blocks archive downloads. Reporting these areas as empty or clean would be misleading.

**How to apply:** For read-only GitHub audits, report run/job metadata coverage separately from raw log-archive coverage. Treat 403/404 archives as unavailable, not clean; use only an existing authorized secret and never expose its value or a signed redirect.