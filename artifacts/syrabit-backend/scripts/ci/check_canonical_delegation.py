#!/usr/bin/env python3
"""Task #559 — Canonical specialist-delegation umbrella CI guard.

This script is the **single source of truth** for the per-feature
"who owns this responsibility" lock declared in
`infra/four-cloud-delegation.md` and `infra/v4-locked-architecture.md`
§17. It supersedes Task #297 / #347 / #491 / #494 / #554's individual
`check_dead_providers.py` guard (now a thin shim that imports `main`
from this module — see `artifacts/syrabit-backend/scripts/check_dead_providers.py`).

Two banks of checks run on every PR:

A. **DEAD-PROVIDER BANK** (the historical Task #297→#554 logic).
   Carried over verbatim so the upgrade is behaviour-preserving.

B. **CANONICAL-DELEGATION BANK** (new in Task #559). Each enforcement
   row maps 1-to-1 onto a row of the per-feature canonical map in
   `infra/four-cloud-delegation.md`. Today only the rows whose
   underlying code has actually shipped are *banned* — the rest carry
   `# TODO Task #557 / #558 / web-push` markers and become hard
   failures the moment those sub-tasks merge. This keeps the umbrella
   honest: documented in the canonical map, but never red on `main`
   for work that has not yet landed.

Currently enforced canonical rows:

  - **English chat dispatch** = strict 2-list driven by
    `cost_caps._select_chat_primary()` over
    `[vertex, workers_ai_llama32_3b]`. Static
    `PROVIDER_PRIORITY["english_rag_chat"]` must equal that pair.
  - **Assamese chat dispatch** = strict 2-list
    `[sarvam, workers_ai_indic]` (Sarvam primary, IndicTrans2 last
    resort). Vertex / Azure-OpenAI may not appear in the chain.
  - **Voice paywall** = `routes/voice.py` `/tts`, `/stt`, `/voice/voice`
    must all sit behind `Depends(require_paid_plan)`. (This duplicates
    `scripts/check_budget_ceiling.py` deliberately so a guard skip
    here cannot smuggle the paywall removal past the umbrella.)
  - **Azure-OpenAI ban** (Task #554) = `azure_openai|AzureOpenAI|
    AZURE_OPENAI_*|gpt-4.1-nano` is bare-token banned (already covered
    by the DEAD-PROVIDER bank; re-asserted here so the canonical map
    row points to a single guard).

TODO-gated (banned only after the parent task merges):

  - **Email**  → SES sole tier-1, web-push self-hosted (Task #557).
                 `sendgrid|SendGridAPIClient|SENDGRID_API_KEY|resend|
                 RESEND_API_KEY|firebase_admin|FCM_SERVER_KEY|
                 FIREBASE_SERVICE_ACCOUNT` — bans flip on once the
                 SES-cutover + self-hosted web-push PRs land.
  - **Observability**  → GCP Cloud Trace single exporter (Task #558).
                 Multiple OTEL exporters + Sentry tracing literals
                 stay tolerated until the narrowing ships.

Allowlist semantics are inherited from the legacy guard (file-level
allowlist + comment / removal-note skip heuristic + ALLOWLIST_PARTS
directories). New umbrella checks reuse the same allowlist so the
behaviour-preserving promise above holds.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Script location: artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py
# parents[0] = ci, [1] = scripts, [2] = syrabit-backend, [3] = artifacts, [4] = repo root.
ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "artifacts" / "syrabit-backend"
FRONTEND = ROOT / "artifacts" / "syrabit"

# ──────────────────────────────────────────────────────────────────────
# A. DEAD-PROVIDER BANK (carried verbatim from Task #297 → #554)
# ──────────────────────────────────────────────────────────────────────
BANNED_LITERAL = re.compile(
    r"\b(cerebras|cohere|voyage_ai|cartesia|groq|openrouter|quge5|"
    r"azure_openai|AzureOpenAI|gpt-4\.1-nano)\b|"
    r"\bAZURE_OPENAI_[A-Z0-9_]+\b",
    re.IGNORECASE,
)
BANNED_VENDOR_USES = re.compile(
    r"(import\s+stripe\b|from\s+stripe\s+import|stripe\.(?:api_key|Webhook|checkout|Customer)|"
    r"import\s+resend\b|from\s+resend\s+import|(?<![A-Za-z0-9_])resend\.Emails|"
    r"workers/bedrock-proxy|providers\.bedrock_proxy|bedrock_proxy_url\s*=|"
    r"^\s*import\s+openai\b|^\s*from\s+openai\s+import|"
    r"^\s*import\s+anthropic\b|^\s*from\s+anthropic\s+import|"
    r"^\s*import\s+xai\b|^\s*from\s+xai\s+import|"
    r"^\s*import\s+grok\b|^\s*from\s+grok\s+import|"
    r"providers\.(?:openai|anthropic|xai|grok)\b)"
)
DIRECT_GEMINI = re.compile(r"""os\.environ\.get\(\s*['"]GEMINI_API_KEY""")

# Task #494 — vertex_format.format_with_vertex must be reached only via
# content_formatter.format_content (V4 §15 §6). Direct callers re-introduce
# the Vertex-only single point of failure.
DIRECT_VERTEX_FORMAT_IMPORT = re.compile(
    r"from\s+vertex_format\s+import\s+[^#\n]*\bformat_with_vertex\b"
    r"|vertex_format\.format_with_vertex\("
)
VERTEX_FORMAT_DIRECT_CALLERS = {
    "artifacts/syrabit-backend/content_formatter.py",
    "artifacts/syrabit-backend/vertex_format.py",
    "artifacts/syrabit-backend/tests/test_vertex_format_contract.py",
    "artifacts/syrabit-backend/routes/ai_chat.py",
}

ALLOWLIST_PARTS = {
    "attached_assets",
    ".local",
    "node_modules",
    "build",
    "dist",
}
# Files where banned tokens legitimately remain. Inherited verbatim from the
# legacy guard so no previously-passing PR turns red on the umbrella swap.
ALLOWLIST_FILES = {
    # Guard + its pytest wrapper — literals are quoted.
    "artifacts/syrabit-backend/scripts/check_dead_providers.py",
    "artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py",
    "artifacts/syrabit-backend/tests/test_dead_providers_guard.py",
    "artifacts/syrabit-backend/tests/test_provider_dispatch.py",
    "artifacts/syrabit-backend/tests/test_credit_drain_order.py",
    "artifacts/syrabit-backend/metrics.py",
    "artifacts/syrabit-backend/routes/cms_sarvam_health.py",
    "artifacts/syrabit-backend/llm.py",
    "artifacts/syrabit-backend/routes/admin_advanced.py",
    "artifacts/syrabit-backend/routes/admin_monetization.py",
    "artifacts/syrabit-backend/chat_speedup_metrics.py",
    "artifacts/syrabit-backend/routes/admin_vertex.py",
    "artifacts/syrabit-backend/server.py",
    "artifacts/syrabit-backend/emergentintegrations/llm/chat.py",
    "artifacts/syrabit-backend/tests/test_workers_ai_429_throttle_alert.py",
    "artifacts/syrabit-backend/tests/test_vertex_chat_fastpath.py",
    "artifacts/syrabit-backend/tests/test_llm_cf_cache_headers.py",
    "artifacts/syrabit/src/components/admin/AdminHealth.jsx",
    "artifacts/syrabit/docs/infra/provider-credit-matrix.md",
    "artifacts/syrabit-backend/DEPLOY.md",
    "artifacts/syrabit-backend/CLOUDRUN-DEPLOY.md",
    "artifacts/syrabit-backend/docs/PERFORMANCE_MONITORING.md",
    "artifacts/syrabit-backend/routes/ai_chat.py",
    "artifacts/syrabit-backend/scripts/dump_openapi.py",
    "artifacts/syrabit-backend/scripts/bench_en_as.py",
    "artifacts/syrabit-backend/tests/test_workers_ai_chat_integration.py",
    "artifacts/syrabit-backend/topic_discovery_service.py",
    "artifacts/syrabit-backend/seo_remediation_service.py",
    "artifacts/syrabit-backend/seo_engine.py",
    "artifacts/syrabit-backend/providers/cloudflare_ai.py",
    "artifacts/syrabit-backend/routes/admin_content.py",
    "artifacts/syrabit-backend/routes/edu_study.py",
    "artifacts/syrabit-backend/routes/admin_health.py",
    "artifacts/syrabit/CLOUDFLARE_PAGES.md",
    "artifacts/syrabit-backend/routes/admin_azure_ai.py",
    "artifacts/syrabit/services/backend/azure_ai/openai.py",
    "artifacts/syrabit/docs/infra/observability.md",
    "artifacts/syrabit/docs/infra/providers-architecture.md",
    "artifacts/syrabit/docs/features/azure-native.md",
    "artifacts/syrabit/docs/infra/startup-credits-migration.md",
    "artifacts/syrabit/ADS.md",
    "artifacts/syrabit/src/utils/adsConfig.js",
    "artifacts/syrabit/docs/infra/providers-task-347-decommission.md",
    "artifacts/syrabit-backend/utils.py",
    "artifacts/syrabit-backend/cf_bot_report.py",
    "artifacts/syrabit-backend/scripts/cf_waf_soften.py",
    "artifacts/syrabit-backend/tests/test_ai_discoverability_policy.py",
    "artifacts/syrabit/vite.config.js",
    "artifacts/syrabit-backend/providers/workers_embed.py",
    "artifacts/syrabit-backend/tests/test_admin_aws_native_route.py",
    "artifacts/syrabit-backend/tests/test_admin_dashboard_metrics_throttle_tiles.py",
    "artifacts/syrabit-backend/tests/test_assamese_rag_namespace.py",
    "artifacts/syrabit-backend/tests/test_embed_failover_degraded_mode.py",
    "artifacts/syrabit-backend/tests/test_ai_gateway_observability.py",
    "artifacts/syrabit/src/components/admin/AdminVertexPanel.jsx",
    "artifacts/syrabit/src/components/admin/AdminAzureAiPanel.jsx",
    "artifacts/syrabit/src/components/admin/AdminAwsNativePanel.jsx",
    "artifacts/syrabit/src/components/admin/EmbedBackfillPill.jsx",
    "artifacts/syrabit/src/components/admin/EmbedBackfillPill.test.jsx",
    "artifacts/syrabit/src/components/admin/vertex-panel/StatusHeader.jsx",
    "artifacts/syrabit/docs/infra/aws-landing-zone.md",
    "artifacts/syrabit/docs/infra/azure-landing-zone.md",
    "artifacts/syrabit/docs/infra/gcp-landing-zone.md",
    "artifacts/syrabit/docs/features/aws-native.md",
    "artifacts/syrabit/docs/infra/credit-runway-cost-model.md",
    "artifacts/syrabit-backend/tests/test_provider_priority_locked.py",
    "artifacts/syrabit-backend/tests/test_chat_rpm_soft_shed.py",
    "artifacts/syrabit-backend/tests/test_assamese_routing_chain_e2e.py",
    "artifacts/syrabit-backend/tests/test_translate_fallback_chain.py",
    "artifacts/syrabit-backend/tests/test_session_fallback.py",
    "artifacts/syrabit-backend/tests/test_slo_emitter.py",
    "artifacts/syrabit-backend/tests/test_credit_burn_meters.py",
    "artifacts/syrabit-backend/tests/observability/test_todo_558_regex.py",
    "artifacts/syrabit-backend/tests/observability/test_no_sentry_tracing.py",
    "artifacts/syrabit-backend/tests/observability/test_otel_exporter_locked.py",
    "artifacts/syrabit-backend/providers/azure_speech.py",
    "artifacts/syrabit-backend/azure_ai_metrics.py",
    "artifacts/syrabit-backend/RUNBOOK.md",
    "artifacts/syrabit-backend/CREDITS.md",
    # Task #559 — canonical map + ADR + cutover runbook intentionally name
    # the historical providers in operator-facing prose.
    "infra/four-cloud-delegation.md",
    "docs/architecture/adr/0003-canonical-strict-specialist-delegation.md",
    "artifacts/syrabit/docs/infra/canonical-delegation-cutover.md",
}
ALLOWLIST_NAME_PREFIXES = ("CHANGELOG",)


def _is_allowlisted(p: Path) -> bool:
    parts = set(p.parts)
    if parts & ALLOWLIST_PARTS:
        return True
    rel = p.relative_to(ROOT).as_posix()
    if rel in ALLOWLIST_FILES:
        return True
    if any(p.name.startswith(prefix) for prefix in ALLOWLIST_NAME_PREFIXES):
        return True
    return False


def _scan_file(p: Path) -> list[str]:
    failures: list[str] = []
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return failures
    is_code = p.suffix in (".py", ".js", ".jsx", ".ts", ".tsx")
    is_doc = p.suffix == ".md"
    for ln, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        is_comment = (
            stripped.startswith("#") or stripped.startswith("//")
            or stripped.startswith("*") or stripped.startswith("/*")
        )
        is_removal_note = (
            "Task #347" in line or "Task #491" in line or "Task #554" in line
            or "Task #559" in line
            or "removed" in line.lower() or "deprecated" in line.lower()
            or "retired" in line.lower() or "decommission" in line.lower()
            or "REMOVED" in line or "retired" in line.lower()
            or "legacy" in line.lower() or "previous" in line.lower()
            or "disabled" in line.lower() or "backfill" in line.lower()
            or "no longer" in line.lower()
        )
        if (is_comment or is_doc) and is_removal_note:
            continue
        if BANNED_LITERAL.search(line):
            failures.append(f"{p.relative_to(ROOT)}:{ln}: banned token → {line.strip()[:120]}")
        if BANNED_VENDOR_USES.search(line):
            failures.append(f"{p.relative_to(ROOT)}:{ln}: banned vendor use (Task #347) → {line.strip()[:120]}")
        if is_code and DIRECT_GEMINI.search(line) and p.name != "config.py":
            failures.append(f"{p.relative_to(ROOT)}:{ln}: direct GEMINI_API_KEY env read → {line.strip()[:120]}")
        if is_code and DIRECT_VERTEX_FORMAT_IMPORT.search(line):
            rel = p.relative_to(ROOT).as_posix()
            if rel not in VERTEX_FORMAT_DIRECT_CALLERS:
                failures.append(
                    f"{p.relative_to(ROOT)}:{ln}: direct vertex_format.format_with_vertex use "
                    f"(Task #494: route through content_formatter.format_content) → {line.strip()[:120]}"
                )
    return failures


def _check_aca_jobs_manifest() -> list[str]:
    """Task #551 §E — every aca_jobs/<name>.py must appear in the
    Lambda manifest (carried forward unchanged)."""
    failures: list[str] = []
    aca_dir = BACKEND / "aca_jobs"
    manifest_path = ROOT / "infra" / "aws" / "lambda" / "manifest.json"
    if not aca_dir.exists():
        return failures
    if not manifest_path.exists():
        failures.append(
            f"{manifest_path.relative_to(ROOT)}: missing — Task #551 §E requires the migrated-jobs manifest."
        )
        return failures
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        failures.append(f"{manifest_path.relative_to(ROOT)}: invalid JSON ({exc})")
        return failures
    migrated = {
        entry.get("aca_module", "").split(".", 1)[-1]
        for entry in manifest.get("migrated_jobs", [])
    }
    exempt = set(manifest.get("exempt_modules", [])) | {"__init__"}
    for path in sorted(aca_dir.glob("*.py")):
        name = path.stem
        if name in exempt:
            continue
        if name not in migrated:
            failures.append(
                f"aca_jobs/{path.name}: not present in infra/aws/lambda/manifest.json — "
                f"Task #551 §E requires every aca_jobs/* loop to have a Lambda counterpart."
            )
    return failures


# ──────────────────────────────────────────────────────────────────────
# B. CANONICAL-DELEGATION BANK (Task #559)
# ──────────────────────────────────────────────────────────────────────
def _check_chat_chains() -> list[str]:
    """English + Assamese static `PROVIDER_PRIORITY` rows must equal the
    canonical 2-element chains. The English chain head flips at runtime
    via `cost_caps._select_chat_primary()`; the static list captures the
    *membership* invariant the dispatcher's pool-existence check reads.
    """
    failures: list[str] = []
    cfg = BACKEND / "config.py"
    if not cfg.exists():
        return failures
    src = cfg.read_text(encoding="utf-8", errors="ignore")

    # english_rag_chat — must contain exactly {"vertex", "workers_ai_llama32_3b"}.
    en_match = re.search(
        r'"english_rag_chat"\s*:\s*\[(.*?)\]', src, re.DOTALL,
    )
    if not en_match:
        failures.append(
            "config.py: PROVIDER_PRIORITY['english_rag_chat'] missing — "
            "Task #559 canonical map requires the dynamic 2-list head."
        )
    else:
        items = {tok.strip().strip(',').strip("'\"") for tok in en_match.group(1).split() if tok.strip().strip(',')}
        items = {x for x in items if x}
        if items != {"vertex", "workers_ai_llama32_3b"}:
            failures.append(
                f"config.py: PROVIDER_PRIORITY['english_rag_chat'] must be exactly "
                f"{{'vertex', 'workers_ai_llama32_3b'}}; found {items}. "
                f"(Task #559 — see infra/four-cloud-delegation.md row 'English chat dispatch')."
            )

    # assamese_rag_chat — must contain exactly {"sarvam", "workers_ai_indic"}.
    as_match = re.search(
        r'"assamese_rag_chat"\s*:\s*\[(.*?)\]', src, re.DOTALL,
    )
    if not as_match:
        failures.append(
            "config.py: PROVIDER_PRIORITY['assamese_rag_chat'] missing — "
            "Task #559 canonical map requires Sarvam primary + IndicTrans2 fallback."
        )
    else:
        items = {tok.strip().strip(',').strip("'\"") for tok in as_match.group(1).split() if tok.strip().strip(',')}
        items = {x for x in items if x}
        if items != {"sarvam", "workers_ai_indic"}:
            failures.append(
                f"config.py: PROVIDER_PRIORITY['assamese_rag_chat'] must be exactly "
                f"{{'sarvam', 'workers_ai_indic'}}; found {items}. "
                f"(Task #559 — Assamese chain is locked: Sarvam primary, IndicTrans2 last resort)."
            )
    return failures


def _check_chat_primary_selector() -> list[str]:
    """`cost_caps._select_chat_primary` must remain the single chokepoint
    that drives the runtime chat head, with the credit-runway flip and
    the `CHAT_PRIMARY_OVERRIDE` operator knob both present."""
    failures: list[str] = []
    cc = BACKEND / "cost_caps.py"
    if not cc.exists():
        failures.append("cost_caps.py: missing — Task #559 needs _select_chat_primary().")
        return failures
    src = cc.read_text(encoding="utf-8", errors="ignore")
    if "_select_chat_primary" not in src:
        failures.append(
            "cost_caps.py: _select_chat_primary() helper missing — "
            "Task #559 canonical English-chat row requires the runway-aware selector."
        )
    if "CHAT_PRIMARY_OVERRIDE" not in src:
        failures.append(
            "cost_caps.py: CHAT_PRIMARY_OVERRIDE knob missing — "
            "operator override is part of the canonical chat-dispatch contract."
        )
    return failures


def _check_voice_paywall() -> list[str]:
    """Task #549 + #559 — `/tts`, `/stt`, `/voice/voice` must each sit
    behind `Depends(require_paid_plan)`. Mirrors the assertion in
    `scripts/check_budget_ceiling.py` so a single-guard skip cannot
    smuggle the paywall removal past the umbrella."""
    failures: list[str] = []
    voice = BACKEND / "routes" / "voice.py"
    if not voice.exists():
        return failures
    src = voice.read_text(encoding="utf-8", errors="ignore")
    if "require_paid_plan" not in src:
        failures.append(
            "routes/voice.py: missing import/use of require_paid_plan "
            "(Task #559 canonical 'Voice paywall' row)."
        )
        return failures
    for route in ("/voice/tts", "/voice/stt", "/voice/voice"):
        idx = src.find(f'"{route}"')
        if idx < 0:
            idx = src.find(f"'{route}'")
        if idx < 0:
            # Fail-loud per V4 §12 (Task #559 round-2 review): a missing
            # canonical route is a routing-contract drift, not a no-op.
            failures.append(
                f"routes/voice.py: canonical route {route} not found "
                f"(Task #559 'Voice paywall' row — must remain present "
                f"and gated by Depends(require_paid_plan))."
            )
            continue
        # Look at the next ~50 lines after the route declaration for the
        # require_paid_plan dependency.
        window = src[idx:idx + 2500]
        if "Depends(require_paid_plan)" not in window:
            failures.append(
                f"routes/voice.py: route {route} must use Depends(require_paid_plan) "
                f"(Task #559 canonical 'Voice paywall' row; mirrors check_budget_ceiling.py)."
            )
    return failures


# ─── TODO-gated bans (kept dormant until the parent task ships) ──────
# Task #557 — SES sole tier-1 transactional email + self-hosted web-push.
# When that task lands, replace the empty pattern with the regex below.
TODO_557_PATTERN = (
    r"sendgrid|SendGridAPIClient|SENDGRID_API_KEY|"
    r"resend|RESEND_API_KEY|"
    r"firebase_admin|FCM_SERVER_KEY|FIREBASE_SERVICE_ACCOUNT"
)
# Task #558 — observability narrowing to a single GCP Cloud Trace exporter.
# Bans:
#   * `OTEL_TRACES_EXPORTER=<value>,` (multi-exporter env — anything with a
#     trailing comma signals more than one sink).
#   * `traces_sample_rate=<positive number>` (Sentry Performance / tracing).
#     The literal `traces_sample_rate=0` is allowed and is what
#     `observability/sentry_setup.py` ships.
#   * `enable_tracing=True` Sentry-SDK kwarg.
#   * Live use of the Sentry transaction / decorator APIs.
TODO_558_PATTERN = (
    # OTEL exporter env: ban EVERY value except the single literal
    # `googlecloud` (with optional surrounding quotes). Comma-separated
    # multi-value lists, alternative single exporters (otlp / jaeger /
    # zipkin / azure_monitor / console / etc.), and empty string all
    # trip. Negative-lookahead pins the only allowed shape.
    r"OTEL_TRACES_EXPORTER\s*=\s*(?![\"']?googlecloud[\"']?\s*(?:$|[\s;#]))[^\s;#]+|"
    # Also keep the explicit comma-arm so quoted+commaed lists remain
    # caught even when the first token is `googlecloud`.
    r"OTEL_TRACES_EXPORTER\s*=\s*[\"']?[a-z_]+[\"']?\s*,|"
    # Match any positive numeric literal: 1, 0.1, 0.05, 0.001, .25, 1e-3, etc.
    # Allowed (literal zero or zero-only floats): =0, =0.0, =0.00. The negative
    # lookahead on the integer side rules out "=0" / "=0." / "=0.0" / "=0.00";
    # the explicit `0*\.0*[1-9]` arm rules in any positive sub-1.0 fraction.
    r"traces_sample_rate\s*=\s*(?:"
    r"[1-9][0-9]*(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
    r"|0*\.0*[1-9][0-9]*(?:[eE][+-]?[0-9]+)?"
    r"|\.[0-9]*[1-9][0-9]*(?:[eE][+-]?[0-9]+)?"
    r")|"
    r"enable_tracing\s*=\s*True|"
    r"sentry_sdk\.start_transaction|"
    r"@sentry_sdk\.trace\b"
)


def _check_canonical_bank() -> list[str]:
    failures: list[str] = []
    failures.extend(_check_chat_chains())
    failures.extend(_check_chat_primary_selector())
    failures.extend(_check_voice_paywall())
    # TODO Task #557 — uncomment the block below once the SES-only +
    # self-hosted web-push PRs merge:
    # failures.extend(_scan_pattern_global(re.compile(TODO_557_PATTERN, re.IGNORECASE),
    #                                      tag="Task #557 (canonical email/web-push)"))
    # Task #558 — observability narrowing has shipped; the umbrella now
    # bans Sentry tracing literals and multi-exporter OTEL configs.
    failures.extend(_scan_pattern_global(re.compile(TODO_558_PATTERN),
                                         tag="Task #558 (canonical observability)"))
    return failures


def _scan_pattern_global(pat: re.Pattern[str], *, tag: str) -> list[str]:
    """Helper used by TODO-gated bans once they activate."""
    out: list[str] = []
    for base in (BACKEND, FRONTEND):
        if not base.exists():
            continue
        for ext in ("*.py", "*.jsx", "*.js", "*.ts", "*.tsx"):
            for p in base.rglob(ext):
                if _is_allowlisted(p):
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for ln, line in enumerate(text.splitlines(), 1):
                    if pat.search(line):
                        out.append(f"{p.relative_to(ROOT)}:{ln}: {tag} → {line.strip()[:120]}")
    return out


# ──────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    targets: list[Path] = []
    for base in (BACKEND, FRONTEND):
        if not base.exists():
            continue
        for ext in ("*.py", "*.jsx", "*.js", "*.ts", "*.tsx", "*.md"):
            targets.extend(base.rglob(ext))

    failures: list[str] = _check_aca_jobs_manifest()
    failures.extend(_check_canonical_bank())

    for p in targets:
        if _is_allowlisted(p):
            rel = p.relative_to(ROOT).as_posix()
            if (
                p.suffix in (".py", ".js", ".jsx", ".ts", ".tsx")
                and rel not in VERTEX_FORMAT_DIRECT_CALLERS
            ):
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    text = ""
                for ln, line in enumerate(text.splitlines(), 1):
                    if DIRECT_VERTEX_FORMAT_IMPORT.search(line):
                        failures.append(
                            f"{p.relative_to(ROOT)}:{ln}: direct vertex_format.format_with_vertex use "
                            f"(Task #494: route through content_formatter.format_content; "
                            f"file-level allowlist does NOT exempt this check) → "
                            f"{line.strip()[:120]}"
                        )
            continue
        failures.extend(_scan_file(p))

    if failures:
        print("Canonical-delegation guard FAILED:")
        for f in failures:
            print(f"  {f}")
        print(
            f"\n{len(failures)} violation(s). See "
            f"artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py "
            f"for the allowlist + per-feature canonical map."
        )
        return 1

    print(f"Canonical-delegation guard OK — scanned {len(targets)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
