"""Task #382 — Embed-pool rebuild on EMBED_PROVIDER_PRIMARY rollback.

Verifies that flipping ``EMBED_PROVIDER_PRIMARY`` is a real rollback
switch — not just a label. When the env var is set to a legacy
provider name, the embed POOL_WEIGHTS rebuild produced by
``config._build_embed_pool`` must:

  * exclude ``workers_ai_custom`` from the active draw (weight 0);
  * restore positive weights on the legacy chain
    (cohere, voyage_ai, vertex, azure_openai, workers_ai);
  * promote the named primary to the top weight when one is requested.

This pins the rollback contract end-to-end so a future refactor that
silently freezes the pool at module import (or that hardcodes
workers_ai_custom in the literal) is caught by CI.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config


def test_default_pool_pins_workers_ai_custom_as_primary():
    pool = config._build_embed_pool("workers_ai_custom")
    assert pool["workers_ai_custom"] == 10000
    # Every legacy provider remains addressable but at weight 0 so the
    # exclusion-redraw loop only reaches them after the worker fails.
    for legacy in ("cohere", "voyage_ai", "vertex", "azure_openai", "workers_ai"):
        assert pool[legacy] == 0, f"{legacy} should be dormant under workers_ai_custom"


def test_rollback_to_cohere_promotes_cohere_and_zeroes_custom():
    pool = config._build_embed_pool("cohere")
    assert pool["workers_ai_custom"] == 0, (
        "rollback must remove the custom worker from the active draw"
    )
    assert pool["cohere"] >= 10000, "rollback target must take the top weight"
    # Other legacy providers stay reachable as fallbacks.
    assert pool["voyage_ai"] > 0
    assert pool["vertex"] > 0
    assert pool["azure_openai"] > 0
    assert pool["workers_ai"] > 0


def test_rollback_to_voyage_promotes_voyage_only():
    pool = config._build_embed_pool("voyage_ai")
    assert pool["workers_ai_custom"] == 0
    assert pool["voyage_ai"] >= 10000
    # Cohere keeps its default legacy weight, doesn't get promoted.
    assert pool["cohere"] == 1000


def test_unknown_primary_falls_back_to_legacy_distribution():
    # An unrecognised flag value still triggers the rollback branch
    # (workers_ai_custom is forced to 0) so an operator typo doesn't
    # accidentally keep routing to the worker.
    pool = config._build_embed_pool("not-a-real-provider")
    assert pool["workers_ai_custom"] == 0
    assert pool["cohere"] == 1000
    assert pool["voyage_ai"] == 1000


def test_module_pool_weights_match_active_flag():
    # Confirms the import-time rebuild was actually applied and that
    # all three embed sub-pools share the same weights.
    expected = config._build_embed_pool(config.EMBED_PROVIDER_PRIMARY)
    assert config.POOL_WEIGHTS["embed"] == expected
    assert config.POOL_WEIGHTS["embed_en"] == expected
    assert config.POOL_WEIGHTS["embed_indic"] == expected


def test_priority_chain_includes_all_legacy_providers_for_rollback():
    # PROVIDER_PRIORITY drives which providers select_provider is even
    # allowed to consider; if a legacy provider is missing from this
    # list, the rollback can't reach it. Pin the full chain explicitly.
    expected = ["workers_ai_custom", "cohere", "voyage_ai", "vertex", "azure_openai", "workers_ai"]
    for pool_name in ("embed", "embed_en", "embed_indic"):
        assert config.PROVIDER_PRIORITY[pool_name] == expected, (
            f"{pool_name} priority chain missing legacy providers — rollback would be impossible"
        )
