---
name: Syrabit git divergence pattern
description: How to handle diverged histories between Replit local main and GitHub main (caused by API commits)
---

# Syrabit Git Divergence Pattern

## The rule
Prefer a normal PAT-backed Git push for a large diverged history. Git smart HTTP
may reject a valid token sent as Bearer authentication; use the standard Basic
form with `x-access-token` as the username. Never print the encoded header.

`main` is protected. After reconciling locally, push a temporary branch, open a
pull request, wait for the required status contexts, merge normally, then fetch
and fast-forward local `main` to the merge commit.

**Why:** a direct push successfully uploaded every object but GitHub rejected the
ref update because protected `main` required checks. The same token worked with
Basic authentication after Bearer authentication was reported as invalid.

**How to apply:** create a backup branch before merging, keep user attachments
untracked, use the pull-request path when direct `main` is protected, and confirm
`git rev-list --left-right --count main...origin/main` returns `0 0`.

The Replit GitHub OAuth connector does not authenticate the workspace's normal
Git remote. Its API proxy can also trigger Replit's Cloudflare protection during
bulk blob uploads or on some base64 payloads.

Secret requests are write/consent flows, not secret readers: the secret-request
callback never returns the saved value to the agent. A provider check using a
property from its return value can therefore send an empty credential and
produce a misleading 401.

**Why:** Replit intentionally withholds secret values from the agent; only
secret existence can be inspected safely through the environment-secrets view.

**How to apply:** validate Git credentials through the Git tool's own
authenticated operation, not by treating a secret-request result as a token.

**Why:** a valid OAuth connection successfully read the repository and created
Git blobs, but the Git CLI still used the invalid workspace PAT and the connector
proxy blocked later blob requests before any branch-reference update.

**How to apply:** prefer a valid PAT-backed normal fast-forward push for a large
multi-commit backlog. Use the connector Git Data API for small reconciliations;
send text blobs as UTF-8, throttle writes, and update the ref only after every
blob, tree, and commit has been created successfully.

The GitHub OAuth connector's `repo` scope can create blobs and trees for normal
source files but cannot modify `.github/workflows/*` without GitHub's separate
`workflow` permission; GitHub reports this as a misleading 404 from the Trees
API. A stale workspace `GITHUB_TOKEN` may independently fail normal pushes.

**Why:** A reconciliation succeeded incrementally until the deployment workflow
path, where both proxy and native Octokit tree creation returned 404 despite
healthy repository write access.

**How to apply:** when workflow permission cannot be granted, publish the
authorized source tree, preserve the complete local tip on a clearly named
backup branch, verify the only remaining delta is the workflow file, then
realign `main` to the remote. Never silently drop the protected workflow change.

## How to apply
1. Get current GitHub main SHA via `GET /repos/{repo}/git/refs/heads/main`
2. Get base tree SHA from `GET /repos/{repo}/git/commits/{sha}`
3. Create blobs for each changed file via `POST /repos/{repo}/git/blobs`
4. Create new tree via `POST /repos/{repo}/git/trees` with `base_tree` + changed items
5. Create commit via `POST /repos/{repo}/git/commits`
6. Update ref via `PATCH /repos/{repo}/git/refs/heads/main` (non-force)

This creates a squashed commit on GitHub. The local Replit history diverges from GitHub.
Replit auto-commits at end of each task, creating further divergence.

## After divergence
The next Replit push (if sandbox allows) will fail due to non-fast-forward.
Merge the live remote tip locally; never force-push over task-agent or GitHub
history. If `main` is protected, publish the merge on a temporary branch and use
a pull request.

When a task merge exists only in local history, create the reconciliation
tree against the current GitHub tree and publish it as a new non-force commit.
If an attachment was present only in the local merge tree and is absent from
the GitHub base tree, omit that path from the new tree; do not send a null
deletion entry.

**Why:** the GitHub Trees API can reject a null deletion entry for a path that
does not exist in the base tree. Omitting the local-only path preserves the
remote state without publishing user screenshots or blocking the sync.

Replit can auto-commit an uploaded conflict screenshot while a sync branch is
open. Check the branch tip after pushing and remove the path from Git tracking
before merging; keep the local file untracked if it is still useful.

**Why:** the screenshot was automatically committed after the initial sync
branch push even though it had been intentionally excluded from the merge.

## Reconciling a Replit “merge conflict” banner
When GitHub `main` and local `main` have diverged after API-created commits,
merge the remote tip locally, resolve only the genuine content conflicts, and
publish the merged **tree** as a non-force Git Data API commit based on the
live GitHub tip. Fetch that new commit, compare its tree hash to local `HEAD`,
and only then reset local `main` to `origin/main`.

**Why:** the API publishes a new squashed commit with different ancestry even
when its files exactly match local `HEAD`; resetting before verifying tree
equality could discard local work, while leaving the histories divergent keeps
the Replit Sync UI in an error state.

## CI dep check
The deploy workflow gates on `ci-deps.yml` which runs `bash scripts/compile-deps.sh --check`.
Always run `bash scripts/compile-deps.sh` (not raw pip-compile) to generate requirements.txt —
the script uses Python 3.12, `--strip-extras`, `--no-header`, `--no-upgrade`, and prepends
a standard header comment. The CI diff ignores comment lines but checks package lines exactly.
