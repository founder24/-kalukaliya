#!/bin/bash
# Auto-sync Replit → GitHub after every checkpoint (post-commit hook).
# Requires GITHUB_TOKEN env var. Fails silently if token is missing.

REPO="https://github.com/founder24/-kalukaliya.git"
BRANCH="main"

if [ -z "$GITHUB_TOKEN" ]; then
  echo "[sync-to-github] GITHUB_TOKEN not set — skipping push" >&2
  exit 0
fi

AUTHED_URL="https://${GITHUB_TOKEN}@github.com/founder24/-kalukaliya.git"

# Run in background so it never blocks the commit or UI
(
  if git push "$AUTHED_URL" "$BRANCH" --quiet 2>&1; then
    echo "[sync-to-github] ✓ Pushed to GitHub ($BRANCH)"
  else
    echo "[sync-to-github] Fast-forward failed — retrying with --force-with-lease" >&2
    git push "$AUTHED_URL" "$BRANCH" --force-with-lease --quiet 2>&1 \
      && echo "[sync-to-github] ✓ Force-pushed to GitHub ($BRANCH)" \
      || echo "[sync-to-github] ✗ Push failed (force-with-lease also failed)" >&2
  fi
) &

exit 0
