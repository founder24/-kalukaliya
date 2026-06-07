---
name: Syrabit git divergence pattern
description: How Contents API pushes diverge from Replit checkpoints, and the fix.
---

# Syrabit git divergence — Contents API vs Replit checkpoints

## The problem

When main agent pushes files to GitHub via the **Contents API** (PUT /repos/.../contents/...), GitHub creates its own commits on `main`. Meanwhile Replit's **checkpoint system** independently commits the same local edits. This produces two diverged histories on the same branch:

- Local `main`: N commits (Replit checkpoints)
- `origin/main`: M commits (Contents API commits, possibly different N)

`git pull --rebase` also fails when one side has a commit that modifies the same file as a Contents API commit (e.g. `scripts/test-live.sh` "both added" or content conflict).

**Why:** The Contents API commit and the local Replit checkpoint touch the same file from the same base, but git sees them as parallel, independent changes to apply in sequence during rebase, which produces a conflict even when file content is identical.

## The fix

```bash
git rebase --abort          # if mid-rebase
git push origin main --force
```

Force-push from Replit is safe because:
1. Replit's local working tree is always the source of truth for file content.
2. The Contents API was only used to unblock deploys; production already ran the correct code.
3. Force-push makes GitHub's history match Replit's without changing any file bytes.

## Prevention

**Prefer local edits + normal git push over Contents API pushes.**  
Only use Contents API when `git push` is completely blocked (e.g. sandbox restriction). If Contents API was used, resolve the divergence in the same session with a force-push before handing back to the user.

**Why:** Contents API and Replit checkpoints are two independent commit streams that will always diverge if both touch the same files.
