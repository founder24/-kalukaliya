"""Shared constants and contract guard for edge-settings PATCH routes.

Both the proxy filter in ``routes/admin_edge_analytics.py`` and the snapshot
tests in ``tests/test_admin_edge_settings.py`` import from here so that adding
a new edge field only ever requires touching one place.

``PATCHABLE_SETTINGS_KEYS`` is the subset of ``CANONICAL_SETTINGS_KEYS`` that
clients may overwrite via PATCH.  The two sets are kept here together so they
cannot drift apart independently.

Use the ``@patch_route_contract(patchable_keys, canonical_keys)`` class
decorator to enforce both invariants at class definition time — the moment
Python evaluates the decorated class body.  This makes the guard opt-out
rather than opt-in so new PATCH route models cannot accidentally skip it.

``assert_patch_contract`` remains public for direct unit-test usage.

----------------------------------------------------------------------------
Adding a new edge-settings PATCH route
----------------------------------------------------------------------------

Follow these steps every time you introduce a PATCH route in a
``routes/admin_edge_*.py`` module:

1. **Define key sets here** (``schemas/edge_settings.py``).

   Add two ``frozenset`` constants that describe the new route's field
   contract, e.g.::

       CANONICAL_PREWARM_KEYS: frozenset[str] = frozenset({
           "enabled",
           "schedule_utc",
           "env_schedule_utc",
           "kv_override_set",
       })
       PATCHABLE_PREWARM_KEYS: frozenset[str] = frozenset({
           "enabled",
           "schedule_utc",
       })

   Rules:
   * ``PATCHABLE_*`` must be a **strict subset** of ``CANONICAL_*``.
     A patchable key absent from the GET response would be write-only —
     clients could set it but never read the effective value back.
   * Keep both sets in this file so they can never drift independently.

2. **Decorate the Pydantic model** in the route module.

   Apply ``@patch_route_contract`` *directly above* the class definition::

       from schemas.edge_settings import (
           CANONICAL_PREWARM_KEYS,
           PATCHABLE_PREWARM_KEYS,
           patch_route_contract,
       )

       @patch_route_contract(PATCHABLE_PREWARM_KEYS, CANONICAL_PREWARM_KEYS)
       class PrewarmSettingsPatch(BaseModel):
           enabled: Optional[bool] = None
           schedule_utc: Optional[str] = None

   The decorator calls ``assert_patch_contract`` at **class definition
   time** (i.e. on ``import``).  A mismatch between the model's fields
   and ``PATCHABLE_*`` raises ``AssertionError`` immediately when the
   backend starts — not buried in a test run hours later.

   Naming convention: the class **must** end in ``Patch`` and inherit
   from ``pydantic.BaseModel``.  The CI script
   ``scripts/ci/check_patch_route_contract.py`` uses this convention to
   find every PATCH model and verify the decorator is present.

3. **Filter the outbound payload** in the PATCH handler.

   Use ``PATCHABLE_*`` as an explicit allowlist when building the JSON
   sent to the edge worker so a future model change cannot accidentally
   forward an unreadable field::

       payload = {
           k: v
           for k, v in data.model_dump(exclude_none=True).items()
           if k in PATCHABLE_PREWARM_KEYS
       }

4. **Add snapshot tests** analogous to ``tests/test_admin_edge_settings.py``:

   * ``test_get_*_returns_exactly_canonical_keys`` — pins the GET key set.
   * ``test_patchable_keys_are_subset_of_canonical_keys`` — subset guard.
   * ``test_patch_drops_non_patchable_fields`` — payload filter check.
   * ``test_import_time_assert_fires_when_patch_model_diverges`` — reload
     the route module after monkeypatching the key frozenset; verify an
     ``AssertionError`` is raised immediately.

5. **Verify the CI guard passes.**

   Run ``python scripts/ci/check_patch_route_contract.py`` locally before
   pushing.  It will fail if it finds a ``*Patch(BaseModel)`` class in any
   ``routes/admin_edge_*.py`` file that does not have the
   ``@patch_route_contract`` decorator on the immediately preceding line.
----------------------------------------------------------------------------
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Callable, Type
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

# ── Prewarm-settings contract (Task #87) ──────────────────────────────────────
# GET /admin/edge/prewarm-settings will return these four fields once the edge
# worker KV endpoint goes live.  PATCHABLE_PREWARM_KEYS is the writable subset
# (on/off switch + schedule).  The remaining two are read-only reflections.
CANONICAL_PREWARM_KEYS: frozenset[str] = frozenset({
    "enabled",
    "env_schedule_utc",
    "kv_override_set",
    "schedule_utc",
})

PATCHABLE_PREWARM_KEYS: frozenset[str] = frozenset({
    "enabled",
    "schedule_utc",
})

# ── Cache-calendar-settings contract (Task #87) ───────────────────────────────
# GET /admin/edge/cache-calendar-settings will return these three fields once
# the edge worker KV endpoint goes live.  PATCHABLE_CACHE_CALENDAR_KEYS is the
# writable subset — only force_season can be overridden at runtime.
CANONICAL_CACHE_CALENDAR_KEYS: frozenset[str] = frozenset({
    "env_force_season",
    "force_season",
    "kv_override_set",
})

PATCHABLE_CACHE_CALENDAR_KEYS: frozenset[str] = frozenset({
    "force_season",
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


def patch_route_contract(
    patchable_keys: frozenset[str],
    canonical_keys: frozenset[str],
) -> "Callable[[Type[BaseModel]], Type[BaseModel]]":
    """Class decorator that enforces the PATCH contract at class definition time.

    Apply this decorator to every Pydantic model used as the request body of a
    PATCH route that forwards fields to the edge worker.  The contract is checked
    the moment Python evaluates the decorated class body (i.e. at module import
    time), so a misconfigured model is caught immediately on backend start rather
    than only when a specific test happens to run.

    This makes the guard *opt-out* rather than *opt-in*: the safety check is
    co-located with the model declaration and cannot be forgotten by accident.

    Usage::

        @patch_route_contract(PATCHABLE_SETTINGS_KEYS, CANONICAL_SETTINGS_KEYS)
        class MySettingsPatch(BaseModel):
            threshold: Optional[int] = None
            disabled: Optional[bool] = None

    Args:
        patchable_keys: The frozenset of field names the PATCH endpoint accepts.
            Must exactly match the decorated model's ``model_fields``.
        canonical_keys: The frozenset of field names exposed by the companion
            GET endpoint.  Must be a superset of *patchable_keys*.

    Returns:
        A class decorator that calls ``assert_patch_contract`` immediately and
        returns the class unchanged if the contract passes.

    Raises:
        AssertionError: At class definition time on any contract violation.
    """
    def decorator(cls: "Type[BaseModel]") -> "Type[BaseModel]":
        assert_patch_contract(cls, patchable_keys, canonical_keys)
        return cls

    return decorator
