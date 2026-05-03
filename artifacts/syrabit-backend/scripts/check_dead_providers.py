#!/usr/bin/env python3
"""Task #297 — CI guard against banned/dead provider tokens.

Fails the build if any of the following appear outside an allowlist:

  - ``cartesia``     (purged 2026-05-03)
  - ``os.environ.get('GEMINI_API_KEY')`` outside ``config.py``
    (Gemini is reached via the CF AI Gateway slug
    ``google-ai-studio/v1beta/openai``; direct env-var reads are banned
    to keep the BYOK lifecycle honest)

Intentionally NOT scanned:

  - ``perplexity`` — every hit refers to PerplexityBot, the AI
    search-engine crawler we want to serve content TO (robots.txt,
    GEO/JSONLD, bot-discovery dashboards). Not used as an LLM provider.
  - ``groq`` / ``openrouter`` / ``cerebras`` — still referenced by the
    BYOK secret-audit lifecycle in ``server.py`` and by historical
    comments / model registries. Removal is tracked by the Railway
    env-var audit table that already prints on every boot.

Allowlisted paths (banned tokens may legitimately appear here):
  - attached_assets/**   (raw user uploads / log snapshots)
  - .local/**            (agent scratch / session plans)
  - **/CHANGELOG*        (historical release notes)
  - tests/test_provider_dispatch.py  (asserts cartesia is ABSENT)
  - this script itself
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # repo root (script is at artifacts/syrabit-backend/scripts/)
BACKEND = ROOT / "artifacts" / "syrabit-backend"
FRONTEND = ROOT / "artifacts" / "syrabit"

# Banned LLM provider literals. ``perplexity`` is matched only as a
# bare token (not as part of ``PerplexityBot`` / ``Perplexity-User`` /
# ``perplexity.ai``) since those are crawler / robots.txt references
# we legitimately serve content to.
BANNED_LITERAL = re.compile(
    r"\b(cartesia|groq|cerebras|openrouter)\b", re.IGNORECASE
)
# NOTE: ``perplexity`` is intentionally NOT scanned. Every repo hit refers
# to PerplexityBot / Perplexity-User (the AI search-engine crawler we
# legitimately serve content TO via robots.txt + GEO/JSONLD), or to user-
# facing copy describing AI citation behavior. Perplexity is not used as
# an LLM provider anywhere in the active routing chain.
DIRECT_GEMINI = re.compile(r"""os\.environ\.get\(\s*['"]GEMINI_API_KEY""")

ALLOWLIST_PARTS = {
    "attached_assets",
    ".local",
    "node_modules",
    "build",
    "dist",
}
# Files where banned tokens legitimately remain — load-bearing throttle /
# credit-lifecycle / model-registry / vendored-SDK / regression-test code.
# Each entry has a documented reason and is scoped narrowly.
ALLOWLIST_FILES = {
    # The guard itself + its pytest wrapper — the literals are quoted strings.
    "artifacts/syrabit-backend/scripts/check_dead_providers.py",
    "artifacts/syrabit-backend/tests/test_dead_providers_guard.py",
    # Regression tests that ASSERT removed providers stay removed.
    "artifacts/syrabit-backend/tests/test_provider_dispatch.py",
    # Throttle / 429-burst lifecycle (groq throttle metric still emitted by
    # production providers as a side-channel; renaming requires a coordinated
    # migration of the CF AI Gateway analytics pipeline — tracked separately).
    "artifacts/syrabit-backend/metrics.py",
    "artifacts/syrabit-backend/routes/cms_sarvam_health.py",
    # SLM model-registry / smart-keypool definitions (load-bearing for the
    # SmartKeyPool eviction loop and per-provider RPM env-var lookup).
    "artifacts/syrabit-backend/llm.py",
    "artifacts/syrabit-backend/routes/admin_advanced.py",
    "artifacts/syrabit-backend/routes/admin_monetization.py",
    # Chat-speedup metrics docstring references the legacy chain comparison.
    "artifacts/syrabit-backend/chat_speedup_metrics.py",
    # Admin Vertex describe-string mentions the historical chain ordering.
    "artifacts/syrabit-backend/routes/admin_vertex.py",
    # BYOK secret-audit lifecycle still scans these env vars on boot to warn
    # operators they can be deleted from Railway (Task #297 documented).
    "artifacts/syrabit-backend/server.py",
    # Vendored Emergent SDK — third-party code, not edited.
    "artifacts/syrabit-backend/emergentintegrations/llm/chat.py",
    # Regression tests that exercise legacy provider paths.
    "artifacts/syrabit-backend/tests/test_workers_ai_429_throttle_alert.py",
    "artifacts/syrabit-backend/tests/test_vertex_chat_fastpath.py",
    "artifacts/syrabit-backend/tests/test_llm_cf_cache_headers.py",
    # AdminHealth retains a 'groq_throttle' state field consumed by the
    # backend cms_sarvam_health endpoint (kept until that field is renamed).
    "artifacts/syrabit/src/components/admin/AdminHealth.jsx",
    # Provider credit-matrix doc lists removed providers in the
    # "explicitly excluded" section so operators understand they are NOT
    # part of PROVIDER_PRIORITY (documentation-only references).
    "artifacts/syrabit/docs/infra/provider-credit-matrix.md",
    # Deployment / performance-monitoring runbooks — historical config
    # snippets (env-var tables, gcloud secret examples, OTel tag values)
    # operators still encounter on legacy CF Workers + Cloud Run deploys.
    "artifacts/syrabit-backend/DEPLOY.md",
    "artifacts/syrabit-backend/CLOUDRUN-DEPLOY.md",
    "artifacts/syrabit-backend/docs/PERFORMANCE_MONITORING.md",
    # Routing-contract docstring inside the Sarvam/Qwen translator (the
    # Qwen fallback path documented in replit.md "LLM Providers").
    "artifacts/syrabit-backend/routes/ai_chat.py",
    # OpenAPI dump utility docstring lists eagerly-imported optional
    # integrations the script has to stub out for CI.
    "artifacts/syrabit-backend/scripts/dump_openapi.py",
    # EN/AS LLM benchmark script — references cerebras/groq/gemini env-var
    # names solely to hydrate them from the gunicorn process environ for
    # comparison probing. They are NOT part of any production routing pool.
    "artifacts/syrabit-backend/scripts/bench_en_as.py",
    # Workers-AI chat integration test docstring documents the prod
    # failure mode it simulates.
    "artifacts/syrabit-backend/tests/test_workers_ai_chat_integration.py",
    # Service / route docstrings + user-facing SEO copy that reference
    # the historical multi-provider chain. Tracked for a future docs
    # sweep but not load-bearing for the routing contract.
    "artifacts/syrabit-backend/topic_discovery_service.py",
    "artifacts/syrabit-backend/seo_remediation_service.py",
    "artifacts/syrabit-backend/seo_engine.py",
    "artifacts/syrabit-backend/providers/cloudflare_ai.py",
    "artifacts/syrabit-backend/routes/admin_content.py",
    "artifacts/syrabit-backend/routes/edu_study.py",
    "artifacts/syrabit-backend/routes/admin_health.py",
    # Cloudflare Pages deploy doc — historical configuration example
    # showing the legacy SmartKeyPool registry. Kept verbatim because
    # operators following the runbook still see this shape on older
    # production deploys until the CF Workers env is fully migrated.
    "artifacts/syrabit/CLOUDFLARE_PAGES.md",
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
    for ln, line in enumerate(text.splitlines(), 1):
        if BANNED_LITERAL.search(line):
            failures.append(f"{p.relative_to(ROOT)}:{ln}: banned token → {line.strip()[:120]}")
        if is_code and DIRECT_GEMINI.search(line) and p.name != "config.py":
            failures.append(f"{p.relative_to(ROOT)}:{ln}: direct GEMINI_API_KEY env read → {line.strip()[:120]}")
    return failures


def main() -> int:
    targets: list[Path] = []
    for base in (BACKEND, FRONTEND):
        if not base.exists():
            continue
        for ext in ("*.py", "*.jsx", "*.js", "*.ts", "*.tsx", "*.md"):
            targets.extend(base.rglob(ext))

    failures: list[str] = []
    for p in targets:
        if _is_allowlisted(p):
            continue
        failures.extend(_scan_file(p))

    if failures:
        print("Dead-provider guard FAILED:")
        for f in failures:
            print(f"  {f}")
        print(f"\n{len(failures)} violation(s). See artifacts/syrabit-backend/scripts/check_dead_providers.py for the allowlist.")
        return 1

    print(f"Dead-provider guard OK — scanned {len(targets)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
