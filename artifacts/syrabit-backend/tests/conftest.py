"""Backend test configuration.

Task #469 — autouse fixture that resets the synthetic ``deps`` stub
between tests so call-history mutations cannot bleed across files.
Task #472 — extends the same snapshot/restore pattern to the
``metrics`` module so a test like
``test_seo_health_alerting.test_dispatch_alert_email_includes_by_sitemap_html``
that pops/reimports ``sys.modules['metrics']`` cannot leave a later
test (e.g. ``test_bing_keyword_pipeline``) staring at a half-built
``metrics`` module that fails ``from metrics import _metrics``.

The stub itself (see ``tests/_deps_stub.py``) now uses ``_MotorDbMock`` /
``_MotorCollectionMock`` so every call to ``await db.<coll>.<method>(...)``
returns an ``AsyncMock`` coroutine regardless of which test ran first.
What this fixture adds on top is the call-history reset: even though the
mock structure is robust, ``mock.call_args`` and ``mock.call_count``
would otherwise accumulate across the whole pytest session, making
``assert_called_once`` style assertions brittle.

Production ``deps`` (the real module, when imported) is intentionally
left alone — we recognise the stub by the ``_is_syrabit_test_stub``
marker. The ``metrics`` snapshot is symmetric: we always restore the
module identity that existed pre-test (since ``metrics`` has no stub
distinction — it's always the real thing in the test process).
"""
import logging
import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# Task #418 — guard against test modules that leak ``logging.disable``.
#
# Task #402 fixed four files that called ``logging.disable(logging.CRITICAL)``
# at module scope without ever restoring it. Any test file that ran after
# such a module silently lost ERROR-log assertions (most painfully
# ``tests/test_vertex_startup_probe.py``) because ``logging.disable`` is a
# process-global switch on ``logging.root.manager.disable``. The per-file
# fix was to wrap the suppression in a try/finally (see
# ``tests/test_provider_dispatch.py``) or in a fixture (see
# ``tests/test_assamese_rag_namespace.py``).
#
# This module-scoped autouse fixture catches the regression at the moment
# it lands. It runs once per test module: pytest tears it down after the
# last test in the module has finished (and after every per-test fixture
# has run its own teardown), so any properly-scoped restore has already
# happened. If ``logging.disable`` is still non-NOTSET at that point, the
# offending module either set it at module scope without a try/finally or
# left a fixture without a yield/finally restore.
#
# The fixture resets the level itself before raising so a single offending
# module fails loud once instead of cascading into every downstream test.
@pytest.fixture(scope="module", autouse=True)
def _no_logging_disable_leak(request):
    yield
    current = logging.root.manager.disable
    if current == logging.NOTSET:
        return
    # Reset so this leak does not contaminate the rest of the suite.
    logging.disable(logging.NOTSET)
    module_name = getattr(request.module, "__name__", "<unknown>")
    module_file = getattr(request.module, "__file__", "<unknown>")
    raise AssertionError(
        f"Test module {module_name} ({module_file}) left "
        f"logging.disable set to level {current} (expected "
        f"logging.NOTSET = {logging.NOTSET}). This silently swallows "
        f"ERROR-log assertions in any test that runs afterwards "
        f"(see Task #402, Task #418).\n\n"
        f"Fix: wrap the disable() call in a try/finally that restores "
        f"the previous level, or move it into a fixture whose teardown "
        f"calls logging.disable(prev). Reference patterns:\n"
        f"  - tests/test_provider_dispatch.py "
        f"(try/finally around an import)\n"
        f"  - tests/test_assamese_rag_namespace.py "
        f"(fixture-based, for lazy imports)"
    )


_DEPS_KEY = "deps"
_METRICS_KEY = "metrics"
_SNAPSHOT_ATTRS = ("db", "is_mongo_available", "supa")
_MISSING = object()


# Task #469 follow-up: pin the synthetic ``deps`` stub at session start.
#
# The historical pattern was that any test file wanting the stub did
# ``install_deps_stub()`` at *its own* module-load time. That works in
# isolation, but ``install_deps_stub()`` is a no-op if ``sys.modules['deps']``
# already exists (force=False). So if any earlier test's import chain
# pulled in the *real* ``deps.py`` (e.g. via a TestClient that mounts a
# router whose import chain bottoms out in ``from deps import db``), the
# real module sticks around in ``sys.modules['deps']`` and every later
# ``install_deps_stub()`` call silently returns the real module instead.
#
# The autouse fixture below then snapshots whatever is current pre-test
# and restores it post-test, so the real-deps pollution propagates to
# every subsequent test — most visibly to ``test_no_sys_modules_pollution``,
# whose entire job is to assert the stub is intact.
#
# Fix: install the stub here, at conftest module load (which happens
# *before* any test file is imported), force=True so we win even if some
# eager import beat us to it. Then in the per-test fixture, if the stub
# went missing during the previous test, restore it. This guarantees
# every test starts with the same stub identity.
try:
    from tests._deps_stub import install_deps_stub  # noqa: E402
    _PINNED_DEPS_STUB = install_deps_stub(force=True)
except Exception:
    # If the stub can't be installed (e.g. import-time error in a
    # production module the stub relies on), fall back to the legacy
    # behaviour rather than crashing the whole suite.
    _PINNED_DEPS_STUB = None


def _snapshot_module(name: str):
    """Return ``(was_present, module_object)`` for ``sys.modules[name]``."""
    return (name in sys.modules, sys.modules.get(name))


def _restore_module(name: str, was_present: bool, module_obj) -> None:
    """Restore ``sys.modules[name]`` to its pre-test identity. If a
    test deleted, replaced, or installed the module, undo that — but
    only if it actually changed (the fast-path no-op keeps this hot
    fixture cheap)."""
    current = sys.modules.get(name)
    if current is module_obj:
        return
    if was_present:
        sys.modules[name] = module_obj
    else:
        sys.modules.pop(name, None)


@pytest.fixture(autouse=True)
def _reset_test_stub_state():
    """Snapshot/restore ``sys.modules['deps']`` and ``sys.modules['metrics']``
    around every test so one test's pollution cannot bleed into another.

    For ``deps`` (the synthetic stub):

    1. **Module identity in ``sys.modules``.** If a test deletes
       ``sys.modules['deps']``, swaps it for a different module
       object, or installs the stub when no module was present
       before, we restore the *exact same module object* (or absence
       thereof) that existed pre-test. This is the canonical fix for
       the cross-test contamination pattern Task #469 targets.

    2. **Module-attribute identity.** Even within the same module
       object, tests like ``test_bing_keyword_pipeline.py`` reassign
       ``deps.db = MagicMock()`` directly (without ``monkeypatch``).
       We snapshot the key stub attributes and restore them.

    3. **Mock call history.** Call counts / args accumulate across
       the session, making ``assert_called_once``-style assertions
       brittle. We call ``reset_mock(return_value=False,
       side_effect=False)`` on the (restored) ``db`` to clear history
       while preserving any ``side_effect`` / ``return_value``
       deliberately set by the next test.

    For ``metrics`` (Task #472): we only do the module-identity
    snapshot/restore. ``metrics`` is the real module, not a stub, and
    has no per-attribute mock state we need to clear — restoring
    identity is enough to guarantee that downstream
    ``from metrics import _metrics, _snapshot_metrics, ...``
    statements continue to find the same fully-initialized module
    they would have found in a fresh process.
    """
    # --- Pre-test stub re-pinning ---
    # If the previous test (or its import chain) replaced our pinned
    # stub with the real ``deps`` module — or with a different stub
    # instance — restore the canonical pinned stub *before* snapshotting
    # so this test starts from the same identity every other test
    # started from. Without this, real-deps pollution leaks across
    # tests via the snapshot/restore loop itself.
    if _PINNED_DEPS_STUB is not None and (
        sys.modules.get(_DEPS_KEY) is not _PINNED_DEPS_STUB
    ):
        sys.modules[_DEPS_KEY] = _PINNED_DEPS_STUB

    # --- Pre-test snapshot ---
    deps_present_pre, deps_module_pre = _snapshot_module(_DEPS_KEY)
    metrics_present_pre, metrics_module_pre = _snapshot_module(_METRICS_KEY)

    is_stub_pre = bool(getattr(deps_module_pre, "_is_syrabit_test_stub", False))
    # Snapshot: record (had_attr, value) per key so we can faithfully
    # restore even when the original value was ``None`` (e.g. ``supa``
    # on the synthetic stub starts as ``None``).
    attr_snapshot = None
    if is_stub_pre:
        attr_snapshot = {
            attr: (hasattr(deps_module_pre, attr),
                   getattr(deps_module_pre, attr, None))
            for attr in _SNAPSHOT_ATTRS
        }

    yield

    # --- Post-test restore ---

    # 1. Restore module identities in sys.modules.
    _restore_module(_DEPS_KEY, deps_present_pre, deps_module_pre)
    _restore_module(_METRICS_KEY, metrics_present_pre, metrics_module_pre)

    # 2. Only touch our deps stub. The real production module is off-limits.
    deps_now = sys.modules.get(_DEPS_KEY)
    if deps_now is None or not getattr(deps_now, "_is_syrabit_test_stub", False):
        return

    # 3. Restore attributes a test may have reassigned on the stub.
    # Restore by *key presence* not value, so a snapshot of ``None``
    # (e.g. ``deps.supa = None`` is the stub's default) is faithfully
    # re-applied and a test can't leak a ``deps.supa = MagicMock()``
    # mutation across the suite.
    if attr_snapshot is not None:
        for attr, (had_attr, original) in attr_snapshot.items():
            try:
                if had_attr:
                    if getattr(deps_now, attr, _MISSING) is not original:
                        setattr(deps_now, attr, original)
                else:
                    if hasattr(deps_now, attr):
                        delattr(deps_now, attr)
            except Exception:
                pass

    # 4. Clear accumulated call history on the (restored) db mock.
    db = getattr(deps_now, "db", None)
    if db is None:
        return
    try:
        db.reset_mock(return_value=False, side_effect=False)
    except Exception:
        # MagicMock subclasses occasionally raise on reset_mock when a
        # test has replaced an attribute with a non-Mock value
        # (e.g. db.foo = "literal"). Don't fail other tests on that —
        # the stub still satisfies the _MotorDbMock awaitability
        # contract for subsequent tests.
        pass
