#!/usr/bin/env bash
#
# check-env-sync.sh
#
# Validates that environment variables used in code are documented in
# .env.shared, and that variables in .env.shared are actually referenced
# somewhere in the codebase.
#
# Exit 0: all variables are in sync (or only non-critical drift found)
# Exit 1: code references undocumented variables (critical)
#
# Usage:
#   .github/scripts/check-env-sync.sh
#

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

# ─── Configuration ────────────────────────────────────────────────────────

ENV_SHARED=".env.shared"
BACKEND_DIR="apps/backend/app"
EDGE_DIR="apps/edge/src"
FRONTEND_DIR="apps/frontend"

# ─── Extract documented variables from .env.shared ────────────────────────
# Matches lines like: VARIABLE_NAME=value (starts with uppercase letter or _)

if [ ! -f "$ENV_SHARED" ]; then
  echo "ERROR: $ENV_SHARED not found"
  exit 1
fi

DOCUMENTED_VARS=()
while IFS= read -r line; do
  # Skip comments and empty lines
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "$line" ]] && continue
  # Extract variable name (everything before the first =)
  if [[ "$line" =~ ^([A-Z][A-Z0-9_]*)= ]]; then
    DOCUMENTED_VARS+=("${BASH_REMATCH[1]}")
  fi
done < "$ENV_SHARED"

echo "=== Environment Variable Sync Check ==="
echo ""
echo "Documented variables in $ENV_SHARED: ${#DOCUMENTED_VARS[@]}"
echo ""

# ─── Scan code for environment variable references ────────────────────────

CODE_VARS=()

# Backend: os.environ.get("VAR") and os.getenv("VAR") and os.environ["VAR"]
if [ -d "$BACKEND_DIR" ]; then
  while IFS= read -r var; do
    CODE_VARS+=("$var")
  done < <(grep -rhoP '(?<=os\.environ\.get\(")[A-Z][A-Z0-9_]*(?=")' "$BACKEND_DIR" 2>/dev/null || true)
  while IFS= read -r var; do
    CODE_VARS+=("$var")
  done < <(grep -rhoP '(?<=os\.getenv\(")[A-Z][A-Z0-9_]*(?=")' "$BACKEND_DIR" 2>/dev/null || true)
  while IFS= read -r var; do
    CODE_VARS+=("$var")
  done < <(grep -rhoP '(?<=os\.environ\[")[A-Z][A-Z0-9_]*(?="\])' "$BACKEND_DIR" 2>/dev/null || true)
fi

# Edge: env.VARIABLE_NAME (Cloudflare Worker bindings)
if [ -d "$EDGE_DIR" ]; then
  while IFS= read -r var; do
    CODE_VARS+=("$var")
  done < <(grep -rhoP '(?<=env\.)[A-Z][A-Z0-9_]*' "$EDGE_DIR" 2>/dev/null || true)
fi

# Frontend: import.meta.env.VITE_* patterns
if [ -d "$FRONTEND_DIR" ]; then
  while IFS= read -r var; do
    CODE_VARS+=("$var")
  done < <(grep -rhoP '(?<=import\.meta\.env\.)[A-Z][A-Z0-9_]*' "$FRONTEND_DIR" --include="*.ts" --include="*.tsx" 2>/dev/null || true)
fi

# Deduplicate code vars
UNIQUE_CODE_VARS=($(printf '%s\n' "${CODE_VARS[@]}" | sort -u))

echo "Unique variables referenced in code: ${#UNIQUE_CODE_VARS[@]}"
echo ""

# ─── Report: Variables used in code but NOT in .env.shared ────────────────

MISSING_FROM_DOCS=()
for var in "${UNIQUE_CODE_VARS[@]}"; do
  found=false
  for doc_var in "${DOCUMENTED_VARS[@]}"; do
    if [ "$var" = "$doc_var" ]; then
      found=true
      break
    fi
  done
  if [ "$found" = "false" ]; then
    MISSING_FROM_DOCS+=("$var")
  fi
done

echo "--- Variables used in code but MISSING from $ENV_SHARED ---"
if [ ${#MISSING_FROM_DOCS[@]} -eq 0 ]; then
  echo "  (none - all code references are documented)"
else
  for var in "${MISSING_FROM_DOCS[@]}"; do
    echo "  UNDOCUMENTED: $var"
  done
fi
echo ""

# ─── Report: Variables in .env.shared but NOT referenced in code ──────────

UNUSED_VARS=()
for doc_var in "${DOCUMENTED_VARS[@]}"; do
  found=false
  for var in "${UNIQUE_CODE_VARS[@]}"; do
    if [ "$var" = "$doc_var" ]; then
      found=true
      break
    fi
  done
  if [ "$found" = "false" ]; then
    UNUSED_VARS+=("$doc_var")
  fi
done

echo "--- Variables in $ENV_SHARED but NOT referenced in code ---"
if [ ${#UNUSED_VARS[@]} -eq 0 ]; then
  echo "  (none - all documented variables are used)"
else
  for var in "${UNUSED_VARS[@]}"; do
    echo "  UNUSED: $var"
  done
fi
echo ""

# ─── Summary and exit code ────────────────────────────────────────────────

echo "=== Summary ==="
echo "  Undocumented (code references missing from .env.shared): ${#MISSING_FROM_DOCS[@]}"
echo "  Unused (in .env.shared but not in code): ${#UNUSED_VARS[@]}"
echo ""

if [ ${#MISSING_FROM_DOCS[@]} -gt 0 ]; then
  echo "FAIL: ${#MISSING_FROM_DOCS[@]} variable(s) used in code are not documented in $ENV_SHARED"
  echo "      Add them to $ENV_SHARED or remove the references."
  exit 1
fi

echo "OK: all environment variables are properly synced"
exit 0
