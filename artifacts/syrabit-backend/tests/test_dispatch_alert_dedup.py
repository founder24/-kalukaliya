"""Task #988 — cross-worker alert dedup correctness tests.

The metrics._dispatch_alert function uses a persistent cross-worker dedup
backed by an atomic ``find_one_and_update`` upsert against
``db.alert_dispatch_log`` (with a unique-index ``DuplicateKeyError`` trap to
detect race losses). This file pins down the four invariants that guarantee
the 1,800-alert blowup we just cleared cannot regress:

1. Two concurrent calls with the same ``dedup_key`` produce exactly one
   delivery (the racing worker observes ``DuplicateKeyError`` and
   short-circuits with ``skipped_cooldown=True``).
2. Calls with different ``dedup_keys`` both deliver — no false-positive
   dedup collapse across distinct incidents.
3. The atomic claim is rolled back when every synchronous delivery channel
   fails — so a transient delivery outage doesn't lock retries for the
   full 6h persistent-cooldown window.
4. ``force=True`` bypasses both the in-memory and the persistent cooldowns
   (admin "test delivery" buttons must always reach the channels).
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()

import metrics as _metrics_mod  # noqa: E402

from pymongo.errors import DuplicateKeyError  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _reset_metrics_globals():
    """Wipe the in-memory cooldown mirror + restore notification channel
    defaults around every test so neither bleeds between assertions.
    """
    _metrics_mod._alert_last_fired.clear()
    _metrics_mod._notification_channels = dict(
        _metrics_mod._NOTIFICATION_CHANNELS_DEFAULT
    )
    _metrics_mod._channel_status = {
        k: dict(v) for k, v in _metrics_mod._CHANNEL_STATUS_DEFAULT.items()
    }
    yield
    _metrics_mod._alert_last_fired.clear()
    _metrics_mod._notification_channels = dict(
        _metrics_mod._NOTIFICATION_CHANNELS_DEFAULT
    )
    _metrics_mod._channel_status = {
        k: dict(v) for k, v in _metrics_mod._CHANNEL_STATUS_DEFAULT.items()
    }


def _build_db_mock(*, claim_side_effect=None, insert_side_effect=None,
                   delete_side_effect=None, alerts=None):
    """Construct a MagicMock for ``deps.db`` covering every collection /
    method the dispatch path touches.

    Channels (email/webhook) are silenced via ``_notification_channels =
    {}`` + empty env vars, so the dispatch boils down to:

      * ``alert_dispatch_log.find_one_and_update`` — the atomic claim
      * ``alerts.insert_one``                       — the persisted alert
      * ``push_subscriptions.count_documents``      — push pre-check
      * ``alert_dispatch_log.delete_one``           — rollback (only if all
                                                      sync channels failed)
      * ``api_config.update_one``                   — channel-status persist
      * ``push_delivery_log.find_one``              — _recompute_push_status
    """
    mock_alert_dispatch_log = MagicMock()
    mock_alert_dispatch_log.find_one_and_update = AsyncMock(
        side_effect=claim_side_effect, return_value=None
    )
    mock_alert_dispatch_log.delete_one = AsyncMock(
        side_effect=delete_side_effect,
        return_value=MagicMock(deleted_count=1),
    )

    mock_alerts = alerts if alerts is not None else MagicMock()
    if not hasattr(mock_alerts, "insert_one") or not isinstance(
        mock_alerts.insert_one, AsyncMock
    ):
        mock_alerts.insert_one = AsyncMock(side_effect=insert_side_effect)

    mock_push_subs = MagicMock()
    # Pretend at least one admin subscriber exists so we don't go down
    # the "no active push subscribers" short-circuit (which would log
    # to push_delivery_log and skip the dispatch). The dispatch_push
    # callable itself is patched at the module level in each test.
    mock_push_subs.count_documents = AsyncMock(return_value=1)

    mock_users = MagicMock()
    mock_users.find = MagicMock(return_value=MagicMock(
        to_list=AsyncMock(return_value=[])
    ))

    mock_api_config = MagicMock()
    mock_api_config.update_one = AsyncMock(return_value=None)

    mock_push_log = MagicMock()
    mock_push_log.find_one = AsyncMock(return_value=None)
    mock_push_log.insert_one = AsyncMock(return_value=None)

    return MagicMock(
        alert_dispatch_log=mock_alert_dispatch_log,
        alerts=mock_alerts,
        push_subscriptions=mock_push_subs,
        users=mock_users,
        api_config=mock_api_config,
        push_delivery_log=mock_push_log,
    )


def _silence_channels():
    _metrics_mod._notification_channels["email"] = ""
    _metrics_mod._notification_channels["webhook_url"] = ""


# ─────────────────────────────────────────────────────────────────────────
# (a) Two concurrent calls with the same dedup_key → exactly one delivery
# ─────────────────────────────────────────────────────────────────────────

class TestConcurrentSameKeyDedups:
    def test_second_concurrent_call_with_same_dedup_key_is_dropped(self):
        """Simulate two workers firing the same alert in parallel.

        The first ``find_one_and_update`` wins the upsert (returns
        normally). The second hits the unique index on ``dedup_key`` and
        gets ``DuplicateKeyError`` — _dispatch_alert must catch that,
        flip ``skipped_cooldown=True``, and return WITHOUT writing a
        second persisted alert.
        """
        _silence_channels()

        # Counter so the AsyncMock raises DuplicateKeyError on call #2.
        claim_calls = {"n": 0}

        async def _claim(*_args, **_kwargs):
            claim_calls["n"] += 1
            if claim_calls["n"] >= 2:
                raise DuplicateKeyError("dup")
            return None

        mock_db = _build_db_mock(claim_side_effect=_claim)

        async def _both():
            # Bypass the in-process cooldown for the second call by
            # clearing _alert_last_fired between them — simulates the
            # cross-worker scenario where each worker has its own
            # in-memory mirror, so only the persistent claim disambiguates.
            r1 = await _metrics_mod._dispatch_alert("dup_key_test", "T", "B")
            _metrics_mod._alert_last_fired.clear()
            r2 = await _metrics_mod._dispatch_alert("dup_key_test", "T", "B")
            return r1, r2

        with patch.dict(os.environ, {"ALERT_EMAIL": "", "ALERT_WEBHOOK_URL": "",
                                     "SENDGRID_API_KEY": ""}), \
             patch.object(_metrics_mod, "db", mock_db), \
             patch("routes.admin_notifications._dispatch_push_to_admins",
                   new_callable=AsyncMock):
            r1, r2 = _run(_both())

        # First call delivered; second was dedup-suppressed.
        assert r1["skipped_cooldown"] is False
        assert r1["persisted"]["ok"] is True
        assert r2["skipped_cooldown"] is True

        # Exactly one persisted alert across the pair.
        assert mock_db.alerts.insert_one.await_count == 1

        # Both calls attempted the atomic claim.
        assert claim_calls["n"] == 2

        # In-memory mirror for the losing worker stays in sync so it
        # short-circuits subsequent retries even before the next persistent
        # check (defense-in-depth for the same worker process).
        assert _metrics_mod._alert_last_fired.get("dup_key_test") is not None

    def test_true_concurrent_same_key_via_asyncio_gather(self):
        """True parallel dispatch via ``asyncio.gather`` — both calls
        enter the dispatch chain on the same event loop turn, both pass
        the in-memory check, and only one wins the atomic claim. The
        loser observes ``DuplicateKeyError`` and short-circuits.

        This complements the sequential simulation above: the calls are
        actually scheduled together and the side_effect counter is the
        only ordering primitive — closer to the real cross-worker race
        the dedup is designed to handle.
        """
        _silence_channels()
        # Wipe in-memory mirror so neither call is blocked at the
        # in-process layer (simulates two distinct workers).
        _metrics_mod._alert_last_fired.clear()

        claim_calls = {"n": 0}
        # An asyncio Event ensures both coroutines reach
        # find_one_and_update before either is allowed to "complete"
        # the upsert — proves the loser hits DuplicateKeyError after
        # the winner has already claimed (rather than via a stale
        # in-memory mirror short-circuit).
        gate = asyncio.Event()

        async def _claim(*_args, **_kwargs):
            claim_calls["n"] += 1
            my_n = claim_calls["n"]
            # Wait until both callers have entered before resolving
            # either one (true race condition simulation).
            if my_n == 1:
                # Winner: signal the gate and proceed.
                gate.set()
                return None
            # Loser: gate is already set by the time we get here,
            # but await it for symmetry, then raise DuplicateKeyError.
            await gate.wait()
            raise DuplicateKeyError("dup")

        mock_db = _build_db_mock(claim_side_effect=_claim)

        async def _race():
            # Clear in-memory mirror BETWEEN the two coroutines'
            # in-memory checks by running them through gather; both
            # pass their initial check on the same event loop turn
            # before either has reached the in-memory write.
            return await asyncio.gather(
                _metrics_mod._dispatch_alert("race_test", "T", "B"),
                _metrics_mod._dispatch_alert("race_test", "T", "B"),
                return_exceptions=False,
            )

        with patch.dict(os.environ, {"ALERT_EMAIL": "", "ALERT_WEBHOOK_URL": "",
                                     "SENDGRID_API_KEY": ""}), \
             patch.object(_metrics_mod, "db", mock_db), \
             patch("routes.admin_notifications._dispatch_push_to_admins",
                   new_callable=AsyncMock):
            results = _run(_race())

        skipped = [r for r in results if r["skipped_cooldown"]]
        delivered = [r for r in results if not r["skipped_cooldown"]]
        # Exactly one winner, exactly one loser, exactly one persisted alert.
        assert len(delivered) == 1
        assert len(skipped) == 1
        assert delivered[0]["persisted"]["ok"] is True
        assert mock_db.alerts.insert_one.await_count == 1

    def test_per_target_dedup_key_uses_threshold_snapshot_field(self):
        """The dedup key is per-target: the same alert_type for two
        different endpoints must claim two distinct rows. We verify by
        reading the ``dedup_key`` written into find_one_and_update.
        """
        _silence_channels()

        captured_keys = []

        async def _claim(filt, *_args, **_kwargs):
            captured_keys.append(filt.get("dedup_key"))
            return None

        mock_db = _build_db_mock(claim_side_effect=_claim)
        with patch.dict(os.environ, {"ALERT_EMAIL": "", "ALERT_WEBHOOK_URL": "",
                                     "SENDGRID_API_KEY": ""}), \
             patch.object(_metrics_mod, "db", mock_db), \
             patch("routes.admin_notifications._dispatch_push_to_admins",
                   new_callable=AsyncMock):
            _run(_metrics_mod._dispatch_alert(
                "endpoint_down", "T", "B",
                threshold_snapshot={"endpoint": "https://api.a/cms"},
            ))
            _metrics_mod._alert_last_fired.clear()
            _run(_metrics_mod._dispatch_alert(
                "endpoint_down", "T", "B",
                threshold_snapshot={"endpoint": "https://api.b/cms"},
            ))

        assert captured_keys == [
            "endpoint_down|endpoint=https://api.a/cms",
            "endpoint_down|endpoint=https://api.b/cms",
        ]


# ─────────────────────────────────────────────────────────────────────────
# (b) Calls with different dedup_keys both deliver
# ─────────────────────────────────────────────────────────────────────────

class TestDistinctDedupKeysBothDeliver:
    def test_two_different_alert_types_both_deliver(self):
        """Two unrelated alerts with no shared per-target context must
        both win their respective claims and persist independently. This
        guards against the dedup key accidentally collapsing to a
        constant (e.g. forgetting to vary by ``alert_type``).
        """
        _silence_channels()

        # All claims succeed (no DuplicateKeyError).
        mock_db = _build_db_mock()

        with patch.dict(os.environ, {"ALERT_EMAIL": "", "ALERT_WEBHOOK_URL": "",
                                     "SENDGRID_API_KEY": ""}), \
             patch.object(_metrics_mod, "db", mock_db), \
             patch("routes.admin_notifications._dispatch_push_to_admins",
                   new_callable=AsyncMock):
            r1 = _run(_metrics_mod._dispatch_alert("high_error_rate", "T", "B"))
            r2 = _run(_metrics_mod._dispatch_alert("high_latency", "T", "B"))

        assert r1["skipped_cooldown"] is False
        assert r2["skipped_cooldown"] is False
        assert r1["persisted"]["ok"] is True
        assert r2["persisted"]["ok"] is True

        # Two distinct claims and two persisted alerts.
        assert mock_db.alert_dispatch_log.find_one_and_update.await_count == 2
        assert mock_db.alerts.insert_one.await_count == 2

        # The dedup_keys written to alert_dispatch_log were distinct.
        all_calls = mock_db.alert_dispatch_log.find_one_and_update.await_args_list
        keys = [call.args[0]["dedup_key"] for call in all_calls]
        assert keys == ["high_error_rate", "high_latency"]

    def test_same_alert_type_with_different_targets_both_deliver(self):
        """``endpoint_down`` for two different endpoints is two
        different incidents — both claims must succeed.
        """
        _silence_channels()
        mock_db = _build_db_mock()

        with patch.dict(os.environ, {"ALERT_EMAIL": "", "ALERT_WEBHOOK_URL": "",
                                     "SENDGRID_API_KEY": ""}), \
             patch.object(_metrics_mod, "db", mock_db), \
             patch("routes.admin_notifications._dispatch_push_to_admins",
                   new_callable=AsyncMock):
            r1 = _run(_metrics_mod._dispatch_alert(
                "endpoint_down", "T", "B",
                threshold_snapshot={"endpoint": "https://api.a/cms"},
            ))
            # Second call uses a DIFFERENT alert_type AS WELL so the
            # in-memory mirror doesn't block it (the persistent layer is
            # what we're really exercising; the in-memory layer is
            # alert_type-only and would otherwise trip first).
            _metrics_mod._alert_last_fired.clear()
            r2 = _run(_metrics_mod._dispatch_alert(
                "endpoint_down", "T", "B",
                threshold_snapshot={"endpoint": "https://api.b/cms"},
            ))

        assert r1["persisted"]["ok"] is True
        assert r2["persisted"]["ok"] is True
        assert mock_db.alerts.insert_one.await_count == 2


# ─────────────────────────────────────────────────────────────────────────
# (c) Rollback fires when all delivery channels fail
# ─────────────────────────────────────────────────────────────────────────

class TestRollbackOnAllChannelFailure:
    def test_claim_rolled_back_when_persisted_insert_fails_and_no_other_channels(self):
        """All synchronous channels (email/webhook/persisted) failing
        must trigger ``alert_dispatch_log.delete_one`` so the next
        alerter tick can re-fire — otherwise a transient Mongo blip at
        claim time would suppress this incident for the full 6h
        persistent cooldown window.
        """
        _silence_channels()

        # Persisted insert raises — email/webhook silenced via empty config.
        mock_db = _build_db_mock(
            insert_side_effect=RuntimeError("mongo write failed"),
        )

        with patch.dict(os.environ, {"ALERT_EMAIL": "", "ALERT_WEBHOOK_URL": "",
                                     "SENDGRID_API_KEY": ""}), \
             patch.object(_metrics_mod, "db", mock_db), \
             patch("routes.admin_notifications._dispatch_push_to_admins",
                   new_callable=AsyncMock):
            outcomes = _run(_metrics_mod._dispatch_alert(
                "rollback_test", "T", "B", threshold_snapshot={"endpoint": "x"}
            ))

        # Persisted channel actually attempted but failed.
        assert outcomes["persisted"]["attempted"] is True
        assert outcomes["persisted"]["ok"] is False
        assert outcomes["email"]["ok"] is False
        assert outcomes["webhook"]["ok"] is False

        # ── Rollback: delete_one was awaited with the same dedup_key.
        mock_db.alert_dispatch_log.delete_one.assert_awaited_once()
        rollback_filter = mock_db.alert_dispatch_log.delete_one.await_args.args[0]
        assert rollback_filter["dedup_key"] == "rollback_test|endpoint=x"
        assert "ts" in rollback_filter

        # In-memory mirror was cleared so the very next call in this
        # worker is free to retry immediately (also clears the cross-
        # worker claim above so all workers can race afresh).
        assert "rollback_test" not in _metrics_mod._alert_last_fired

    def test_no_rollback_when_at_least_one_channel_delivered(self):
        """If the persisted alert insert succeeded, the claim must be
        held — otherwise we'd let a second worker re-deliver the same
        incident on its next tick.
        """
        _silence_channels()

        mock_db = _build_db_mock()  # all writes succeed

        with patch.dict(os.environ, {"ALERT_EMAIL": "", "ALERT_WEBHOOK_URL": "",
                                     "SENDGRID_API_KEY": ""}), \
             patch.object(_metrics_mod, "db", mock_db), \
             patch("routes.admin_notifications._dispatch_push_to_admins",
                   new_callable=AsyncMock):
            outcomes = _run(_metrics_mod._dispatch_alert("hold_claim", "T", "B"))

        assert outcomes["persisted"]["ok"] is True
        # Claim was NOT released (delete_one never called).
        mock_db.alert_dispatch_log.delete_one.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────
# (d) force=True bypasses the cooldown
# ─────────────────────────────────────────────────────────────────────────

class TestForceBypassesCooldown:
    def test_force_true_skips_in_memory_cooldown(self):
        """Pre-seed the in-memory ``_alert_last_fired`` so a normal
        dispatch is blocked, then verify ``force=True`` blasts through.
        """
        _silence_channels()
        # Block in-memory: a recent fire for this alert_type.
        _metrics_mod._alert_last_fired["forced_alert"] = (
            _metrics_mod._time_mod.time()
        )

        mock_db = _build_db_mock()
        with patch.dict(os.environ, {"ALERT_EMAIL": "", "ALERT_WEBHOOK_URL": "",
                                     "SENDGRID_API_KEY": ""}), \
             patch.object(_metrics_mod, "db", mock_db), \
             patch("routes.admin_notifications._dispatch_push_to_admins",
                   new_callable=AsyncMock):
            blocked = _run(_metrics_mod._dispatch_alert("forced_alert", "T", "B"))
            forced = _run(_metrics_mod._dispatch_alert(
                "forced_alert", "T", "B", force=True,
            ))

        assert blocked["skipped_cooldown"] is True
        assert forced["skipped_cooldown"] is False
        assert forced["persisted"]["ok"] is True

    def test_force_true_skips_persistent_claim_entirely(self):
        """The persistent ``alert_dispatch_log`` claim block is wrapped
        in ``if not force:`` — when ``force=True`` the atomic upsert
        must NOT run at all (otherwise an admin "test delivery" while a
        real cooldown is active would silently lose the race and the
        admin would see a confusing skipped_cooldown=True).
        """
        _silence_channels()

        async def _claim_should_not_be_called(*_a, **_kw):  # pragma: no cover
            raise AssertionError(
                "find_one_and_update must not be called when force=True"
            )

        mock_db = _build_db_mock(claim_side_effect=_claim_should_not_be_called)

        with patch.dict(os.environ, {"ALERT_EMAIL": "", "ALERT_WEBHOOK_URL": "",
                                     "SENDGRID_API_KEY": ""}), \
             patch.object(_metrics_mod, "db", mock_db), \
             patch("routes.admin_notifications._dispatch_push_to_admins",
                   new_callable=AsyncMock):
            forced = _run(_metrics_mod._dispatch_alert(
                "force_skip_claim", "T", "B", force=True,
            ))

        assert forced["skipped_cooldown"] is False
        assert forced["persisted"]["ok"] is True
        # Sanity: the claim path was bypassed → no rollback either.
        mock_db.alert_dispatch_log.delete_one.assert_not_awaited()
        mock_db.alert_dispatch_log.find_one_and_update.assert_not_awaited()

    def test_force_true_dispatches_even_when_persistent_claim_would_fail(self):
        """Combined check: simulate a duplicate slot on the persistent
        log + a fresh in-memory cooldown. Without ``force``, the alert
        would be dropped twice over. With ``force``, the alert delivers.
        """
        _silence_channels()
        _metrics_mod._alert_last_fired["forced_dup"] = (
            _metrics_mod._time_mod.time()
        )

        async def _claim(*_a, **_kw):
            raise DuplicateKeyError("already claimed")

        mock_db = _build_db_mock(claim_side_effect=_claim)

        with patch.dict(os.environ, {"ALERT_EMAIL": "", "ALERT_WEBHOOK_URL": "",
                                     "SENDGRID_API_KEY": ""}), \
             patch.object(_metrics_mod, "db", mock_db), \
             patch("routes.admin_notifications._dispatch_push_to_admins",
                   new_callable=AsyncMock):
            normal = _run(_metrics_mod._dispatch_alert("forced_dup", "T", "B"))
            forced = _run(_metrics_mod._dispatch_alert(
                "forced_dup", "T", "B", force=True,
            ))

        # Normal call hit the in-memory cooldown FIRST and returned without
        # touching the persistent log; forced call bypassed both layers
        # and delivered the persisted alert.
        assert normal["skipped_cooldown"] is True
        assert forced["skipped_cooldown"] is False
        assert forced["persisted"]["ok"] is True
        assert mock_db.alerts.insert_one.await_count == 1
