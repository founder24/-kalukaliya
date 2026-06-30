#!/usr/bin/env bash
# setup-vectorize-indexes.sh
#
# Creates all 6 required metadata indexes on the Syrabit Vectorize index.
# Run this once after creating the index, and again whenever a new field
# needs to be filterable.
#
# Prerequisites:
#   wrangler installed and authenticated:  npx wrangler login
#   CF_INDEX_NAME  — Vectorize index name (default: syrabit-rag)
#
# Usage:
#   ./scripts/setup-vectorize-indexes.sh
#   CF_INDEX_NAME=syrabit-rag-staging ./scripts/setup-vectorize-indexes.sh
#
# After running, verify with:
#   GET /admin/rag/vectorize/info  (health.status should be "ok")

set -euo pipefail

INDEX_NAME="${CF_INDEX_NAME:-syrabit-rag}"

echo "==> Setting up metadata indexes on Vectorize index: ${INDEX_NAME}"
echo ""

declare -A INDEXES=(
    ["subjectId"]="string"
    ["chapterId"]="string"
    ["topicId"]="string"
    ["medium"]="string"
    ["sourceType"]="string"
    ["chunkType"]="string"
)

CREATED=0
SKIPPED=0
FAILED=0

for FIELD in "${!INDEXES[@]}"; do
    TYPE="${INDEXES[$FIELD]}"
    echo -n "  [${FIELD}] (${TYPE}) ... "

    OUTPUT=$(npx wrangler vectorize create-metadata-index "${INDEX_NAME}" \
        --property-name "${FIELD}" \
        --type "${TYPE}" 2>&1) || true

    if echo "$OUTPUT" | grep -qi "already exists"; then
        echo "already exists (skipped)"
        SKIPPED=$((SKIPPED + 1))
    elif echo "$OUTPUT" | grep -qi "success\|created"; then
        echo "created"
        CREATED=$((CREATED + 1))
    else
        echo "FAILED"
        echo "    ${OUTPUT}" | head -5
        FAILED=$((FAILED + 1))
    fi
done

echo ""
echo "==> Done: ${CREATED} created, ${SKIPPED} already existed, ${FAILED} failed"
echo ""

if [ "$FAILED" -gt 0 ]; then
    echo "Some indexes failed to create. Check output above."
    echo "You can also create them individually:"
    echo ""
    for FIELD in subjectId chapterId topicId medium sourceType chunkType; do
        echo "  npx wrangler vectorize create-metadata-index ${INDEX_NAME} --property-name ${FIELD} --type string"
    done
    exit 1
fi

echo "All 6 metadata indexes are present."
echo "Verify via: curl -s <admin-api>/admin/rag/vectorize/info | jq '.health'"
