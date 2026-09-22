---
name: Release evidence limits
description: Constraints when using the connected GitHub workflow as production verification evidence
---

The connected GitHub API is reliable for workflow, job, step, and artifact metadata, but workflow log and artifact archive downloads may return 403 even when the run and artifact are visible. Treat job conclusions and live probes as the evidence surface unless archive access is independently available.

**Why:** The release workflow can successfully deploy and pass the chat-performance job while the overall run fails in an unrelated guarded check. Archive denial makes the job result useful for pass/fail status but insufficient for extracting the detailed performance report.

**How to apply:** Verify deployment and named job conclusions separately from the overall workflow conclusion. For authenticated checks, enable the guarded path only when the complete disposable fixture set is provisioned; a student token alone is not enough for the full authenticated routing job.