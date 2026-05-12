"""Shared constants for the edge SPA-title-miss settings contract.

Both the proxy filter in ``routes/admin_edge_analytics.py`` and the snapshot
tests in ``tests/test_admin_edge_settings.py`` import from here so that adding
a new edge field only ever requires touching one place.
"""
from __future__ import annotations

CANONICAL_SETTINGS_KEYS: frozenset[str] = frozenset({
    "disabled",
    "env_disabled",
    "env_threshold",
    "kv_override_set",
    "threshold",
})
