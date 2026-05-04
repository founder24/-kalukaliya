#!/usr/bin/env bash
# scripts/do.sh — Digital Ocean App Platform helper (Task #331).
#
# Thin wrapper around `doctl apps {update,logs}` so day-to-day deploy
# and debug calls are short and consistent across machines.
#
# Subcommands:
#   deploy     <app>           Push the committed spec for <app>.
#   logs       <app> [type]    Tail logs (type: run|build|deploy, default run).
#   status     <app>           Print the current deployment phase + live URL.
#   import-env <app> <file>    Upsert encrypted env vars from a KEY=VALUE file.
#   verify     <app>           Hit /api/health (backend) or /health (rust-core).
#   grpc-check                 grpcurl health.Check against the rust-core gRPC port.
#
# <app> is one of: syrabit-backend | rust-core
#
# Required env:
#   DO_APP_ID_SYRABIT_BACKEND  — repo variable mirrored locally for convenience
#   DO_APP_ID_RUST_CORE        — repo variable mirrored locally for convenience
#
# `doctl auth init` must already have been run (see infra/do/README.md).

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

die() { echo "do.sh: $*" >&2; exit 1; }

require() { command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"; }

app_id_for() {
  case "$1" in
    syrabit-backend) echo "${DO_APP_ID_SYRABIT_BACKEND:-}" ;;
    rust-core)       echo "${DO_APP_ID_RUST_CORE:-}" ;;
    *) die "unknown app: $1 (expected syrabit-backend|rust-core)" ;;
  esac
}

spec_for() {
  case "$1" in
    syrabit-backend) echo "${REPO_ROOT}/infra/do/app-syrabit-backend.yaml" ;;
    rust-core)       echo "${REPO_ROOT}/infra/do/app-rust-core.yaml" ;;
    *) die "unknown app: $1" ;;
  esac
}

health_path_for() {
  case "$1" in
    syrabit-backend) echo "/api/health" ;;
    rust-core)       echo "/health" ;;
  esac
}

cmd_deploy() {
  local app="${1:-}"; [[ -n "$app" ]] || die "usage: do.sh deploy <app>"
  local id; id="$(app_id_for "$app")"; [[ -n "$id" ]] || die "no app id set for $app"
  local spec; spec="$(spec_for "$app")"
  require doctl
  echo "▶ doctl apps update $id --spec $spec --wait"
  doctl apps update "$id" --spec "$spec" --wait
}

cmd_logs() {
  local app="${1:-}"; local kind="${2:-run}"
  [[ -n "$app" ]] || die "usage: do.sh logs <app> [run|build|deploy]"
  local id; id="$(app_id_for "$app")"; [[ -n "$id" ]] || die "no app id set for $app"
  require doctl
  echo "▶ doctl apps logs $id --type $kind --follow"
  doctl apps logs "$id" --type "$kind" --follow
}

cmd_status() {
  local app="${1:-}"; [[ -n "$app" ]] || die "usage: do.sh status <app>"
  local id; id="$(app_id_for "$app")"; [[ -n "$id" ]] || die "no app id set for $app"
  require doctl
  doctl apps get "$id" --format ID,DefaultIngress,ActiveDeployment.Phase,UpdatedAt
}

cmd_import_env() {
  local app="${1:-}"; local file="${2:-}"
  [[ -n "$app" && -n "$file" ]] || die "usage: do.sh import-env <app> <file.env>"
  [[ -f "$file" ]] || die "env file not found: $file"
  local id; id="$(app_id_for "$app")"; [[ -n "$id" ]] || die "no app id set for $app"
  local spec; spec="$(spec_for "$app")"
  require doctl; require python3

  # Build a patched spec with values pulled from <file.env>. We write
  # to a temp file so the committed YAML never touches plaintext.
  local tmp; tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  python3 - "$spec" "$file" "$tmp" <<'PY'
"""
YAML-safe spec patcher.

Reads the committed App Platform spec, finds every env entry whose
`value` is the `PLACEHOLDER_SET_VIA_doctl` sentinel, and rewrites it
with the matching value from <file.env>. Uses PyYAML when available
so JSON blobs / quotes / newlines in secrets (e.g. GCP service
account JSON) are emitted as proper YAML literals; falls back to a
hardened regex path that double-quote-escapes the value if PyYAML
isn't installed on the operator's machine.
"""
import os, re, sys
spec_path, env_path, out_path = sys.argv[1:4]

env = {}
with open(env_path) as f:
    for line in f:
        line = line.rstrip("\n")
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        # Strip surrounding matching quotes from the env-file value.
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        env[k.strip()] = v

PLACEHOLDER = "PLACEHOLDER_SET_VIA_doctl"

try:
    import yaml  # type: ignore
    with open(spec_path) as f:
        spec = yaml.safe_load(f)
    missing = []
    for svc in spec.get("services", []) or []:
        for entry in svc.get("envs", []) or []:
            if entry.get("value") == PLACEHOLDER:
                key = entry.get("key")
                if key in env:
                    entry["value"] = env[key]
                else:
                    missing.append(key)
    if missing:
        sys.stderr.write(
            "do.sh import-env: env file is missing required keys: "
            + ", ".join(sorted(missing)) + "\n"
        )
        sys.exit(2)
    with open(out_path, "w") as f:
        yaml.safe_dump(spec, f, sort_keys=False, default_flow_style=False)
except ImportError:
    # Fallback: regex patch with double-quote escaping. Safe for
    # arbitrary scalars because we always emit a double-quoted YAML
    # string and escape `\` and `"` per the YAML 1.2 spec.
    def yaml_dq(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'

    with open(spec_path) as f:
        spec_text = f.read()
    out, current_key, missing = [], None, []
    for line in spec_text.splitlines():
        m = re.match(r"\s*- key:\s*(\S+)", line)
        if m:
            current_key = m.group(1)
            out.append(line); continue
        if current_key and PLACEHOLDER in line:
            if current_key in env:
                line = re.sub(
                    r'"' + PLACEHOLDER + r'"',
                    yaml_dq(env[current_key]),
                    line,
                )
            else:
                missing.append(current_key)
        out.append(line)
    if missing:
        sys.stderr.write(
            "do.sh import-env: env file is missing required keys: "
            + ", ".join(sorted(set(missing))) + "\n"
        )
        sys.exit(2)
    with open(out_path, "w") as f:
        f.write("\n".join(out) + "\n")
PY

  echo "▶ doctl apps update $id --spec <patched> --wait"
  doctl apps update "$id" --spec "$tmp" --wait
}

cmd_verify() {
  local app="${1:-}"; [[ -n "$app" ]] || die "usage: do.sh verify <app>"
  local id; id="$(app_id_for "$app")"; [[ -n "$id" ]] || die "no app id set for $app"
  require doctl; require curl
  local url; url="$(doctl apps get "$id" --format DefaultIngress --no-header)"
  local path; path="$(health_path_for "$app")"
  echo "▶ curl ${url}${path}"
  curl -fsSL "${url}${path}" && echo
}

cmd_grpc_check() {
  require grpcurl
  local id; id="$(app_id_for rust-core)"; [[ -n "$id" ]] || die "no app id set for rust-core"
  local url; url="$(doctl apps get "$id" --format DefaultIngress --no-header)"
  local host; host="${url#https://}"; host="${host%/*}"
  echo "▶ grpcurl -plaintext ${host}:50051 health.Check"
  grpcurl -plaintext "${host}:50051" health.Check
}

usage() {
  sed -n '2,18p' "$0"
  exit 1
}

main() {
  local sub="${1:-}"; shift || true
  case "$sub" in
    deploy)     cmd_deploy "$@" ;;
    logs)       cmd_logs "$@" ;;
    status)     cmd_status "$@" ;;
    import-env) cmd_import_env "$@" ;;
    verify)     cmd_verify "$@" ;;
    grpc-check) cmd_grpc_check "$@" ;;
    -h|--help|help|"") usage ;;
    *) die "unknown subcommand: $sub" ;;
  esac
}

main "$@"
