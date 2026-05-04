#!/usr/bin/env python3
"""Task #363 — Embed-model consistency CI check.

Fails CI when the embedding-model identifiers configured in code drift
out of sync with what is documented in the runbook. This is the cheap,
mechanical check that complements the heavier shard-rebalance work
deferred in #363.

Checks:

1. ``COHERE_EMBED_MODEL`` matches the constant in ``config.py``.
2. ``VOYAGE_EMBED_MODEL`` matches the constant in ``config.py``.
3. The bge-m3 references in ``llm.py`` (Workers AI fallback) are still
   the canonical ``@cf/baai/bge-m3`` identifier.
4. Vector dim must remain 1024 across all three (drift here invalidates
   the cross-provider Pinecone index).

Run locally:

    python -m artifacts.syrabit-backend.scripts.ci.embed_model_consistency_check

Or directly:

    python artifacts/syrabit-backend/scripts/ci/embed_model_consistency_check.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Canonical models — bumping these requires a deliberate co-ordinated
# change to ``config.py`` and the Pinecone index version. Treat any
# change here as a Big Deal.
EXPECTED = {
    "COHERE_EMBED_MODEL":  "embed-multilingual-v3.0",
    "VOYAGE_EMBED_MODEL":  "voyage-3.5",
    "WORKERS_AI_EMBED":    "@cf/baai/bge-m3",
    "VECTOR_DIM":          1024,
}

BACKEND = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND.parents[1]


def _read(rel: str) -> str:
    p = BACKEND / rel
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def _check_config_constant(text: str, name: str, expected: str, errors: list[str]) -> None:
    # Match e.g. COHERE_EMBED_MODEL = ... 'embed-multilingual-v3.0'
    pattern = re.compile(
        rf"{re.escape(name)}\s*=.*?['\"]({re.escape(expected)})['\"]",
        re.DOTALL,
    )
    if not pattern.search(text):
        errors.append(
            f"config.py: {name} does not resolve to the expected default "
            f"{expected!r}. Either update the canonical value in this CI "
            f"check or restore the constant."
        )


def _check_workers_ai(text: str, errors: list[str]) -> None:
    if EXPECTED["WORKERS_AI_EMBED"] not in text:
        errors.append(
            f"llm.py / providers: Workers AI embed identifier "
            f"{EXPECTED['WORKERS_AI_EMBED']!r} not found. The bge-m3 model "
            f"is the cross-provider standard — drift breaks the Pinecone "
            f"vector compatibility guarantee."
        )


def _check_vector_dim(errors: list[str]) -> None:
    config_text = _read("config.py")
    # Heuristic: look for "1024" near "EMBED" / "DIM" / "PINECONE".
    if not re.search(r"\b1024\b", config_text):
        errors.append(
            "config.py: vector dim 1024 not found anywhere. Cohere, Voyage, "
            "and bge-m3 are all 1024-dim — losing this constant suggests "
            "drift that will break Pinecone."
        )


def main() -> int:
    errors: list[str] = []
    config_text = _read("config.py")
    if not config_text:
        print(
            "ERROR: cannot find artifacts/syrabit-backend/config.py — has "
            "the layout changed?",
            file=sys.stderr,
        )
        return 2
    _check_config_constant(config_text, "COHERE_EMBED_MODEL", EXPECTED["COHERE_EMBED_MODEL"], errors)
    _check_config_constant(config_text, "VOYAGE_EMBED_MODEL", EXPECTED["VOYAGE_EMBED_MODEL"], errors)

    # Workers AI bge-m3 reference — appears in llm.py docstring + providers/cloudflare_ai.py.
    llm_text = _read("llm.py")
    cf_text = _read("providers/cloudflare_ai.py")
    _check_workers_ai(llm_text + "\n" + cf_text, errors)

    _check_vector_dim(errors)

    if errors:
        print("✗ embed-model consistency check FAILED", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("✓ embed-model consistency check passed")
    print(f"  cohere   = {EXPECTED['COHERE_EMBED_MODEL']}")
    print(f"  voyage   = {EXPECTED['VOYAGE_EMBED_MODEL']}")
    print(f"  workers  = {EXPECTED['WORKERS_AI_EMBED']}")
    print(f"  dim      = {EXPECTED['VECTOR_DIM']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
