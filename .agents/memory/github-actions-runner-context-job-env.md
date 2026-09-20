---
name: GitHub Actions runner context invalid in job-level env
description: A job-level `env:` block cannot reference the `runner` context; only step-level env/with/working-directory can. Referencing it at job level causes the whole workflow run to fail to parse (0 jobs executed), not just a lint warning.
---

A GitHub Actions workflow with a **job-level** `env:` entry like:

```yaml
jobs:
  my-job:
    env:
      SOME_DIR: ${{ runner.temp }}/subdir   # INVALID at job level
```

fails GitHub's own workflow parser at trigger time. actionlint reports it precisely:

```
context "runner" is not allowed here. available contexts are "github", "inputs", "matrix", "needs", "secrets", "strategy", "vars"
```

**Why:** the `runner` context is only available inside step-level `env:`, `with:`, `working-directory`, and `run:` — not in the job-level `env:` map. GitHub's runtime enforces this at parse time, so a workflow with this mistake can appear to silently execute zero jobs when triggered (looks like a platform glitch, not a YAML syntax error), because the whole run fails before any job starts.

**How to apply:** if a workflow run shows 0 jobs / fails to start with no clear error, run `actionlint` (or `scripts/check-github-actions.sh` if present) against the workflow files first — it will name the exact bad context reference. Fix by moving the `runner.temp`-based value to a step-level `env:`, or just reference `$RUNNER_TEMP` directly inside `run:` shell scripts (GitHub sets it as a real env var in every step, no expression needed).
