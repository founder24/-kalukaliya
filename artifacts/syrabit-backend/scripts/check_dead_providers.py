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
  - ``groq`` / ``openrouter`` — still referenced by the BYOK secret-audit
    lifecycle in ``server.py`` and by historical comments / model
    registries. Removal is tracked by the Railway env-var audit table
    that already prints on every boot.
  - ``cerebras`` / ``cohere`` / ``voyage_ai`` / ``baseten`` /
    ``cartesia`` / ``bedrock`` / ``gemini`` / ``xai`` /
    ``openai_direct`` — purged in Task #491 from runtime code paths.
    Banned bare-token to keep the dispatch chain honest. The audit
    allowlist is the only place where the literals may legitimately
    appear (this script + the V4 changelog).

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
# Task #347 extended this guard to cover Stripe, Quge5, Resend and the
# bedrock-proxy worker — all decommissioned providers whose runtime
# code paths have been deleted. New SDK imports / wrangler bindings /
# env-var reads for these vendors must be caught at CI time so they
# cannot creep back into the active routing / payment / email chain.
#
# OpenAI / Anthropic / xAI / Grok are intentionally NOT scanned here:
# they appear extensively as legitimate AI-crawler operator names in
# the bot-detection (utils.py, cf_bot_report.py, analytics_helpers.py)
# and Workers AI model aliases (``openai/gpt-oss-20b``). Banning their
# bare tokens would produce thousands of false positives. Their
# providers / SDKs were already deleted by Task #347 and are guarded by
# the absence of the matching dispatch branches in PROVIDER_PRIORITY.
#
# Match rules:
#   * ``\b...\b`` — bare-token, case-insensitive.
#   * Allowlisted files below cover load-bearing references (legacy
#     throttle metrics, BYOK env-audit warnings, deprecation stubs,
#     regression tests, historical runbooks).
BANNED_LITERAL = re.compile(
    r"\b(cerebras|cohere|voyage_ai|cartesia|groq|openrouter|quge5|baseten|bedrock|gemini|xai|openai_direct)\b",
    re.IGNORECASE,
)
# Stripe / Resend / bedrock-proxy are tracked separately because their
# tokens are short enough (or natural-language enough) to collide with
# unrelated copy ("strip", "resend email" UI verbs, deprecation
# comments). Match only when used as a Python/JS import or SDK call.
# Env-var lookups (``RESEND_API_KEY``, ``STRIPE_SECRET_KEY``) are NOT
# scanned here — the audit-on-boot lifecycle in server.py already warns
# operators when these legacy vars are still set in the runtime env so
# they can be deleted from the secret store. Banning the env-var name
# itself would require rewriting ~80 call sites in admin-alert routes
# and tests in a single task; that Resend → SendGrid backend cleanup
# is tracked as a Task #347 follow-up.
BANNED_VENDOR_USES = re.compile(
    r"(import\s+stripe\b|from\s+stripe\s+import|stripe\.(?:api_key|Webhook|checkout|Customer)|"
    r"import\s+resend\b|from\s+resend\s+import|(?<![A-Za-z0-9_])resend\.Emails|"
    r"workers/bedrock-proxy|providers\.bedrock_proxy|bedrock_proxy_url\s*=|"
    # Task #347 — block bare SDK imports for the four LLM vendors that
    # were code-decommissioned. The bare token names (``openai``,
    # ``anthropic``, ``xai``, ``grok``) are NOT scanned because they
    # collide with legitimate AI-crawler operator strings in the
    # bot-detection / robots.txt / Workers AI model-alias surfaces
    # (e.g. ``@cf/openai/gpt-oss-20b`` is a Cloudflare model alias —
    # the OpenAI SDK is gone but the model name remains).
    r"^\s*import\s+openai\b|^\s*from\s+openai\s+import|"
    r"^\s*import\s+anthropic\b|^\s*from\s+anthropic\s+import|"
    r"^\s*import\s+xai\b|^\s*from\s+xai\s+import|"
    r"^\s*import\s+grok\b|^\s*from\s+grok\s+import|"
    r"providers\.(?:openai|anthropic|xai|grok)\b)"
)
# NOTE: ``perplexity`` is intentionally NOT scanned. Every repo hit refers
# to PerplexityBot / Perplexity-User (the AI search-engine crawler we
# legitimately serve content TO via robots.txt + GEO/JSONLD), or to user-
# facing copy describing AI citation behavior. Perplexity is not used as
# an LLM provider anywhere in the active routing chain.
DIRECT_GEMINI = re.compile(r"""os\.environ\.get\(\s*['"]GEMINI_API_KEY""")

# Task #494 — `vertex_format.format_with_vertex` MUST be reached only
# through the `content_formatter.format_content` dispatcher so the
# Workers-AI Llama-3.3-70b fallback is never silently bypassed (V4 §15
# §6). Direct imports from any other module re-introduce the
# Vertex-only single point of failure that #494 was created to remove.
DIRECT_VERTEX_FORMAT_IMPORT = re.compile(
    r"from\s+vertex_format\s+import\s+[^#\n]*\bformat_with_vertex\b"
    r"|vertex_format\.format_with_vertex\("
)
# Files that are allowed to call `format_with_vertex` directly: the
# dispatcher itself, the module that defines it, and the contract test
# that pins its signature.
VERTEX_FORMAT_DIRECT_CALLERS = {
    "artifacts/syrabit-backend/content_formatter.py",
    "artifacts/syrabit-backend/vertex_format.py",
    "artifacts/syrabit-backend/tests/test_vertex_format_contract.py",
    # Task #494 — chat hot-path exemption. The Assamese translate-polish
    # in routes/ai_chat.py is TTFT-critical and intentionally bypasses
    # the dispatcher: routing it through `format_content` would add
    # Llama-70b fallback latency + formatter telemetry to a streaming
    # request where the correct degradation is to ship the un-polished
    # IndicTrans2 output immediately. The dispatcher remains the only
    # path for store-time content (notes / chapters / Assamese bulk
    # translate), see `routes/admin_pipeline.py` and `admin_advanced.py`.
    "artifacts/syrabit-backend/routes/ai_chat.py",
}

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
    # Failure-mode strings + AI Gateway routing tables that name historical
    # providers ("Bedrock-Cohere", "Groq", "Azure OpenAI") inside operator
    # documentation. Removing these would erase the routing breadcrumb
    # operators rely on when reading dashboards.
    "artifacts/syrabit-backend/routes/admin_azure_ai.py",
    "artifacts/syrabit/services/backend/azure_ai/openai.py",
    "artifacts/syrabit/docs/infra/observability.md",
    "artifacts/syrabit/docs/infra/providers-architecture.md",
    "artifacts/syrabit/docs/features/azure-native.md",
    "artifacts/syrabit/docs/infra/startup-credits-migration.md",
    # Ads runbook — historical ad-network comparison tables that name
    # Quge5 alongside Adsterra/PropellerAds/AdPushup in the "considered
    # but rejected" section. Documentation-only references.
    "artifacts/syrabit/ADS.md",
    # Legacy disabled-network registry — `'quge5'` literal kept inside
    # `DISABLED_NETWORKS` so any old admin config row still gets stripped
    # at boot. Removing the literal would silently let cached configs
    # re-enable the network.
    "artifacts/syrabit/src/utils/adsConfig.js",
    # Task #347 decommission runbook — operator-facing doc that
    # intentionally names every removed vendor + shows the old SDK
    # import lines so readers can grep their own deploys for survivors.
    "artifacts/syrabit/docs/infra/providers-task-347-decommission.md",
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
    is_doc  = p.suffix == ".md"
    for ln, line in enumerate(text.splitlines(), 1):
        # Skip deprecation comments / docstrings that NAME a removed
        # provider — they're load-bearing for operators reading
        # `git blame` and the dead-provider regression tests. Heuristic:
        # the line either starts with a comment prefix OR contains the
        # task tag that announced the removal.
        stripped = line.lstrip()
        is_comment = (
            stripped.startswith("#") or stripped.startswith("//")
            or stripped.startswith("*") or stripped.startswith("/*")
        )
        is_removal_note = (
            "Task #347" in line or "removed" in line.lower()
            or "deprecated" in line.lower() or "REMOVED" in line
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
            # Task #494 — the file-level allowlist exempts legacy banned
            # tokens (groq/cerebras prose, vendored SDK code) but MUST
            # NOT exempt the new direct vertex_format ban: every caller
            # of `vertex_format.format_with_vertex` outside the
            # dispatcher / module / contract test re-introduces the
            # Vertex-only single point of failure §15 §6 was created to
            # remove. Run the vertex_format scan unconditionally for any
            # code file that is not on VERTEX_FORMAT_DIRECT_CALLERS.
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
        print("Dead-provider guard FAILED:")
        for f in failures:
            print(f"  {f}")
        print(f"\n{len(failures)} violation(s). See artifacts/syrabit-backend/scripts/check_dead_providers.py for the allowlist.")
        return 1

    print(f"Dead-provider guard OK — scanned {len(targets)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
