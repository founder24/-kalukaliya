#!/usr/bin/env python3
"""
Task #89 — single source of truth for the production env-var contract.

Scans backend Python, Lambda Python, worker TypeScript, ACA bicep,
Lambda Terraform, and wrangler.toml files and (a) generates
`docs/infra/env-vars.md` (`--write` mode) or (b) checks that the
on-disk doc is in sync with what the code currently references
(default mode, run in CI).

Doc rebuild:
    python scripts/ci/check_env_vars_doc.py --write

CI check (fails on drift):
    python scripts/ci/check_env_vars_doc.py
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parents[2]
DOC_PATH = REPO / "docs" / "infra" / "env-vars.md"

# ---------------------------------------------------------------------------
# Source roots scanned per "service" bucket. A service is a deploy unit:
# different services have different env-var contracts (ACA backend env vs.
# wrangler [vars] bindings vs. Lambda environment{} blocks).
# ---------------------------------------------------------------------------

PY_REF_RE = re.compile(
    r"""os\.environ(?:\.get\(|\[)\s*["']([A-Z][A-Z0-9_]+)["']"""
    r"""|os\.getenv\(\s*["']([A-Z][A-Z0-9_]+)["']"""
)
TS_REF_RE = re.compile(r"""\benv\.([A-Z][A-Z0-9_]+)\b""")
# Catches TypeScript Env interface property declarations like
# `JWT_SECRET?: string;` inside `interface Env { ... }` blocks. Casted
# access patterns (`(env as Env & { JWT_SECRET?: string }).JWT_SECRET`)
# in workers/edge-proxy/src/index.ts hide the env var name behind a
# property cast that the bare TS_REF_RE above misses.
TS_TYPE_DECL_RE = re.compile(
    r"""^\s*([A-Z][A-Z0-9_]+)\??:\s*(?:string|number|boolean)""", re.M
)
WRANGLER_VAR_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*=", re.M)
BICEP_NAME_RE = re.compile(r"name:\s*'([A-Z][A-Z0-9_]+)'")
BICEP_SECRETREF_RE = re.compile(
    r"\{\s*name:\s*'([A-Z][A-Z0-9_]+)'\s*,\s*secretRef:\s*'([a-z0-9-]+)'"
)
BICEP_VALUE_RE = re.compile(
    r"\{\s*name:\s*'([A-Z][A-Z0-9_]+)'\s*,\s*value:\s*'([^']*)'"
)
TF_ENV_BLOCK_RE = re.compile(r"environment\s*\{\s*variables\s*=\s*merge\([^,]+,\s*\{(.*?)\}\s*\)\s*\}", re.S)
TF_ENV_KV_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*=\s*(.+?)$", re.M)

# Names that are NOT environment variables (worker bindings to Durable
# Objects, KV namespaces, R2 buckets, D1 databases, AI binding, etc.).
# These show up in `env.X` references but are not configured via env;
# they're declared as bindings in wrangler.toml.
WORKER_BINDING_NAMES = {
    # Workers AI / Analytics Engine / static assets bindings.
    "AI", "ANALYTICS", "ASSETS",
    # KV namespaces (declared under [[kv_namespaces]] in wrangler.toml).
    "AI_RESPONSE_CACHE_KV_ID", "AI_RESPONSE_CACHE_KV_ID_NE_INDIA",
    "BOT_HTML_CACHE", "CF_EDGE_CACHE", "CONTENT_CACHE", "CONTENT_DB",
    "RATE_LIMIT", "SYLLABUS_INDEX",
    # Durable Objects (declared under [[durable_objects.bindings]]).
    "CHAT_SESSION", "RATE_LIMITER", "RATE_LIMITER_DO", "SEASON_CACHE_DO",
    # R2 buckets.
    "R2_MEDIA",
    # Special: mTLS cert binding. (NOTE: PAGES_ORIGIN was REMOVED — it is
    # a real plaintext [vars] env var in workers/edge-proxy/wrangler.toml,
    # not a binding.)
    "MTLS_CERT",
}

# Names that look like env vars in regex matches but are local helper
# variables / loop vars / dict keys inside the codebase. Add only after
# manual inspection confirms they aren't real env vars.
PY_NOISE_NAMES: set[str] = {
    # AWS Lambda runtime hands these out — not operator-controlled.
    "AWS_LAMBDA_FUNCTION_NAME", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
}


@dataclass
class Service:
    key: str          # short slug for anchors
    title: str        # human-readable section header
    blurb: str        # one-paragraph description
    code_dirs: list[Path] = field(default_factory=list)
    code_lang: str = "py"   # "py" | "ts"
    deploy_files: list[Path] = field(default_factory=list)


SERVICES: list[Service] = [
    Service(
        key="aca-backend",
        title="ACA backend (`syrabit-backend`)",
        blurb=(
            "FastAPI runtime in `artifacts/syrabit-backend/`, deployed to "
            "Azure Container Apps via `infra/azure/aca-syrabit-backend.bicep`. "
            "Env section in the bicep file is the canonical wiring."
        ),
        code_dirs=[
            REPO / "artifacts/syrabit-backend",
        ],
        code_lang="py",
        deploy_files=[REPO / "infra/azure/aca-syrabit-backend.bicep"],
    ),
    Service(
        key="aca-jobs",
        title="ACA / Lambda batch jobs",
        blurb=(
            "Background jobs that run inside the ACA backend container "
            "(`aca_jobs/*.py`) AND, increasingly, on AWS Lambda "
            "(`artifacts/syrabit/services/backend/lambda_batch/*.py`). "
            "Lambda wiring lives in `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`."
        ),
        code_dirs=[
            REPO / "artifacts/syrabit-backend/aca_jobs",
            REPO / "artifacts/syrabit/services/backend/lambda_batch",
        ],
        code_lang="py",
        deploy_files=[REPO / "artifacts/syrabit/infra/aws/lambda-batch-jobs.tf"],
    ),
    Service(
        key="edge-proxy",
        title="Cloudflare Worker — `syrabit-edge` (edge proxy)",
        blurb=(
            "Routes `api.syrabit.ai/*` and friends. Bindings + plaintext "
            "vars in `workers/edge-proxy/wrangler.toml`; secrets via "
            "`wrangler secret put` (not in this repo)."
        ),
        code_dirs=[REPO / "workers/edge-proxy/src"],
        code_lang="ts",
        deploy_files=[REPO / "workers/edge-proxy/wrangler.toml"],
    ),
    Service(
        key="embed-worker",
        title="Cloudflare Worker — `syrabit-embed-worker`",
        blurb=(
            "Custom Workers-AI embedding endpoint at `embed.syrabit.ai`. "
            "Bindings in `artifacts/syrabit/workers/embed-worker/wrangler.toml`."
        ),
        code_dirs=[REPO / "artifacts/syrabit/workers/embed-worker/src"],
        code_lang="ts",
        deploy_files=[REPO / "artifacts/syrabit/workers/embed-worker/wrangler.toml"],
    ),
    Service(
        key="email-worker",
        title="Cloudflare Worker — `syrabit-email` (410 stub)",
        blurb=(
            "Task #556 retired transport — only `/email/health` is live; "
            "every other route returns HTTP 410. Kept on the deploy "
            "manifest so stale callers fail loud."
        ),
        code_dirs=[REPO / "workers/email-worker/src"],
        code_lang="ts",
        deploy_files=[REPO / "workers/email-worker/wrangler.toml"],
    ),
]


# ---------------------------------------------------------------------------
# Heuristic: classify whether a var is a secret based on name + bicep
# secretRef wiring. Secrets must never be committed to the repo.
# ---------------------------------------------------------------------------
SECRET_NAME_HINTS = (
    # Direct credential suffixes.
    "_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_DSN", "_JWT",
    # Connection-string style names that carry credentials inline.
    "MONGO_URL", "DATABASE_URL", "API_KEY", "CREDENTIALS",
    "PRIVATE_KEY",
    # NOTE: `_ARN` was removed — an AWS Secrets Manager ARN is itself a
    # public reference, not the secret value. The Lambda runtime
    # dereferences it at cold-start; the ARN can safely be checked into
    # Terraform state.
)

NON_SECRET_OVERRIDES = {
    # *_REGION names look secret-ish but are non-sensitive AWS config.
    "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_GLACIER_REGION",
    "AWS_NATIVE_PRIMARY_REGION", "AWS_NATIVE_SECONDARY_REGION",
    "AWS_SES_REGION", "BEDROCK_EMBED_REGION", "SES_REGION",
    "VERTEX_LOCATION",
    # *_URL names that point to public endpoints.
    "WORKERS_EMBED_URL", "BACKEND_URL", "EDGE_WORKER_URL",
    "EDGE_WORKER_PREVIEW_URL", "PUBLIC_BASE_URL", "POSTHOG_HOST",
    "CF_EDGE_KV_CACHE_URL", "CF_EDGE_PROXY_URL", "CF_API_DOMAIN",
    "CF_AI_GATEWAY_URL", "AZURE_FORM_RECOGNIZER_ENDPOINT",
    "BACKEND_WEBHOOK_URL", "PAGES_ORIGIN",
    # ID-style identifiers that are public (account IDs, project IDs).
    "CF_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID", "CF_AI_GATEWAY_ID",
    "CF_AI_GATEWAY_ACCOUNT_ID", "AZURE_SUBSCRIPTION_ID",
    "AZURE_TENANT_ID", "VERTEX_PROJECT_ID", "GCP_BILLING_PROJECT",
    "ADSENSE_ACCOUNT_ID", "ADSENSE_CLIENT_ID",
    "AWS_FRAUD_DETECTOR_ID", "AWS_FRAUD_DETECTOR_PAYMENT_ID",
    "AWS_FRAUD_DETECTOR_VERSION", "AWS_FRAUD_DETECTOR_PAYMENT_VERSION",
    "GOOGLE_OAUTH_CLIENT_ID",
}


def is_secret(name: str, bicep_secrets: set[str]) -> bool:
    if name in bicep_secrets:
        return True
    if name in NON_SECRET_OVERRIDES:
        return False
    return any(name.endswith(h) or h in name for h in SECRET_NAME_HINTS)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def scan_python_dir(root: Path) -> set[str]:
    out: set[str] = set()
    if not root.exists():
        return out
    for path in root.rglob("*.py"):
        # Skip caches, tests (test-only env vars are not part of the
        # production contract), and one-off migration scripts.
        rel = path.relative_to(REPO).as_posix()
        if "__pycache__" in rel:
            continue
        if "/tests/" in rel or rel.endswith("_test.py") or "/test_" in rel:
            continue
        if "/scripts/" in rel and "ci/" not in rel:
            # one-off operator scripts — not part of the live contract
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in PY_REF_RE.finditer(text):
            name = m.group(1) or m.group(2)
            if name and name not in PY_NOISE_NAMES:
                out.add(name)
    return out


def scan_ts_dir(root: Path) -> set[str]:
    out: set[str] = set()
    if not root.exists():
        return out
    for path in list(root.rglob("*.ts")) + list(root.rglob("*.mjs")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in TS_REF_RE.finditer(text):
            name = m.group(1)
            if name and name not in WORKER_BINDING_NAMES:
                out.add(name)
        # Catch type-declaration-only env refs (cast access patterns).
        for m in TS_TYPE_DECL_RE.finditer(text):
            name = m.group(1)
            if name and name not in WORKER_BINDING_NAMES:
                out.add(name)
    return out


def parse_bicep(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return (secret_refs, plain_values) maps for env: [...] entries."""
    if not path.exists():
        return {}, {}
    text = path.read_text(encoding="utf-8")
    secrets: dict[str, str] = {}
    plains: dict[str, str] = {}
    for m in BICEP_SECRETREF_RE.finditer(text):
        secrets[m.group(1)] = m.group(2)
    for m in BICEP_VALUE_RE.finditer(text):
        plains[m.group(1)] = m.group(2)
    return secrets, plains


def parse_lambda_tf(path: Path) -> dict[str, str]:
    """Return env_name -> source_expression for the lambda-batch-jobs.tf
    `environment { variables = merge(local.otel_env, { ... }) }` block."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    block = TF_ENV_BLOCK_RE.search(text)
    if not block:
        return out
    for line in block.group(1).splitlines():
        m = re.match(r"\s*([A-Z][A-Z0-9_]+)\s*=\s*(.+?)\s*$", line)
        if m:
            value = m.group(2).rstrip(",").strip()
            if value.startswith("#"):
                continue
            out[m.group(1)] = value
    return out


def parse_wrangler_vars(path: Path) -> set[str]:
    """Return the set of plaintext var names declared in wrangler.toml."""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    out: set[str] = set()
    in_vars = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_vars = stripped.endswith(".vars]") or stripped == "[vars]"
            continue
        if not in_vars:
            continue
        m = re.match(r"\s*([A-Z][A-Z0-9_]+)\s*=", line)
        if m:
            out.add(m.group(1))
    return out


# ---------------------------------------------------------------------------
# Doc generation
# ---------------------------------------------------------------------------

def render_table(rows: list[tuple[str, str, str, str]]) -> str:
    """Render a 4-col markdown table: name | type | wired? | notes."""
    lines = [
        "| env var | type | wired in deploy infra? | notes |",
        "|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: x[0]):
        lines.append(f"| `{r[0]}` | {r[1]} | {r[2]} | {r[3]} |")
    return "\n".join(lines)


def build_doc() -> str:
    bicep_secrets, bicep_plains = parse_bicep(REPO / "infra/azure/aca-syrabit-backend.bicep")
    bicep_set = set(bicep_secrets) | set(bicep_plains)

    tf_envs = parse_lambda_tf(REPO / "artifacts/syrabit/infra/aws/lambda-batch-jobs.tf")
    tf_secret_arns = {k for k in tf_envs if k.endswith("_SECRET_ARN") or k.endswith("_SECRET")}

    edge_vars = parse_wrangler_vars(REPO / "workers/edge-proxy/wrangler.toml")
    embed_vars = parse_wrangler_vars(REPO / "artifacts/syrabit/workers/embed-worker/wrangler.toml")
    email_vars = parse_wrangler_vars(REPO / "workers/email-worker/wrangler.toml")

    # Build per-service tables.
    sections: list[str] = []
    for svc in SERVICES:
        if svc.code_lang == "py":
            code_refs = set()
            for d in svc.code_dirs:
                code_refs |= scan_python_dir(d)
        else:
            code_refs = set()
            for d in svc.code_dirs:
                code_refs |= scan_ts_dir(d)

        rows: list[tuple[str, str, str, str]] = []

        if svc.key == "aca-backend":
            # Union of "everything code references" and "everything bicep
            # binds" — code-only refs surface as "❌ not wired" so the
            # operator can decide between (a) wire it in bicep, (b) prune
            # the dead code path, or (c) confirm it comes from a different
            # infra file (account-billing.tf / Key Vault / etc.).
            interesting = code_refs | bicep_set
            for name in interesting:
                wired = "✅ secretRef `%s`" % bicep_secrets[name] if name in bicep_secrets \
                        else ("✅ literal value" if name in bicep_plains else "❌ code-only")
                typ = "🔒 secret" if is_secret(name, set(bicep_secrets)) else "⚙️ config"
                note = ""
                if name in code_refs and name in bicep_set:
                    note = "code-referenced + wired"
                elif name in bicep_set:
                    note = "wired but no code reference found (deploy-time only)"
                else:
                    note = "code-referenced only"
                rows.append((name, typ, wired, note))
        elif svc.key == "aca-jobs":
            interesting = code_refs | set(tf_envs)
            for name in interesting:
                if name in tf_envs:
                    wired = "✅ Lambda env (TF)"
                else:
                    wired = "❌ in-process / ACA-only"
                typ = "🔒 secret" if is_secret(name, tf_secret_arns) else "⚙️ config"
                note = "Lambda + ACA" if name in tf_envs and name in code_refs else \
                       "TF-wired only" if name in tf_envs else "code-only"
                rows.append((name, typ, wired, note))
        elif svc.key == "edge-proxy":
            interesting = code_refs | edge_vars
            for name in interesting:
                wired = "✅ wrangler [vars]" if name in edge_vars else "❌ wrangler secret (operator-set)"
                typ = "🔒 secret" if is_secret(name, set()) else "⚙️ config"
                rows.append((name, typ, wired, ""))
        elif svc.key == "embed-worker":
            interesting = code_refs | embed_vars
            for name in interesting:
                wired = "✅ wrangler [vars]" if name in embed_vars else "❌ wrangler secret (operator-set)"
                typ = "🔒 secret" if is_secret(name, set()) else "⚙️ config"
                rows.append((name, typ, wired, ""))
        elif svc.key == "email-worker":
            interesting = code_refs | email_vars
            for name in interesting:
                wired = "✅ wrangler [vars]" if name in email_vars else "❌ wrangler secret (operator-set)"
                typ = "🔒 secret" if is_secret(name, set()) else "⚙️ config"
                rows.append((name, typ, wired, ""))

        deploy_paths = ", ".join(f"`{p.relative_to(REPO).as_posix()}`" for p in svc.deploy_files)
        sections.append(
            f"## {svc.title}\n\n{svc.blurb}\n\n"
            f"**Deploy file(s):** {deploy_paths}\n\n"
            f"{render_table(rows)}\n"
        )

    header = (
        "# Production environment-variable contract\n\n"
        "**GENERATED — do not hand-edit.** Regenerate with\n"
        "`python scripts/ci/check_env_vars_doc.py --write`. CI runs the same\n"
        "script in check mode and fails if this file drifts from the code.\n\n"
        "## Why this file exists\n\n"
        "Task #87's code-review found that the `replit.md` env list is only\n"
        "the narrow CI-enforced subset and several genuinely-required\n"
        "secrets (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY`,\n"
        "`ORIGIN_SHARED_SECRET`, `D1_SYNC_SECRET`) live only inside\n"
        "`infra/azure/aca-syrabit-backend.bicep` + worker bindings. New\n"
        "on-call / new-environment bring-up had no single source of truth.\n"
        "This doc fills that gap by extracting env references from every\n"
        "deploy unit and cross-referencing them against the bicep / TF /\n"
        "wrangler wiring that actually exists in the repo.\n\n"
        "## Conventions\n\n"
        "| symbol | meaning |\n"
        "|---|---|\n"
        "| 🔒 secret | sensitive — must come from a secret store (Key Vault / Secrets Manager / wrangler secret) |\n"
        "| ⚙️ config | non-sensitive (URLs, region names, feature flags) — safe to commit |\n"
        "| ✅ wired | declared in the deploy infra file (bicep env, TF env block, wrangler.toml `[vars]`) |\n"
        "| ❌ not wired | code references the var but no deploy infra binds it — operator must set it manually OR the code path is dead |\n\n"
        "## Sources scanned\n\n"
        "- ACA backend: `artifacts/syrabit-backend/**/*.py` (excluding `tests/`, `scripts/`, `__pycache__`)\n"
        "- Background jobs: `artifacts/syrabit-backend/aca_jobs/`, `artifacts/syrabit/services/backend/lambda_batch/`\n"
        "- Workers: `workers/edge-proxy/src/`, `artifacts/syrabit/workers/embed-worker/src/`, `workers/email-worker/src/`\n"
        "- Deploy infra: `infra/azure/aca-syrabit-backend.bicep`, `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`, the three wrangler.toml files\n\n"
        "## Limitations\n\n"
        "- The script does AST-free regex extraction; vars built from\n"
        "  `f\"PREFIX_{var}\"` strings are NOT captured.\n"
        "- One-off operator scripts under `artifacts/syrabit-backend/scripts/`\n"
        "  are intentionally excluded — they don't run in production.\n"
        "- `secret?` classification is heuristic (name-suffix + bicep\n"
        "  `secretRef` wiring); see `NON_SECRET_OVERRIDES` in the script for\n"
        "  the explicit non-secret allowlist.\n"
        "- A `❌ not wired` row in the ACA-backend table can mean either (a)\n"
        "  the operator is expected to inject it via the ACA env block by\n"
        "  hand, (b) the code path is dead, or (c) the value is sourced\n"
        "  from another infra file (`infra/aws/account-billing.tf`,\n"
        "  upstream Key Vault) — review case-by-case.\n\n"
    )

    return header + "\n".join(sections)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true",
        help="Regenerate %s from current code." % DOC_PATH.relative_to(REPO).as_posix(),
    )
    args = parser.parse_args()

    new_content = build_doc()

    if args.write:
        DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOC_PATH.write_text(new_content, encoding="utf-8")
        print(f"WROTE {DOC_PATH.relative_to(REPO)} ({len(new_content)} bytes)")
        return 0

    if not DOC_PATH.exists():
        print(f"FAIL: {DOC_PATH.relative_to(REPO)} does not exist. "
              f"Run with --write to generate it.", file=sys.stderr)
        return 1

    on_disk = DOC_PATH.read_text(encoding="utf-8")
    if on_disk == new_content:
        print(f"OK {DOC_PATH.relative_to(REPO)} matches code "
              f"({len(new_content)} bytes).")
        return 0

    # Show a small diff hint.
    import difflib
    diff = difflib.unified_diff(
        on_disk.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{DOC_PATH.relative_to(REPO)}",
        tofile=f"b/{DOC_PATH.relative_to(REPO)}",
        n=3,
    )
    sys.stderr.writelines(list(diff)[:200])
    sys.stderr.write(
        f"\nFAIL: {DOC_PATH.relative_to(REPO)} is out of sync with code.\n"
        f"Run: python scripts/ci/check_env_vars_doc.py --write\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
