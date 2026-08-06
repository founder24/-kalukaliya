#!/usr/bin/env bash
# Fill in missing chapters for partially-ingested AHSEC subjects.
# Runs WITHOUT --force so already-completed chapters are skipped.
set -euo pipefail

cd "$(dirname "$0")/.."

SUBJECTS=(
  "Accountancy"
  "Business Studies"
  "Chemistry"
  "Economics"
  "Mathematics"
  "Physics"
)

for subj in "${SUBJECTS[@]}"; do
  echo ""
  echo "========================================"
  echo "Filling gaps: $subj"
  echo "========================================"
  python3 -m scripts.ahsec_ingest \
    --class11 --class12 \
    --medium en \
    --subject "$subj" \
    --delay 3 \
    || echo "  [WARNING] $subj finished with errors — continuing"
  sleep 2
done

echo ""
echo "=== FILL GAPS COMPLETE ==="
