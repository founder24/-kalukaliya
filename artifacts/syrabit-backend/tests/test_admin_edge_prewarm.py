"""Task #87 — Contract tests for prewarm and cache-calendar edge PATCH route stubs.

Tests cover three layers of the @patch_route_contract guard:

  1. Subset invariant — every PATCHABLE_* key must appear in the corresponding
     CANONICAL_* set (no write-only fields).

  2. Decorator fires at definition time — @patch_route_contract raises
     AssertionError synchronously when a synthetic model diverges from the
     patchable key set.

  3. Import-time assert fires on module reload — monkeypatching a PATCHABLE_*
     constant and reloading routes.admin_edge_prewarm must raise AssertionError
     the moment Python evaluates the decorated class body.

No HTTP endpoint tests are included: both routes are stubs that return 501.
Replace them with GET/PATCH snapshot tests analogous to
test_admin_edge_settings.py once the edge worker endpoints go live.
"""
from __future__ import annotations

import importlib
import sys

import pytest

from schemas.edge_settings import (
    CANONICAL_CACHE_CALENDAR_KEYS,
    CANONICAL_PREWARM_KEYS,
    PATCHABLE_CACHE_CALENDAR_KEYS,
    PATCHABLE_PREWARM_KEYS,
    patch_route_contract,
)


# ── 1. Subset invariant ────────────────────────────────────────────────────────

def test_patchable_prewarm_keys_are_subset_of_canonical_prewarm_keys():
    """Every prewarm field writable via PATCH must also appear in the GET
    response (CANONICAL_PREWARM_KEYS) so the frontend can read back what it
    wrote."""
    assert PATCHABLE_PREWARM_KEYS <= CANONICAL_PREWARM_KEYS, (
        f"PATCHABLE_PREWARM_KEYS contains keys absent from CANONICAL_PREWARM_KEYS.\n"
        f"  patchable:  {sorted(PATCHABLE_PREWARM_KEYS)}\n"
        f"  canonical:  {sorted(CANONICAL_PREWARM_KEYS)}\n"
        f"  extra keys: {sorted(PATCHABLE_PREWARM_KEYS - CANONICAL_PREWARM_KEYS)}\n"
        "Add the missing key(s) to CANONICAL_PREWARM_KEYS in schemas/edge_settings.py."
    )


def test_patchable_cache_calendar_keys_are_subset_of_canonical_cache_calendar_keys():
    """Every cache-calendar field writable via PATCH must also appear in the GET
    response (CANONICAL_CACHE_CALENDAR_KEYS) so the frontend can read it back."""
    assert PATCHABLE_CACHE_CALENDAR_KEYS <= CANONICAL_CACHE_CALENDAR_KEYS, (
        f"PATCHABLE_CACHE_CALENDAR_KEYS contains keys absent from CANONICAL_CACHE_CALENDAR_KEYS.\n"
        f"  patchable:  {sorted(PATCHABLE_CACHE_CALENDAR_KEYS)}\n"
        f"  canonical:  {sorted(CANONICAL_CACHE_CALENDAR_KEYS)}\n"
        f"  extra keys: {sorted(PATCHABLE_CACHE_CALENDAR_KEYS - CANONICAL_CACHE_CALENDAR_KEYS)}\n"
        "Add the missing key(s) to CANONICAL_CACHE_CALENDAR_KEYS in schemas/edge_settings.py."
    )


# ── 2. Decorator fires at class definition time ───────────────────────────────

def test_patch_route_contract_fires_for_prewarm_extra_field():
    """Adding a rogue field to a PrewarmSettingsPatch-like model must raise
    AssertionError at class definition time (not later)."""
    with pytest.raises(AssertionError, match="do not match"):
        @patch_route_contract(PATCHABLE_PREWARM_KEYS, CANONICAL_PREWARM_KEYS)
        class _BadPrewarmPatch:
            model_fields = {k: None for k in PATCHABLE_PREWARM_KEYS} | {"rogue_field": None}


def test_patch_route_contract_fires_for_prewarm_missing_field():
    """Dropping a field from PATCHABLE_PREWARM_KEYS in a model must raise
    AssertionError at class definition time."""
    one_key = next(iter(sorted(PATCHABLE_PREWARM_KEYS)))
    with pytest.raises(AssertionError, match="do not match"):
        @patch_route_contract(PATCHABLE_PREWARM_KEYS, CANONICAL_PREWARM_KEYS)
        class _MissingPrewarmPatch:
            model_fields = {one_key: None}  # deliberately missing the other key(s)


def test_patch_route_contract_fires_for_cache_calendar_extra_field():
    """Adding a rogue field to a CacheCalendarSettingsPatch-like model must
    raise AssertionError at class definition time."""
    with pytest.raises(AssertionError, match="do not match"):
        @patch_route_contract(PATCHABLE_CACHE_CALENDAR_KEYS, CANONICAL_CACHE_CALENDAR_KEYS)
        class _BadCacheCalendarPatch:
            model_fields = {k: None for k in PATCHABLE_CACHE_CALENDAR_KEYS} | {"rogue_field": None}


def test_patch_route_contract_fires_for_cache_calendar_missing_field():
    """Removing all fields from a CacheCalendarSettingsPatch-like model must
    raise AssertionError at class definition time."""
    with pytest.raises(AssertionError, match="do not match"):
        @patch_route_contract(PATCHABLE_CACHE_CALENDAR_KEYS, CANONICAL_CACHE_CALENDAR_KEYS)
        class _EmptyCacheCalendarPatch:
            model_fields = {}


# ── 3. Import-time assert fires on module reload ──────────────────────────────

def test_import_time_assert_fires_when_prewarm_patch_model_diverges(monkeypatch):
    """Monkeypatch PATCHABLE_PREWARM_KEYS to a narrower set, then reload
    routes.admin_edge_prewarm — the @patch_route_contract decorator on
    PrewarmSettingsPatch must raise AssertionError immediately."""
    import schemas.edge_settings as edge_settings_mod

    monkeypatch.setattr(
        edge_settings_mod,
        "PATCHABLE_PREWARM_KEYS",
        frozenset({"enabled"}),  # narrower than the real model's two fields
    )

    module_name = "routes.admin_edge_prewarm"
    original_module = sys.modules.pop(module_name, None)
    try:
        with pytest.raises(AssertionError, match="do not match"):
            importlib.import_module(module_name)
    finally:
        if original_module is not None:
            sys.modules[module_name] = original_module
        else:
            sys.modules.pop(module_name, None)


def test_import_time_assert_fires_when_cache_calendar_patch_model_diverges(monkeypatch):
    """Monkeypatch PATCHABLE_CACHE_CALENDAR_KEYS to a divergent set, then
    reload routes.admin_edge_prewarm — the @patch_route_contract decorator on
    CacheCalendarSettingsPatch must raise AssertionError immediately."""
    import schemas.edge_settings as edge_settings_mod

    monkeypatch.setattr(
        edge_settings_mod,
        "PATCHABLE_CACHE_CALENDAR_KEYS",
        frozenset({"nonexistent_field"}),  # diverges from model's {"force_season"}
    )

    module_name = "routes.admin_edge_prewarm"
    original_module = sys.modules.pop(module_name, None)
    try:
        with pytest.raises(AssertionError, match="do not match"):
            importlib.import_module(module_name)
    finally:
        if original_module is not None:
            sys.modules[module_name] = original_module
        else:
            sys.modules.pop(module_name, None)


# ── 4. Module import (live contract check) ────────────────────────────────────

def test_route_module_imports_cleanly():
    """Importing routes.admin_edge_prewarm must not raise.

    This mirrors starting the backend: if either @patch_route_contract assertion
    fails, the backend refuses to start.  The fact this test passes proves both
    PrewarmSettingsPatch and CacheCalendarSettingsPatch currently satisfy their
    contracts.
    """
    mod = importlib.import_module("routes.admin_edge_prewarm")
    assert hasattr(mod, "PrewarmSettingsPatch")
    assert hasattr(mod, "CacheCalendarSettingsPatch")
    assert hasattr(mod, "router")
