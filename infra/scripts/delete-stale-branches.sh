#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# delete-stale-branches.sh — Delete all GitHub branches except main.
#
# Repo: https://github.com/founder24/-kalukaliya
#
# Run in Cloud Shell (gh CLI is pre-installed):
#   bash <(curl -fsSL https://raw.githubusercontent.com/founder24/-kalukaliya/main/infra/scripts/delete-stale-branches.sh)
#
# Options:
#   DRY_RUN=true bash ... — list branches without deleting (safe preview)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO="founder24/-kalukaliya"
DRY_RUN="${DRY_RUN:-false}"
PROTECTED=("main")

G="\033[92m"; R="\033[91m"; Y="\033[93m"; B="\033[94m"; X="\033[0m"

echo -e "${B}╔══════════════════════════════════════════════════╗${X}"
echo -e "${B}║      Syrabit — Delete Stale Branches             ║${X}"
echo -e "${B}╚══════════════════════════════════════════════════╝${X}"
echo "Repo     : https://github.com/$REPO"
echo "Dry run  : $DRY_RUN"
echo "Protected: ${PROTECTED[*]}"
echo ""

# ── Auth check ────────────────────────────────────────────────────────────────
if ! gh auth status &>/dev/null 2>&1; then
  echo -e "${Y}Not logged into GitHub CLI. Logging in now...${X}"
  gh auth login --hostname github.com --git-protocol https --web
fi

echo -e "${G}✓${X} GitHub auth OK: $(gh auth status 2>&1 | grep 'Logged in' | head -1 | xargs)"
echo ""

# ── Fetch all branches ────────────────────────────────────────────────────────
echo "Fetching all branches..."
ALL_BRANCHES=$(gh api "repos/$REPO/branches" --paginate --jq '.[].name')
TOTAL=$(echo "$ALL_BRANCHES" | wc -l)
echo "Found $TOTAL branches"
echo ""

is_protected() {
  local branch="$1"
  for p in "${PROTECTED[@]}"; do
    [ "$branch" = "$p" ] && return 0
  done
  return 1
}

DELETED=0
SKIPPED=0
FAILED=0

if [ "$DRY_RUN" = "true" ]; then
  echo -e "${Y}=== DRY RUN — no branches will be deleted ===${X}"
fi

while IFS= read -r BRANCH; do
  if is_protected "$BRANCH"; then
    echo -e "  ${G}✓  KEEP   ${X} $BRANCH"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  if [ "$DRY_RUN" = "true" ]; then
    echo -e "  ${Y}🗑  WOULD  ${X} $BRANCH"
    DELETED=$((DELETED + 1))
  else
    if gh api "repos/$REPO/git/refs/heads/$BRANCH" \
        -X DELETE --silent 2>/dev/null; then
      echo -e "  ${R}🗑  DELETED${X} $BRANCH"
      DELETED=$((DELETED + 1))
    else
      echo -e "  ${Y}✗  FAILED ${X} $BRANCH (may be protected by branch protection rules)"
      FAILED=$((FAILED + 1))
    fi
  fi
done <<< "$ALL_BRANCHES"

echo ""
echo -e "${B}════════════════════════════════${X}"
if [ "$DRY_RUN" = "true" ]; then
  echo -e "${Y}Dry run complete — nothing deleted${X}"
  echo "  Would delete : $DELETED"
  echo "  Protected    : $SKIPPED"
  echo ""
  echo "To actually delete, run:"
  echo "  bash infra/scripts/delete-stale-branches.sh"
else
  echo -e "${G}Cleanup complete!${X}"
  echo "  Deleted   : $DELETED"
  echo "  Protected : $SKIPPED"
  echo "  Failed    : $FAILED"
fi
echo -e "${B}════════════════════════════════${X}"
echo ""
echo "Remaining branches:"
gh api "repos/$REPO/branches" --paginate --jq '.[].name' | sort
