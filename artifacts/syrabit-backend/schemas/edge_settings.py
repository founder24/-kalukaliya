"""Shared constants and contract guard for the edge SPA-title-miss settings.

Both the proxy filter in ``routes/admin_edge_analytics.py`` and the snapshot
tests in ``tests/test_admin_edge_settings.py`` import from here so that adding
a new edge field only ever requires touching one place.

``PATCHABLE_SETTINGS_KEYS`` is the subset of ``CANONICAL_SETTINGS_KEYS`` that
clients may overwrite via PATCH.  The two sets are kept here together so they
cannot drift apart independently.

Call ``assert_patch_contract`` once per PATCH route at module import time to
enforce both invariants the moment the backend starts.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Type
    from pydantic import BaseModel

CANONICAL_SETTINGS_KEYS: frozenset[str] = frozenset({
    "disabled",
    "env_disabled",
    "env_threshold",
    "kv_override_set",
    "threshold",
})

PATCHABLE_SETTINGS_KEYS: frozenset[str] = frozenset({
    "disabled",
    "threshold",
})


def assert_patch_contract(
    patch_model: "Type[BaseModel]",
    patchable_keys: frozenset[str],
    canonical_keys: frozenset[str],
    *,
    model_name: "str | None" = None,
) -> None:
    """Assert the PATCH contract invariants at import time.

    Two checks are enforced:

    1. The Pydantic *patch_model*'s field names must exactly equal
       *patchable_keys* — no more, no fewer.  This prevents the model and
       the outbound-payload filter from drifting apart silently.

    2. Every key in *patchable_keys* must also appear in *canonical_keys*
       (the GET allowlist).  A patchable field absent from the GET response
       would be write-only — clients could set it but never read it back.

    Call this helper once per PATCH route at module import time so any
    contract violation surfaces immediately when the backend starts, not
    only when a test happens to run.

    Args:
        patch_model: A Pydantic ``BaseModel`` subclass whose ``model_fields``
            must exactly match *patchable_keys*.
        patchable_keys: The frozenset of field names the PATCH endpoint
            accepts and forwards to the downstream service.
        canonical_keys: The frozenset of field names exposed by the GET
            endpoint.  Must be a superset of *patchable_keys*.
        model_name: Optional display name used in error messages.
            Defaults to ``patch_model.__name__``.

    Raises:
        AssertionError: On any contract violation, with a descriptive message
            explaining which keys are mismatched and how to fix them.
    """
    name = model_name or patch_model.__name__
    actual_fields = frozenset(patch_model.model_fields)
    assert actual_fields == patchable_keys, (
        f"{name} fields {actual_fields} do not match "
        f"PATCHABLE_SETTINGS_KEYS {patchable_keys} in schemas/edge_settings.py. "
        "Update both together."
    )
    assert patchable_keys <= canonical_keys, (
        f"PATCHABLE_SETTINGS_KEYS {patchable_keys} is not a subset of "
        f"CANONICAL_SETTINGS_KEYS {canonical_keys} in schemas/edge_settings.py. "
        "Every patchable key must also appear in CANONICAL_SETTINGS_KEYS so it can be "
        "read back via GET; add the missing key(s) to CANONICAL_SETTINGS_KEYS."
    )
