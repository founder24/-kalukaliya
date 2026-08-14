#!/usr/bin/env bash
# Fill in missing chapters for partially-ingested AHSEC subjects.
# Runs WITHOUT --force so already-completed chapters are skipped.
# Covers all AHSEC subjects that have pre-seeded chapter stubs.
#
# Usage:
#   bash ahsec_fill_gaps.sh               # both EN and AS passes
#   bash ahsec_fill_gaps.sh --medium en   # English only
#   bash ahsec_fill_gaps.sh --medium as   # Assamese only
set -euo pipefail

# ── Cross-script mutex ────────────────────────────────────────────────────────
# Prevent concurrent ingestion runs that would exhaust the MongoDB pool and
# deadlock on the progress JSONL lock.  Uses a non-blocking flock so the script
# exits immediately instead of queuing behind another run.
LOCK_FILE="/tmp/syrabit_ingest.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[ahsec_fill_gaps] Another ingestion script is already running." >&2
  echo "  Lock file: $LOCK_FILE" >&2
  echo "  Wait for it to finish, then re-run this script." >&2
  exit 0
fi
echo $$ > /tmp/syrabit_ingest.pid
# ── Lock acquired — proceed ───────────────────────────────────────────────────

cd "$(dirname "$0")/.."

# ── Parse optional --medium flag ──────────────────────────────────────────────
MEDIUM=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --medium)
      MEDIUM="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -n "$MEDIUM" && "$MEDIUM" != "en" && "$MEDIUM" != "as" ]]; then
  echo "ERROR: --medium must be 'en' or 'as' (got '$MEDIUM')" >&2
  exit 1
fi

SUBJECTS=(
  "Accountancy"
  "Biology"
  "Business Studies"
  "Chemistry"
  "Economics"
  "History"
  "Mathematics"
  "Physics"
  "Political Science"
)

# ── Pass 1: English ───────────────────────────────────────────────────────────
if [[ -z "$MEDIUM" || "$MEDIUM" == "en" ]]; then
  echo ""
  echo "========================================"
  echo "=== PASS 1: English (EN) fill-gaps   ==="
  echo "========================================"

  for subj in "${SUBJECTS[@]}"; do
    echo ""
    echo "----------------------------------------"
    echo "EN — Filling gaps: $subj"
    echo "----------------------------------------"
    python3 -m scripts.ahsec_ingest \
      --class11 --class12 \
      --medium en \
      --subject "$subj" \
      --delay 3 \
      || echo "  [WARNING] $subj EN finished with errors — continuing"
    sleep 2
  done
fi

# ── Pass 2: Assamese — DISABLED (coming soon) ────────────────────────────────
# Assamese notes are paused. Re-enable by removing the early-exit guard below
# and passing --medium as (or no --medium flag) to this script.
if [[ -z "$MEDIUM" || "$MEDIUM" == "as" ]]; then
  if [[ "$MEDIUM" == "as" ]]; then
    echo ""
    echo "=== Assamese fill-gaps skipped — AS notes are coming soon ==="
  fi
  # AS pass intentionally disabled; skip silently when called without --medium
fi

echo ""
echo "=== FILL GAPS COMPLETE (EN only — AS coming soon) ==="
