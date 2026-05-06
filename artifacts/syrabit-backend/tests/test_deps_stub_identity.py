"""Regression guard for Task #774 / Task #784.

The cross-test pollution fix in ``tests/_deps_stub.py::install_deps_stub``
relies on an *in-place mutation* contract: when ``force=True`` is called
on an already-installed synthetic stub, the helper MUST mutate that
existing module object instead of constructing a fresh
``types.ModuleType("deps")`` and swapping ``sys.modules`` to it.

If a future "simplification" reverts the helper to building a new
module each call, then any route module that previously bound names
via ``from deps import db, security, ...`` at import time will keep
pointing at the *old* module, while a test's local ``import deps``
resolves to the *new* one. Tests then have to monkeypatch BOTH —
exactly the divergence Task #774 eliminated.

This file fails immediately if that contract is broken.
"""
from __future__ import annotations

import sys

from tests._deps_stub import install_deps_stub


_TRACKED_ATTRS = (
    "db",
    "security",
    "pg_pool",
    "redis_client",
    "supa",
    "pwd_ctx",
    "logger",
    "is_mongo_available",
    "mark_mongo_down",
    "_create_supa",
    "sarvam_llm_client",
    "_assert_not_cms_context",
)


def test_install_deps_stub_force_returns_same_module_object():
    """Two ``force=True`` calls must yield the *same* module object.

    Module identity is what keeps the conftest's pinned stub, every
    route's captured ``mon.deps`` reference, and the test's local
    ``import deps`` all pointing at one shared dict.
    """
    first = install_deps_stub(force=True)
    second = install_deps_stub(force=True)

    assert first is second, (
        "install_deps_stub(force=True) returned a different module on "
        "the second call — the in-place reuse contract is broken. See "
        "tests/_deps_stub.py and Task #774 for why this matters."
    )
    assert sys.modules["deps"] is first, (
        "sys.modules['deps'] no longer points at the stub returned by "
        "install_deps_stub — module identity has been swapped."
    )
    assert getattr(first, "_is_syrabit_test_stub", False) is True


def test_install_deps_stub_force_preserves_attribute_identity():
    """Top-level attributes must keep their identity across reinstalls.

    Route modules do ``from deps import db, security, pg_pool, ...`` at
    import time, which captures the *object* (not the attribute name).
    If ``install_deps_stub(force=True)`` rebuilds these as new objects,
    the route's bound names silently desync from ``deps.<name>`` and
    monkeypatching one no longer affects the other.
    """
    install_deps_stub(force=True)
    before = {name: getattr(sys.modules["deps"], name) for name in _TRACKED_ATTRS}

    install_deps_stub(force=True)
    after = {name: getattr(sys.modules["deps"], name) for name in _TRACKED_ATTRS}

    mismatches = [n for n in _TRACKED_ATTRS if before[n] is not after[n]]
    assert not mismatches, (
        "install_deps_stub(force=True) replaced the identity of these "
        f"top-level attributes: {mismatches}. Route modules that did "
        "`from deps import <name>` at import time will now diverge "
        "from `sys.modules['deps'].<name>`. See Task #774."
    )


def test_route_module_deps_identity_matches_sys_modules():
    """A representative route's bound ``deps`` names must equal the stub's.

    ``routes/admin_monetization.py`` does both ``import deps`` and
    ``from deps import _create_supa, db, supa`` at module-load time. The
    conftest pins the synthetic stub before any route is imported, so
    the route's captured references should always coincide with
    ``sys.modules['deps'].<name>`` — including after a later
    ``install_deps_stub(force=True)``.
    """
    install_deps_stub(force=True)

    from routes import admin_monetization as mon

    deps_mod = sys.modules["deps"]

    assert mon.deps is deps_mod, (
        "routes.admin_monetization.deps is no longer the same module "
        "object as sys.modules['deps']. install_deps_stub must mutate "
        "the existing stub in place; see Task #774."
    )
    assert mon.db is deps_mod.db, (
        "routes.admin_monetization.db diverged from sys.modules['deps'].db "
        "— the in-place attribute identity contract is broken."
    )
    assert mon.supa is deps_mod.supa
    assert mon._create_supa is deps_mod._create_supa

    # And the contract must survive another forced reinstall.
    install_deps_stub(force=True)
    deps_mod = sys.modules["deps"]
    assert mon.deps is deps_mod
    assert mon.db is deps_mod.db
    assert mon.supa is deps_mod.supa
    assert mon._create_supa is deps_mod._create_supa
