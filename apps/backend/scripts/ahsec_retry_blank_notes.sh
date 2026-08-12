#!/usr/bin/env bash
# Re-ingestion runner for chapters that got blank notes during the token-limit outage.
#
# Steps:
#   1. Run ahsec_retry_blank_notes.py to find blank chapters and unlock them
#      in the progress file (removes "done" entries for affected PDFs).
#   2. Run ahsec_fill_gaps.sh to re-generate notes for all unlocked chapters.
#      The fill-gaps script runs WITHOUT --force, so chapters that already
#      have good notes are safely skipped — only blank ones are regenerated.
#
# Usage:
#   cd apps/backend && bash scripts/ahsec_retry_blank_notes.sh
#   cd apps/backend && bash scripts/ahsec_retry_blank_notes.sh --medium en
#   cd apps/backend && bash scripts/ahsec_retry_blank_notes.sh --medium as
#   cd apps/backend && bash scripts/ahsec_retry_blank_notes.sh --dry-run
#
# --medium en   Only unlock and re-process English notes
# --medium as   Only unlock and re-process Assamese notes
# (no flag)     Unlock and re-process both EN and AS notes
# --dry-run     Report blank chapters without making any changes

set -euo pipefail

cd "$(dirname "$0")/.."

MEDIUM_ARG=""
DRY_RUN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --medium)
      MEDIUM_ARG="--medium $2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="--dry-run"
      shift
      ;;
    *)
      shift
      ;;
  esac
done

echo ""
echo "========================================"
echo "=== Step 1: Discover & unlock blank chapters (EN only — AS coming soon)"
echo "========================================"
# Assamese notes are paused — only unlock/retry English blank chapters.
# When AS is re-enabled, remove the override below.
if [[ -z "$MEDIUM_ARG" ]]; then
  MEDIUM_ARG="--medium en"
fi
# shellcheck disable=SC2086
python3 -m scripts.ahsec_retry_blank_notes $MEDIUM_ARG $DRY_RUN

if [[ -n "$DRY_RUN" ]]; then
  echo ""
  echo "Dry-run complete — no changes made.  Re-run without --dry-run to apply."
  exit 0
fi

echo ""
echo "========================================"
echo "=== Step 2: Re-run fill-gaps to regenerate blank notes"
echo "========================================"
# Pass the medium restriction through so fill-gaps only runs the relevant pass.
# shellcheck disable=SC2086
bash scripts/ahsec_fill_gaps.sh $MEDIUM_ARG

echo ""
echo "=== RETRY BLANK NOTES COMPLETE ==="
