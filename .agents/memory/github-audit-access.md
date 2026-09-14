---
name: GitHub audit access
description: Limits on auditing repository security and protection settings through the GitHub API
---

Public GitHub API data is sufficient for pull-request files, comments, reviews, and check-run conclusions. Dependabot alerts, code scanning, secret scanning, and branch-protection endpoints require a valid authenticated token; a failed token must not be treated as an empty result.

**Why:** An unauthenticated request returns HTTP 401 for those endpoints, so reporting “no alerts” would be misleading.

**How to apply:** When the repository audit needs private security or repository-policy data, verify authentication first and report those areas as unavailable if the token is rejected. Do not retry by exposing or copying the token.