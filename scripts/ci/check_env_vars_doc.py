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
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC_PATH = REPO / "docs" / "infra" / "env-vars.md"

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# Captures the full os.environ / os.getenv access pattern so we can
# distinguish required (subscript) from optional (.get/.getenv) and
# pull the literal default expression out when present.
#   group "sub"     -> os.environ["NAME"]
#   group "get"     -> os.environ.get("NAME"[, default])
#   group "getenv"  -> os.getenv("NAME"[, default])
#   group "default" -> default expression (raw, may include quotes / call)
PY_REF_RE = re.compile(
    r"""
    os\.environ\[\s*["'](?P<sub>[A-Z][A-Z0-9_]+)["']\s*\]
    |
    os\.environ\.get\(\s*["'](?P<get>[A-Z][A-Z0-9_]+)["']
        (?:\s*,\s*(?P<get_default>[^)]+?))?
    \s*\)
    |
    os\.getenv\(\s*["'](?P<getenv>[A-Z][A-Z0-9_]+)["']
        (?:\s*,\s*(?P<getenv_default>[^)]+?))?
    \s*\)
    """,
    re.X,
)

# TypeScript runtime env access: `env.NAME` (worker pattern). Defaults
# typically live next to the access via `?? "x"` / `|| "x"` but we don't
# attempt to parse them here — TS env reads are reported as optional
# unless wrangler [vars] gives them a literal value.
TS_REF_RE = re.compile(r"""\benv\.(?P<name>[A-Z][A-Z0-9_]+)\b""")
# Catches TS `Env` interface property declarations like
# `JWT_SECRET?: string;` — covers casted-access reads such as
# `(env as Env & { JWT_SECRET?: string }).JWT_SECRET` that hide the
# property name from the bare TS_REF_RE above.
TS_TYPE_DECL_RE = re.compile(
    r"""^\s*(?P<name>[A-Z][A-Z0-9_]+)\??:\s*(?:string|number|boolean)""",
    re.M,
)

WRANGLER_VAR_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*=\s*(.+?)\s*$", re.M)
BICEP_SECRETREF_RE = re.compile(
    r"\{\s*name:\s*'([A-Z][A-Z0-9_]+)'\s*,\s*secretRef:\s*'([a-z0-9-]+)'"
)
BICEP_VALUE_RE = re.compile(
    r"\{\s*name:\s*'([A-Z][A-Z0-9_]+)'\s*,\s*value:\s*'([^']*)'"
)
TF_ENV_BLOCK_RE = re.compile(
    r"environment\s*\{\s*variables\s*=\s*merge\([^,]+,\s*\{(.*?)\}\s*\)\s*\}",
    re.S,
)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

# Names that are NOT environment variables (worker bindings to Durable
# Objects, KV namespaces, R2 buckets, D1 databases, AI binding, etc.).
WORKER_BINDING_NAMES = {
    # Workers AI / Analytics Engine / static assets bindings.
    "AI", "ANALYTICS", "ASSETS",
    # KV namespaces (declared under [[kv_namespaces]] in wrangler.toml).
    "AI_RESPONSE_CACHE_KV_ID", "AI_RESPONSE_CACHE_KV_ID_NE_INDIA",
    "BOT_HTML_CACHE", "CF_EDGE_CACHE", "CONTENT_CACHE", "CONTENT_DB",
    "RATE_LIMIT", "SYLLABUS_INDEX",
    # Durable Objects.
    "CHAT_SESSION", "RATE_LIMITER", "RATE_LIMITER_DO", "SEASON_CACHE_DO",
    # R2 buckets.
    "R2_MEDIA",
    # mTLS cert binding. (PAGES_ORIGIN was removed — it's a real
    # plaintext [vars] env var in workers/edge-proxy/wrangler.toml.)
    "MTLS_CERT",
}

# Names that look like env vars in regex matches but are platform-
# managed (AWS Lambda runtime), not operator-controlled.
PY_NOISE_NAMES: set[str] = {
    "AWS_LAMBDA_FUNCTION_NAME",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
}


# ---------------------------------------------------------------------------
# Service definitions
# ---------------------------------------------------------------------------

@dataclass
class Service:
    key: str
    title: str
    blurb: str
    code_dirs: list[Path] = field(default_factory=list)
    code_lang: str = "py"
    deploy_files: list[Path] = field(default_factory=list)


SERVICES: list[Service] = [
    Service(
        key="aca-backend",
        title="ACA backend (`syrabit-backend`)",
        blurb=(
            "FastAPI runtime in `artifacts/syrabit-backend/`, deployed to "
            "Azure Container Apps via `infra/azure/aca-syrabit-backend.bicep`. "
            "The bicep `env:` array is the canonical wiring."
        ),
        code_dirs=[REPO / "artifacts/syrabit-backend"],
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
# Secret classification
# ---------------------------------------------------------------------------

SECRET_NAME_HINTS = (
    "_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_DSN", "_JWT",
    "MONGO_URL", "DATABASE_URL", "API_KEY", "CREDENTIALS", "PRIVATE_KEY",
    # NOTE: `_ARN` removed — Secrets Manager ARNs are public references.
)

NON_SECRET_OVERRIDES = {
    "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_GLACIER_REGION",
    "AWS_NATIVE_PRIMARY_REGION", "AWS_NATIVE_SECONDARY_REGION",
    "AWS_SES_REGION", "BEDROCK_EMBED_REGION", "SES_REGION",
    "VERTEX_LOCATION",
    "WORKERS_EMBED_URL", "BACKEND_URL", "EDGE_WORKER_URL",
    "EDGE_WORKER_PREVIEW_URL", "PUBLIC_BASE_URL", "POSTHOG_HOST",
    "CF_EDGE_KV_CACHE_URL", "CF_EDGE_PROXY_URL", "CF_API_DOMAIN",
    "CF_AI_GATEWAY_URL", "AZURE_FORM_RECOGNIZER_ENDPOINT",
    "BACKEND_WEBHOOK_URL", "PAGES_ORIGIN",
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
# Per-reference metadata
# ---------------------------------------------------------------------------

@dataclass
class RefMeta:
    """Aggregated metadata for a single env var across all call sites."""
    name: str
    required: bool = False  # True iff at least one call site uses subscript
    default: str | None = None  # first non-None default literal seen
    source: str | None = None  # repo-relative file:line of first reference

    def merge(self, *, required: bool, default: str | None,
              source: str | None) -> None:
        if required:
            self.required = True
        if self.default is None and default is not None:
            self.default = default
        if self.source is None and source is not None:
            self.source = source


def _normalize_default(raw: str | None) -> str | None:
    """Trim and collapse whitespace; preserve string-literal quoting."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # Collapse internal whitespace runs but keep single spaces.
    raw = re.sub(r"\s+", " ", raw)
    return raw


def scan_python_dir(root: Path) -> dict[str, RefMeta]:
    out: dict[str, RefMeta] = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if "__pycache__" in rel:
            continue
        if "/tests/" in rel or rel.endswith("_test.py") or "/test_" in rel:
            continue
        if "/scripts/" in rel and "ci/" not in rel:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in PY_REF_RE.finditer(text):
            name = m.group("sub") or m.group("get") or m.group("getenv")
            if not name or name in PY_NOISE_NAMES:
                continue
            required = bool(m.group("sub"))
            default = _normalize_default(
                m.group("get_default") or m.group("getenv_default")
            )
            line = text.count("\n", 0, m.start()) + 1
            source = f"{rel}:{line}"
            meta = out.setdefault(name, RefMeta(name=name))
            meta.merge(required=required, default=default, source=source)
    return out


def scan_ts_dir(root: Path) -> dict[str, RefMeta]:
    """TS env access doesn't expose required/default at the regex level
    (defaults are typically `?? "x"` next to the access). Returns each
    ref as optional with no default; source is first occurrence."""
    out: dict[str, RefMeta] = {}
    if not root.exists():
        return out
    for path in sorted(list(root.rglob("*.ts")) + list(root.rglob("*.mjs"))):
        rel = path.relative_to(REPO).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in TS_REF_RE.finditer(text):
            name = m.group("name")
            if not name or name in WORKER_BINDING_NAMES:
                continue
            line = text.count("\n", 0, m.start()) + 1
            meta = out.setdefault(name, RefMeta(name=name))
            meta.merge(required=False, default=None, source=f"{rel}:{line}")
        for m in TS_TYPE_DECL_RE.finditer(text):
            name = m.group("name")
            if not name or name in WORKER_BINDING_NAMES:
                continue
            line = text.count("\n", 0, m.start()) + 1
            meta = out.setdefault(name, RefMeta(name=name))
            meta.merge(required=False, default=None, source=f"{rel}:{line}")
    return out


# ---------------------------------------------------------------------------
# Deploy-infra parsing
# ---------------------------------------------------------------------------

def parse_bicep(path: Path) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return (secret_refs, plain_values, sources) where sources maps
    each var name to `relpath:line` of its bicep declaration."""
    if not path.exists():
        return {}, {}, {}
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(REPO).as_posix()
    secrets: dict[str, str] = {}
    plains: dict[str, str] = {}
    sources: dict[str, str] = {}
    for m in BICEP_SECRETREF_RE.finditer(text):
        name = m.group(1)
        secrets[name] = m.group(2)
        sources[name] = f"{rel}:{text.count(chr(10), 0, m.start()) + 1}"
    for m in BICEP_VALUE_RE.finditer(text):
        name = m.group(1)
        plains[name] = m.group(2)
        sources.setdefault(
            name, f"{rel}:{text.count(chr(10), 0, m.start()) + 1}"
        )
    return secrets, plains, sources


def parse_lambda_tf(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return (env_name -> source_expression, env_name -> relpath:line)."""
    if not path.exists():
        return {}, {}
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(REPO).as_posix()
    block = TF_ENV_BLOCK_RE.search(text)
    if not block:
        return {}, {}
    block_start_line = text.count("\n", 0, block.start()) + 1
    out: dict[str, str] = {}
    sources: dict[str, str] = {}
    for offset, line in enumerate(block.group(1).splitlines()):
        m = re.match(r"\s*([A-Z][A-Z0-9_]+)\s*=\s*(.+?)\s*$", line)
        if m:
            value = m.group(2).rstrip(",").strip()
            if value.startswith("#"):
                continue
            name = m.group(1)
            out[name] = value
            sources[name] = f"{rel}:{block_start_line + offset}"
    return out, sources


def parse_wrangler_vars(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return (var_name -> literal_value, var_name -> relpath:line)."""
    if not path.exists():
        return {}, {}
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(REPO).as_posix()
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    in_vars = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_vars = stripped.endswith(".vars]") or stripped == "[vars]"
            continue
        if not in_vars:
            continue
        m = re.match(r"\s*([A-Z][A-Z0-9_]+)\s*=\s*(.+?)\s*$", line)
        if m:
            name = m.group(1)
            values[name] = m.group(2).strip()
            sources[name] = f"{rel}:{lineno}"
    return values, sources


# ---------------------------------------------------------------------------
# Doc rendering
# ---------------------------------------------------------------------------

@dataclass
class Row:
    name: str
    typ: str       # "🔒 secret" | "⚙️ config"
    required: str  # "required" | "optional" | "—"
    default: str   # rendered default (literal) or "—"
    wired: str     # "✅ ..." | "❌ ..."
    source: str    # `relpath:line` (code or deploy)
    notes: str


def _shorten(s: str, limit: int = 60) -> str:
    s = s.strip()
    if len(s) > limit:
        return s[: limit - 1] + "…"
    return s


def _fmt_default(meta: RefMeta | None,
                 deploy_value: str | None) -> str:
    """Prefer code-supplied default; fall back to deploy literal value."""
    if meta and meta.default is not None:
        return f"`{_shorten(meta.default)}`"
    if deploy_value is not None:
        return f"`{_shorten(deploy_value)}` (deploy)"
    return "—"


def _required_label(meta: RefMeta | None) -> str:
    if meta is None:
        return "—"
    return "required" if meta.required else "optional"


def _src_label(meta: RefMeta | None, deploy_source: str | None) -> str:
    if meta and meta.source:
        return f"`{meta.source}`"
    if deploy_source:
        return f"`{deploy_source}` (deploy)"
    return "—"


def render_table(rows: list[Row]) -> str:
    lines = [
        "| env var | type | required? | default | wired in deploy infra? | source | notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: x.name):
        lines.append(
            f"| `{r.name}` | {r.typ} | {r.required} | {r.default} | "
            f"{r.wired} | {r.source} | {r.notes} |"
        )
    return "\n".join(lines)


def build_doc() -> str:
    bicep_secrets, bicep_plains, bicep_sources = parse_bicep(
        REPO / "infra/azure/aca-syrabit-backend.bicep"
    )
    bicep_set = set(bicep_secrets) | set(bicep_plains)

    tf_envs, tf_sources = parse_lambda_tf(
        REPO / "artifacts/syrabit/infra/aws/lambda-batch-jobs.tf"
    )
    tf_secret_arns = {
        k for k in tf_envs
        if k.endswith("_SECRET_ARN") or k.endswith("_SECRET")
    }

    edge_vars, edge_sources = parse_wrangler_vars(
        REPO / "workers/edge-proxy/wrangler.toml"
    )
    embed_vars, embed_sources = parse_wrangler_vars(
        REPO / "artifacts/syrabit/workers/embed-worker/wrangler.toml"
    )
    email_vars, email_sources = parse_wrangler_vars(
        REPO / "workers/email-worker/wrangler.toml"
    )

    sections: list[str] = []
    for svc in SERVICES:
        if svc.code_lang == "py":
            code_refs: dict[str, RefMeta] = {}
            for d in svc.code_dirs:
                for k, v in scan_python_dir(d).items():
                    if k in code_refs:
                        code_refs[k].merge(
                            required=v.required, default=v.default,
                            source=v.source,
                        )
                    else:
                        code_refs[k] = v
        else:
            code_refs = {}
            for d in svc.code_dirs:
                for k, v in scan_ts_dir(d).items():
                    if k in code_refs:
                        code_refs[k].merge(
                            required=v.required, default=v.default,
                            source=v.source,
                        )
                    else:
                        code_refs[k] = v

        rows: list[Row] = []

        if svc.key == "aca-backend":
            interesting = set(code_refs) | bicep_set
            for name in interesting:
                meta = code_refs.get(name)
                if name in bicep_secrets:
                    wired = f"✅ secretRef `{bicep_secrets[name]}`"
                elif name in bicep_plains:
                    wired = "✅ literal value"
                else:
                    wired = "❌ code-only"
                deploy_value = bicep_plains.get(name)
                deploy_src = bicep_sources.get(name)
                if name in code_refs and name in bicep_set:
                    note = "code-referenced + wired"
                elif name in bicep_set:
                    note = "wired but no code reference found (deploy-time only)"
                else:
                    note = "code-referenced only"
                rows.append(Row(
                    name=name,
                    typ="🔒 secret" if is_secret(name, set(bicep_secrets)) else "⚙️ config",
                    required=_required_label(meta),
                    default=_fmt_default(meta, deploy_value),
                    wired=wired,
                    source=_src_label(meta, deploy_src),
                    notes=note,
                ))
        elif svc.key == "aca-jobs":
            interesting = set(code_refs) | set(tf_envs)
            for name in interesting:
                meta = code_refs.get(name)
                if name in tf_envs:
                    wired = "✅ Lambda env (TF)"
                else:
                    wired = "❌ in-process / ACA-only"
                deploy_value = tf_envs.get(name)
                deploy_src = tf_sources.get(name)
                note = ("Lambda + ACA"
                        if name in tf_envs and name in code_refs
                        else "TF-wired only" if name in tf_envs
                        else "code-only")
                rows.append(Row(
                    name=name,
                    typ="🔒 secret" if is_secret(name, tf_secret_arns) else "⚙️ config",
                    required=_required_label(meta),
                    default=_fmt_default(meta, deploy_value),
                    wired=wired,
                    source=_src_label(meta, deploy_src),
                    notes=note,
                ))
        else:
            # workers
            wvars = {"edge-proxy": edge_vars, "embed-worker": embed_vars,
                     "email-worker": email_vars}[svc.key]
            wsrc = {"edge-proxy": edge_sources, "embed-worker": embed_sources,
                    "email-worker": email_sources}[svc.key]
            interesting = set(code_refs) | set(wvars)
            for name in interesting:
                meta = code_refs.get(name)
                if name in wvars:
                    wired = "✅ wrangler [vars]"
                else:
                    wired = "❌ wrangler secret (operator-set)"
                deploy_value = wvars.get(name)
                deploy_src = wsrc.get(name)
                rows.append(Row(
                    name=name,
                    typ="🔒 secret" if is_secret(name, set()) else "⚙️ config",
                    required=_required_label(meta),
                    default=_fmt_default(meta, deploy_value),
                    wired=wired,
                    source=_src_label(meta, deploy_src),
                    notes="",
                ))

        deploy_paths = ", ".join(
            f"`{p.relative_to(REPO).as_posix()}`" for p in svc.deploy_files
        )
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
        "## Column semantics\n\n"
        "- **required?** — `required` if at least one call site uses the\n"
        "  raise-on-missing form (`os.environ[\"X\"]`); `optional` if every\n"
        "  call site uses `os.environ.get(...)` / `os.getenv(...)`.\n"
        "  Worker (TS) refs are reported as `optional` because TS defaults\n"
        "  typically live next to the access via `?? \"x\"` and aren't\n"
        "  parsed by the regex extractor — treat the deploy-infra wiring\n"
        "  as the source of truth for required-on-deploy.\n"
        "- **default** — the literal default expression seen in the first\n"
        "  call site that supplies one. If the code didn't supply a\n"
        "  default but the deploy infra hard-codes a literal (bicep\n"
        "  `value:` / TF / wrangler `[vars]`), that value is shown with a\n"
        "  `(deploy)` suffix. `—` means the var has no default and must\n"
        "  be supplied at runtime.\n"
        "- **source** — `relpath:line` of the first reference. Code refs\n"
        "  win over deploy refs; deploy refs are shown with a `(deploy)`\n"
        "  suffix when the var has no code reference.\n\n"
        "## Sources scanned\n\n"
        "- ACA backend: `artifacts/syrabit-backend/**/*.py` (excluding `tests/`, `scripts/`, `__pycache__`)\n"
        "- Background jobs: `artifacts/syrabit-backend/aca_jobs/`, `artifacts/syrabit/services/backend/lambda_batch/`\n"
        "- Workers: `workers/edge-proxy/src/`, `artifacts/syrabit/workers/embed-worker/src/`, `workers/email-worker/src/`\n"
        "- Deploy infra: `infra/azure/aca-syrabit-backend.bicep`, `artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`, the three wrangler.toml files\n\n"
        "## Limitations\n\n"
        "- Regex extraction (no AST) — vars built from `f\"PREFIX_{var}\"`\n"
        "  strings or destructured TS objects are NOT captured.\n"
        "- One-off operator scripts under `artifacts/syrabit-backend/scripts/`\n"
        "  are intentionally excluded — they don't run in production.\n"
        "- `secret?` classification is heuristic (name-suffix + bicep\n"
        "  `secretRef` wiring); see `NON_SECRET_OVERRIDES` in the script.\n"
        "- A `❌ not wired` row in the ACA-backend table can mean: (a) the\n"
        "  operator is expected to inject it via the ACA env block by hand,\n"
        "  (b) the code path is dead, or (c) the value is sourced from\n"
        "  another infra file (`infra/aws/account-billing.tf`, upstream\n"
        "  Key Vault) — review case-by-case.\n"
        "- TS `required?` is always `optional` because the regex extractor\n"
        "  does not parse `?? \"x\"` / `|| \"x\"` defaults next to access.\n"
        "  Confirm runtime-required workers vars from the deploy infra and\n"
        "  the worker source itself.\n\n"
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
