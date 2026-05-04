#!/usr/bin/env bash
#
# scripts/digitalocean.sh — dispatcher for driving the syrabit-backend
# and rust-core apps on Digital Ocean App Platform from this workspace
# and from CI.
#
# Task #336 — replaces the deprecated `scripts/railway.sh`. The
# subcommand surface is intentionally close to the old Railway helper
# so existing `pnpm` aliases need only swap the prefix.
#
# Subcommands:
#   deploy    <app>             Build the image, push to DOCR (sha-tagged),
#                               rewrite `image.tag` in the spec, and run
#                               `doctl apps update --wait`. <app> is
#                               either `syrabit-backend` or `rust-core`.
#   redeploy  <app>             Re-roll the latest already-built image
#                               by submitting an empty spec patch.
#   logs      <app> [phase]     Stream runtime / build / deploy logs.
#                               phase ∈ {run, build, deploy} (default run).
#   status    <app>             Print active deployment id, phase, region,
#                               and a live health probe.
#   verify    <app>             GET /api/health (backend) or /health
#                               (rust-core); exit 1 on non-2xx.
#   grpc-check                  grpcurl health.Check on the rust-core
#                               internal :50051 port from a managed alpine
#                               console pod (`doctl apps console`).
#   vars      <app>             List env vars for the app's first service.
#   var-set   <app> KEY=VAL...  Set one or more env vars (triggers redeploy).
#   var-unset <app> KEY...      Delete one or more env vars (triggers redeploy).
#   import-env <app> FILE       Bulk-import env vars from a `KEY=VAL` file
#                               into the spec, replacing every
#                               `PLACEHOLDER_SET_VIA_doctl` entry.
#
# Required env:
#   DIGITALOCEAN_ACCESS_TOKEN     PAT with App Platform + DOCR write.
#                                 `doctl auth init -t "$DIGITALOCEAN_ACCESS_TOKEN"`
#                                 is invoked once per session.
#
# Targeting env (resolved automatically when unset):
#   DO_APP_ID_SYRABIT_BACKEND     UUID of the syrabit-backend app.
#   DO_APP_ID_RUST_CORE           UUID of the rust-core app.
#   DO_HEALTHCHECK_URL_BACKEND    Public health URL (default: https://api.syrabit.ai/api/health).
#   DO_HEALTHCHECK_URL_RUST       Public health URL (default: https://rust-core.syrabit.ai/health).
#
# Exit codes:
#   0  success
#   1  usage / config / auth error
#   2  the deploy or health probe failed on Digital Ocean's side
#
set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." &> /dev/null && pwd)"

DOCR_REGISTRY="${DOCR_REGISTRY:-registry.digitalocean.com/syrabit}"
SPEC_BACKEND="${REPO_ROOT}/.do/app.yaml"
SPEC_RUST="${REPO_ROOT}/.do/app-rust-core.yaml"
HEALTH_BACKEND="${DO_HEALTHCHECK_URL_BACKEND:-https://api.syrabit.ai/api/health}"
HEALTH_RUST="${DO_HEALTHCHECK_URL_RUST:-https://rust-core.syrabit.ai/health}"

# ─── helpers ───────────────────────────────────────────────────────────────
log()  { printf '\033[36m[do]\033[0m %s\n' "$*" >&2; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*" >&2; }
warn() { printf '  \033[33m⚠\033[0m %s\n' "$*" >&2; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }
die()  { fail "$*"; exit 1; }

require() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd is required but not on PATH"
}

require_token() {
  [[ -n "${DIGITALOCEAN_ACCESS_TOKEN:-}" ]] \
    || die "DIGITALOCEAN_ACCESS_TOKEN is not set. Export it before running $SCRIPT_NAME."
}

ensure_doctl_auth() {
  require doctl
  require_token
  # `doctl auth init` is idempotent; suppress its "already authenticated"
  # noise but surface real errors.
  doctl auth init -t "$DIGITALOCEAN_ACCESS_TOKEN" >/dev/null 2>&1 \
    || die "doctl auth init failed — check DIGITALOCEAN_ACCESS_TOKEN scopes."
}

resolve_app() {
  local app="$1"
  case "$app" in
    syrabit-backend)
      if [[ -n "${DO_APP_ID_SYRABIT_BACKEND:-}" ]]; then
        echo "$DO_APP_ID_SYRABIT_BACKEND"; return 0
      fi
      ;;
    rust-core)
      if [[ -n "${DO_APP_ID_RUST_CORE:-}" ]]; then
        echo "$DO_APP_ID_RUST_CORE"; return 0
      fi
      ;;
    *)
      die "unknown app '$app' (expected syrabit-backend|rust-core)"
      ;;
  esac
  doctl apps list --format ID,Spec.Name --no-header \
    | awk -v n="$app" '$2==n {print $1; exit}'
}

spec_for() {
  case "$1" in
    syrabit-backend) echo "$SPEC_BACKEND" ;;
    rust-core)       echo "$SPEC_RUST"    ;;
    *)               die "unknown app '$1'" ;;
  esac
}

dockerfile_for() {
  case "$1" in
    syrabit-backend)
      echo "${REPO_ROOT}/artifacts/syrabit-backend"
      ;;
    rust-core)
      echo "${REPO_ROOT}/backend/rust-core"
      ;;
    *)
      die "unknown app '$1'"
      ;;
  esac
}

build_and_push() {
  local app="$1"
  local short_sha
  short_sha="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "manual-$(date +%s)")"
  local tag="sha-${short_sha}"
  local image="${DOCR_REGISTRY}/${app}:${tag}"
  local context
  context="$(dockerfile_for "$app")"

  require docker
  log "building ${image}"
  doctl registry login >/dev/null
  if [[ "$app" == "rust-core" ]]; then
    docker build -t "$image" -f "${context}/Dockerfile" "$context"
  else
    docker build -t "$image" "$context"
  fi
  log "pushing ${image}"
  docker push "$image"
  echo "$tag"
}

rewrite_tag() {
  # Uses yq if available; falls back to sed line-anchored swap.
  local spec="$1" tag="$2"
  if command -v yq >/dev/null 2>&1; then
    yq -i ".services[0].image.tag = \"$tag\"" "$spec"
  else
    sed -i.bak -E "s|(^[[:space:]]*tag:[[:space:]]*).*$|\1${tag}|" "$spec"
    rm -f "${spec}.bak"
  fi
}

# ─── subcommands ───────────────────────────────────────────────────────────

cmd_deploy() {
  local app="${1:?deploy requires <app>}"
  ensure_doctl_auth
  local app_id; app_id="$(resolve_app "$app")"
  [[ -n "$app_id" ]] || die "app '$app' not found on this DO team"
  local spec; spec="$(spec_for "$app")"

  local tag; tag="$(build_and_push "$app")"
  log "rewriting $spec → image.tag = $tag"
  rewrite_tag "$spec" "$tag"

  log "submitting spec to App Platform (this blocks until ACTIVE)"
  if doctl apps update "$app_id" --spec "$spec" --wait; then
    ok "deploy succeeded ($app @ $tag)"
  else
    die "deploy failed — see \`doctl apps logs $app_id --type deploy\`"
  fi
}

cmd_redeploy() {
  local app="${1:?redeploy requires <app>}"
  ensure_doctl_auth
  local app_id; app_id="$(resolve_app "$app")"
  log "creating empty deployment for $app ($app_id)"
  if doctl apps create-deployment "$app_id" --wait; then
    ok "redeploy succeeded"
  else
    exit 2
  fi
}

cmd_logs() {
  local app="${1:?logs requires <app>}"
  local phase="${2:-run}"
  ensure_doctl_auth
  local app_id; app_id="$(resolve_app "$app")"
  doctl apps logs "$app_id" --type "$phase" --follow
}

cmd_status() {
  local app="${1:?status requires <app>}"
  ensure_doctl_auth
  local app_id; app_id="$(resolve_app "$app")"
  log "app=$app id=$app_id"
  doctl apps get "$app_id" --format ID,Spec.Name,DefaultIngress,ActiveDeployment.Phase,UpdatedAt --no-header
  cmd_verify "$app" || warn "health probe failed (see above)"
}

cmd_verify() {
  local app="${1:?verify requires <app>}"
  local url
  case "$app" in
    syrabit-backend) url="$HEALTH_BACKEND" ;;
    rust-core)       url="$HEALTH_RUST"    ;;
    *)               die "unknown app '$app'" ;;
  esac
  log "GET $url"
  local code
  code="$(curl -sS -o /tmp/do-health.$$ -w '%{http_code}' --max-time 10 "$url" || echo 000)"
  if [[ "$code" == 2* ]]; then
    ok "health: HTTP $code"
    head -c 256 /tmp/do-health.$$ >&2; echo >&2
    rm -f /tmp/do-health.$$
    return 0
  fi
  fail "health probe returned HTTP $code"
  head -c 512 /tmp/do-health.$$ >&2 || true; echo >&2
  rm -f /tmp/do-health.$$
  return 2
}

cmd_grpc_check() {
  ensure_doctl_auth
  require grpcurl
  local app_id; app_id="$(resolve_app "rust-core")"
  log "grpc.health.v1.Health/Check on rust-core internal :50051"
  doctl apps console "$app_id" --component core <<'EOF' || exit 2
grpcurl -plaintext localhost:50051 grpc.health.v1.Health/Check
EOF
}

cmd_vars() {
  local app="${1:?vars requires <app>}"
  ensure_doctl_auth
  local app_id; app_id="$(resolve_app "$app")"
  doctl apps spec get "$app_id" \
    | awk '/^[[:space:]]*envs:/,/^[^[:space:]]/'
}

cmd_var_set() {
  local app="${1:?var-set requires <app>}"; shift
  [[ $# -gt 0 ]] || die "var-set requires KEY=VAL pairs"
  ensure_doctl_auth
  local app_id; app_id="$(resolve_app "$app")"
  local tmpspec; tmpspec="$(mktemp)"
  doctl apps spec get "$app_id" > "$tmpspec"
  for kv in "$@"; do
    local key="${kv%%=*}"; local val="${kv#*=}"
    [[ "$key" != "$kv" ]] || die "expected KEY=VAL, got '$kv'"
    if command -v yq >/dev/null 2>&1; then
      yq -i "(.services[0].envs[] | select(.key == \"$key\") | .value) = \"$val\"" "$tmpspec" \
      || yq -i ".services[0].envs += [{\"key\":\"$key\",\"value\":\"$val\",\"scope\":\"RUN_TIME\",\"type\":\"SECRET\"}]" "$tmpspec"
    else
      die "yq is required for var-set without dashboard access"
    fi
  done
  doctl apps update "$app_id" --spec "$tmpspec" --wait
  rm -f "$tmpspec"
  ok "vars updated"
}

cmd_var_unset() {
  local app="${1:?var-unset requires <app>}"; shift
  [[ $# -gt 0 ]] || die "var-unset requires at least one KEY"
  ensure_doctl_auth
  require yq
  local app_id; app_id="$(resolve_app "$app")"
  local tmpspec; tmpspec="$(mktemp)"
  doctl apps spec get "$app_id" > "$tmpspec"
  for key in "$@"; do
    yq -i "del(.services[0].envs[] | select(.key == \"$key\"))" "$tmpspec"
  done
  doctl apps update "$app_id" --spec "$tmpspec" --wait
  rm -f "$tmpspec"
  ok "vars removed"
}

cmd_import_env() {
  local app="${1:?import-env requires <app>}"
  local file="${2:?import-env requires path to KEY=VAL file}"
  [[ -f "$file" ]] || die "env file not found: $file"
  ensure_doctl_auth
  require yq
  local app_id; app_id="$(resolve_app "$app")"
  local spec; spec="$(spec_for "$app")"
  local tmpspec; tmpspec="$(mktemp)"
  cp "$spec" "$tmpspec"

  while IFS= read -r line; do
    # strip comments / blank lines
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    local key="${line%%=*}"; local val="${line#*=}"
    # strip surrounding quotes if present
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    yq -i "(.services[0].envs[] | select(.key == \"$key\") | .value) = \"$val\"" "$tmpspec" || true
  done < "$file"

  if grep -q 'PLACEHOLDER_SET_VIA_doctl' "$tmpspec"; then
    warn "some PLACEHOLDER_SET_VIA_doctl entries still present in patched spec"
    grep -n PLACEHOLDER_SET_VIA_doctl "$tmpspec" >&2
  fi

  doctl apps update "$app_id" --spec "$tmpspec" --wait
  rm -f "$tmpspec"
  ok "env imported from $file"
}

usage() {
  cat >&2 <<EOF
$SCRIPT_NAME — Digital Ocean App Platform helper for syrabit-backend + rust-core.

Usage:
  $SCRIPT_NAME deploy     <app>
  $SCRIPT_NAME redeploy   <app>
  $SCRIPT_NAME logs       <app> [run|build|deploy]
  $SCRIPT_NAME status     <app>
  $SCRIPT_NAME verify     <app>
  $SCRIPT_NAME grpc-check
  $SCRIPT_NAME vars       <app>
  $SCRIPT_NAME var-set    <app> KEY=VAL [KEY=VAL ...]
  $SCRIPT_NAME var-unset  <app> KEY     [KEY ...]
  $SCRIPT_NAME import-env <app> file

  <app> ∈ {syrabit-backend, rust-core}

Required env: DIGITALOCEAN_ACCESS_TOKEN
EOF
  exit 1
}

main() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    deploy)     cmd_deploy "$@" ;;
    redeploy)   cmd_redeploy "$@" ;;
    logs)       cmd_logs "$@" ;;
    status)     cmd_status "$@" ;;
    verify)     cmd_verify "$@" ;;
    grpc-check) cmd_grpc_check "$@" ;;
    vars)       cmd_vars "$@" ;;
    var-set)    cmd_var_set "$@" ;;
    var-unset)  cmd_var_unset "$@" ;;
    import-env) cmd_import_env "$@" ;;
    -h|--help|help|"") usage ;;
    *) fail "unknown subcommand '$cmd'"; usage ;;
  esac
}

main "$@"
