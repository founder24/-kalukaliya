#!/usr/bin/env bash
# Smoke check for the syrabit-embed-worker.
#
# Runs the four-shape probe documented in README.md against either the
# staging or production worker and asserts the response shape matches
# what the backend (`providers/workers_embed.py`) and the Pinecone
# 1024-dim index expect. Exit code is 0 on full pass, non-zero on the
# first failed assertion — safe to wire into CI as a gate after
# `wrangler deploy --env staging`.
#
# Usage:
#   EMBED_STAGING_SHARED_SECRET=... scripts/smoke.sh staging
#   EMBED_SHARED_SECRET=...        scripts/smoke.sh production
#
# Environment:
#   EMBED_STAGING_SHARED_SECRET — required when target=staging
#   EMBED_SHARED_SECRET         — required when target=production
#   EMBED_HOST_OVERRIDE         — optional, overrides the default host
#                                 (useful when probing from a wrangler
#                                 dev tunnel or a preview hostname)

set -euo pipefail

target="${1:-staging}"
case "$target" in
  staging)
    host="${EMBED_HOST_OVERRIDE:-https://embed-staging.syrabit.ai}"
    secret="${EMBED_STAGING_SHARED_SECRET:-}"
    expect_version_suffix="-staging"
    ;;
  production)
    host="${EMBED_HOST_OVERRIDE:-https://embed.syrabit.ai}"
    secret="${EMBED_SHARED_SECRET:-}"
    expect_version_suffix=""
    ;;
  *)
    echo "usage: $0 {staging|production}" >&2
    exit 2
    ;;
esac

if [[ -z "$secret" ]]; then
  echo "error: missing shared secret env var for target=$target" >&2
  exit 2
fi

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "  ok  — $*"; }

echo "=== Embed worker smoke — target=$target host=$host ==="

# 1. /health — must be 200, dims=1024, version suffix matches target.
echo "[1/4] GET /health"
health="$(curl -fsS "$host/health")"
echo "      $health"
dims="$(printf '%s' "$health" | python3 -c 'import json,sys;print(json.load(sys.stdin)["dims"])')"
version="$(printf '%s' "$health" | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])')"
[[ "$dims" == "1024" ]] || fail "/health dims=$dims (want 1024 — Pinecone index width)"
if [[ -n "$expect_version_suffix" ]]; then
  [[ "$version" == *"$expect_version_suffix" ]] \
    || fail "/health version=$version (want suffix '$expect_version_suffix')"
fi
pass "dims=1024, version=$version"

# 2. /version — must echo same dims AND same version string as /health
#    (and carry the staging suffix on staging).
echo "[2/4] GET /version"
ver="$(curl -fsS "$host/version")"
echo "      $ver"
vdims="$(printf '%s' "$ver" | python3 -c 'import json,sys;print(json.load(sys.stdin)["dims"])')"
vversion="$(printf '%s' "$ver" | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])')"
[[ "$vdims" == "1024" ]] || fail "/version dims=$vdims (want 1024)"
[[ "$vversion" == "$version" ]] \
  || fail "/version version=$vversion disagrees with /health version=$version"
if [[ -n "$expect_version_suffix" ]]; then
  [[ "$vversion" == *"$expect_version_suffix" ]] \
    || fail "/version version=$vversion (want suffix '$expect_version_suffix')"
fi
pass "version endpoint matches /health (version=$vversion)"

# 3. POST /embed without auth — must be 401.
echo "[3/4] POST /embed (no auth, expect 401)"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$host/embed" \
        -H 'content-type: application/json' \
        -d '{"texts":["ping"]}')"
[[ "$code" == "401" ]] || fail "unauth /embed returned $code (want 401)"
pass "401 enforced"

# 4. POST /embed with auth — must return 1024-long vectors AND a
#    model_version that (a) is non-empty, (b) matches the version /health
#    + /version reported, and (c) carries the env-appropriate suffix
#    (i.e. ends with `-staging` on staging). The suffix check is the
#    canary that catches the "we accidentally promoted prod's worker
#    code under the staging route" failure mode — without it a bad
#    staging build could still pass CI (Task #437 review).
echo "[4/4] POST /embed (authed, expect 1024-dim vectors)"
body='{"texts":["the mitochondria is the powerhouse of the cell","photosynthesis"]}'
resp="$(curl -fsS -X POST "$host/embed" \
        -H 'content-type: application/json' \
        -H "X-Embed-Secret: $secret" \
        -d "$body")"
EXPECT_SUFFIX="$expect_version_suffix" HEALTH_VERSION="$version" \
python3 - <<PY
import json, os, sys
d = json.loads('''$resp''')
v = d["vectors"]
assert d["dims"] == 1024, f"dims={d['dims']} want 1024"
assert d["count"] == 2, f"count={d['count']} want 2"
assert len(v) == 2 and all(len(x) == 1024 for x in v), \
    f"vector lengths={[len(x) for x in v]} want [1024,1024]"
mv = d.get("model_version") or ""
assert mv, "model_version empty"
hv = os.environ.get("HEALTH_VERSION", "")
assert mv == hv, f"/embed model_version={mv!r} disagrees with /health version={hv!r}"
suffix = os.environ.get("EXPECT_SUFFIX", "")
if suffix:
    assert mv.endswith(suffix), \
        f"/embed model_version={mv!r} missing required suffix {suffix!r} — wrong worker answered?"
print(f"  ok  — count=2, each vector len=1024, model_version={mv}")
PY

echo
echo "=== PASS ($target) ==="
