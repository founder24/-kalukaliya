#!/usr/bin/env python3
"""
Verify that the seven legacy secrets removed in Task #347 are absent
from every cloud surface they ever lived in. Operator runbook tool
for Task #364 §C.2.

Surfaces checked:
  - Cloudflare Worker secrets (per worker, via the CF API)
  - Azure Key Vault (`az keyvault secret list`, includes soft-deleted
    rows so a recently-deleted secret in the 90-day window also
    counts as "purged" — operator gets a NOTE row, not a FAIL)
  - GitHub Actions (`gh secret list` for repo + each named env)

Exit codes:
  0  every surface confirms every secret absent (or soft-deleted in KV)
  1  one or more (surface, secret) pairs still present — the script
     prints them as `FAIL: <surface> :: <secret>`
  2  harness failure (network, missing CLI, bad credentials)
  3  usage error

The script is read-only. It never deletes, sets, or modifies any
secret. CF API calls only use the `workers_scripts:read` permission
on the supplied token.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

CF_LEGACY_SECRETS = [
    "OPENAI_API_KEY",
    "XAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "BEDROCK_PROXY_AUTH_TOKEN",
    "RESEND_API_KEY",
]
AZURE_LEGACY_SECRETS = [
    "OPENAI-API-KEY",
    "XAI-API-KEY",
    "ANTHROPIC-API-KEY",
    "RESEND-API-KEY",
    "STRIPE-SECRET-KEY",
    "STRIPE-WEBHOOK-SECRET",
]
GH_LEGACY_SECRETS = [
    "OPENAI_API_KEY",
    "XAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "BEDROCK_PROXY_AUTH_TOKEN",
    "RESEND_API_KEY",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
]


def _cf_secret_names(account_id: str, worker: str, token: str) -> list[str]:
    url = (f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
           f"/workers/scripts/{worker}/secrets")
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"  NOTE: CF Worker `{worker}` returned 404 — "
                  f"already deleted; nothing to check")
            return []
        raise
    if not data.get("success", False):
        raise RuntimeError(
            f"CF API for `{worker}` returned success=false: "
            f"{data.get('errors', [])}")
    return [row["name"] for row in (data.get("result") or [])]


def _az_kv_secret_names(vault: str, include_deleted: bool = True) -> tuple[
        set[str], set[str]]:
    if shutil.which("az") is None:
        raise RuntimeError("`az` CLI not on PATH")
    out = subprocess.run(
        ["az", "keyvault", "secret", "list",
         "--vault-name", vault, "-o", "json"],
        capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise RuntimeError(f"az keyvault secret list failed: {out.stderr}")
    active = {row["name"] for row in json.loads(out.stdout) if "name" in row}
    deleted: set[str] = set()
    if include_deleted:
        out2 = subprocess.run(
            ["az", "keyvault", "secret", "list-deleted",
             "--vault-name", vault, "-o", "json"],
            capture_output=True, text=True, check=False)
        if out2.returncode == 0:
            deleted = {row["name"] for row in json.loads(out2.stdout)
                       if "name" in row}
    return active, deleted


def _gh_secret_names(repo: str, env: str | None) -> set[str]:
    if shutil.which("gh") is None:
        raise RuntimeError("`gh` CLI not on PATH")
    cmd = ["gh", "secret", "list", "--repo", repo, "--json", "name"]
    if env:
        cmd += ["--env", env]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise RuntimeError(
            f"gh secret list (env={env or 'repo'}) failed: {out.stderr}")
    try:
        return {row["name"] for row in json.loads(out.stdout)}
    except (json.JSONDecodeError, TypeError) as e:
        raise RuntimeError(f"gh secret list output unparseable: {e}") from e


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cf-account-id",
                   default=os.environ.get("CF_ACCOUNT_ID", ""),
                   help="Cloudflare account ID. Falls back to "
                        "$CF_ACCOUNT_ID. Skip CF entirely with --skip-cf.")
    p.add_argument("--cf-workers", default="",
                   help="Comma-separated list of CF Worker names to "
                        "check. Required unless --skip-cf is set.")
    p.add_argument("--cf-token-env", default="CF_API_TOKEN",
                   help="Env var holding the CF API token. Default "
                        "CF_API_TOKEN. Token only needs "
                        "`workers_scripts:read`.")
    p.add_argument("--skip-cf", action="store_true")
    p.add_argument("--azure-vault", default="",
                   help="Azure Key Vault name. Skip with --skip-azure.")
    p.add_argument("--skip-azure", action="store_true")
    p.add_argument("--gh-repo", default="",
                   help="GitHub repo (owner/name). Skip with --skip-gh.")
    p.add_argument("--gh-envs", default="",
                   help="Comma-separated list of GH environments to "
                        "check in addition to the repo level.")
    p.add_argument("--skip-gh", action="store_true")
    args = p.parse_args()

    failures: list[tuple[str, str]] = []
    notes: list[str] = []

    if not args.skip_cf:
        if not args.cf_account_id or not args.cf_workers:
            print("ERROR: --cf-account-id and --cf-workers are required "
                  "unless --skip-cf is set", file=sys.stderr)
            return 3
        token = os.environ.get(args.cf_token_env, "").strip()
        if not token:
            print(f"ERROR: env var {args.cf_token_env} unset/empty",
                  file=sys.stderr)
            return 2
        for worker in [w.strip() for w in args.cf_workers.split(",")
                       if w.strip()]:
            try:
                names = set(_cf_secret_names(
                    args.cf_account_id, worker, token))
            except (urllib.error.URLError, urllib.error.HTTPError,
                    RuntimeError, TimeoutError) as e:
                print(f"ERROR: CF probe `{worker}` failed: {e}",
                      file=sys.stderr)
                return 2
            for s in CF_LEGACY_SECRETS:
                if s in names:
                    failures.append((f"cloudflare:{worker}", s))

    if not args.skip_azure:
        if not args.azure_vault:
            print("ERROR: --azure-vault is required unless --skip-azure",
                  file=sys.stderr)
            return 3
        try:
            active, deleted = _az_kv_secret_names(args.azure_vault)
        except RuntimeError as e:
            print(f"ERROR: Azure KV probe failed: {e}", file=sys.stderr)
            return 2
        for s in AZURE_LEGACY_SECRETS:
            if s in active:
                failures.append((f"azure-kv:{args.azure_vault}", s))
            elif s in deleted:
                notes.append(
                    f"  NOTE: azure-kv:{args.azure_vault} :: {s} is "
                    f"soft-deleted (within 90-day recovery window) — "
                    f"counts as purged")

    if not args.skip_gh:
        if not args.gh_repo:
            print("ERROR: --gh-repo is required unless --skip-gh",
                  file=sys.stderr)
            return 3
        try:
            repo_names = _gh_secret_names(args.gh_repo, env=None)
        except RuntimeError as e:
            print(f"ERROR: GH probe (repo) failed: {e}", file=sys.stderr)
            return 2
        for s in GH_LEGACY_SECRETS:
            if s in repo_names:
                failures.append((f"github:{args.gh_repo}", s))
        for env in [e.strip() for e in args.gh_envs.split(",") if e.strip()]:
            try:
                env_names = _gh_secret_names(args.gh_repo, env=env)
            except RuntimeError as e:
                print(f"ERROR: GH probe (env={env}) failed: {e}",
                      file=sys.stderr)
                return 2
            for s in GH_LEGACY_SECRETS:
                if s in env_names:
                    failures.append((f"github:{args.gh_repo}@{env}", s))

    for note in notes:
        print(note)

    if not failures:
        print("OK: every (surface, secret) pair confirmed absent")
        return 0
    for surface, secret in failures:
        print(f"FAIL: {surface} :: {secret} still present")
    print(f"\n{len(failures)} (surface, secret) pair(s) still present")
    return 1


if __name__ == "__main__":
    sys.exit(main())
