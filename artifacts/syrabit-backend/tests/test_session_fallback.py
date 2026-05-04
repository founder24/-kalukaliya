"""Task #362 §3 — Per-session sticky fallback tests."""
from __future__ import annotations

import os
import sys
import time

# Force the test config before importing the module under test so the
# trip threshold is a reasonable test value regardless of environment.
os.environ.setdefault("SESSION_FALLBACK_K", "3")
os.environ.setdefault("SESSION_FALLBACK_TTFB_MS", "2400")
os.environ.setdefault("SESSION_FALLBACK_PROVIDER", "azure_openai")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeRedis:
    """Minimal in-memory Upstash-shaped Redis used by these tests.

    Implements only the surface session_fallback.py uses: get/set/rpush/
    ltrim/expire/lrange/sadd/smembers/scan.
    """

    def __init__(self):
        self.kv: dict = {}
        self.lists: dict = {}
        self.sets: dict = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, ex=None):
        self.kv[k] = v
        return "OK"

    def rpush(self, k, v):
        self.lists.setdefault(k, []).append(v)
        return len(self.lists[k])

    def ltrim(self, k, start, end):
        lst = self.lists.get(k, [])
        if end == -1:
            end = len(lst) - 1
        if start < 0:
            start = max(0, len(lst) + start)
        self.lists[k] = lst[start : end + 1]
        return "OK"

    def lrange(self, k, start, end):
        lst = self.lists.get(k, [])
        if end == -1:
            end = len(lst) - 1
        return lst[start : end + 1]

    def expire(self, k, ttl):
        return 1

    def sadd(self, k, *members):
        self.sets.setdefault(k, set()).update(members)
        return len(members)

    def smembers(self, k):
        return list(self.sets.get(k, set()))

    def scan(self, cursor, match=None, count=200):
        # Only need to match "session:fallback:*" — return all matching keys.
        prefix = (match or "").rstrip("*")
        keys = [k for k in self.kv.keys() if k.startswith(prefix)]
        return [0, keys]


def _install_fake_redis(monkeypatch):
    fake = _FakeRedis()
    import deps as _deps_mod
    monkeypatch.setattr(_deps_mod, "redis_client", fake, raising=False)
    return fake


def test_no_swap_when_session_id_empty(monkeypatch):
    _install_fake_redis(monkeypatch)
    import session_fallback as sf
    sf.record_turn_ttfb("", 9999.0)
    assert sf.get_session_swap("") is None


def test_no_swap_when_redis_unavailable(monkeypatch):
    import deps as _deps_mod
    monkeypatch.setattr(_deps_mod, "redis_client", None, raising=False)
    import session_fallback as sf
    sf.record_turn_ttfb("sess-X", 9999.0)
    assert sf.get_session_swap("sess-X") is None


def test_swap_does_not_trip_below_K_slow_turns(monkeypatch):
    _install_fake_redis(monkeypatch)
    import session_fallback as sf
    sid = "sess-A"
    # Two slow turns (K=3) → should not trip.
    sf.record_turn_ttfb(sid, 5000)
    sf.record_turn_ttfb(sid, 5000)
    assert sf.get_session_swap(sid) is None


def test_swap_trips_after_K_consecutive_slow_turns(monkeypatch):
    _install_fake_redis(monkeypatch)
    import session_fallback as sf
    sid = "sess-B"
    for _ in range(sf.SESSION_FALLBACK_K):
        sf.record_turn_ttfb(sid, sf.SESSION_FALLBACK_TTFB_MS + 100)
    assert sf.get_session_swap(sid) == sf.SESSION_FALLBACK_PROVIDER


def test_fast_turn_resets_consecutive_counter(monkeypatch):
    _install_fake_redis(monkeypatch)
    import session_fallback as sf
    sid = "sess-C"
    # Two slow, then one fast — list trims to last K, so the fast turn
    # forces the all-slow predicate to false.
    sf.record_turn_ttfb(sid, sf.SESSION_FALLBACK_TTFB_MS + 100)
    sf.record_turn_ttfb(sid, sf.SESSION_FALLBACK_TTFB_MS + 100)
    sf.record_turn_ttfb(sid, 100)  # fast turn
    assert sf.get_session_swap(sid) is None
    # Now another two slow turns — that's [100, slow, slow] → still not all slow.
    sf.record_turn_ttfb(sid, sf.SESSION_FALLBACK_TTFB_MS + 100)
    sf.record_turn_ttfb(sid, sf.SESSION_FALLBACK_TTFB_MS + 100)
    assert sf.get_session_swap(sid) is None
    # One more slow turn → trim drops the fast one → all K slow → trip.
    sf.record_turn_ttfb(sid, sf.SESSION_FALLBACK_TTFB_MS + 100)
    assert sf.get_session_swap(sid) == sf.SESSION_FALLBACK_PROVIDER


def test_swap_is_sticky_no_auto_revert(monkeypatch):
    _install_fake_redis(monkeypatch)
    import session_fallback as sf
    sid = "sess-D"
    for _ in range(sf.SESSION_FALLBACK_K):
        sf.record_turn_ttfb(sid, sf.SESSION_FALLBACK_TTFB_MS + 100)
    assert sf.get_session_swap(sid) == sf.SESSION_FALLBACK_PROVIDER
    # Subsequent fast turns must not clear the swap (sticky-by-design).
    for _ in range(5):
        sf.record_turn_ttfb(sid, 100)
    assert sf.get_session_swap(sid) == sf.SESSION_FALLBACK_PROVIDER


def test_disabled_kill_switch_blocks_swap(monkeypatch):
    fake = _install_fake_redis(monkeypatch)
    fake.kv["session:fallback:disabled"] = "1"
    import session_fallback as sf
    sid = "sess-E"
    for _ in range(sf.SESSION_FALLBACK_K):
        sf.record_turn_ttfb(sid, sf.SESSION_FALLBACK_TTFB_MS + 100)
    assert sf.get_session_swap(sid) is None


def test_get_current_session_id_outside_chat_turn():
    """The contextvar export must work safely when no chat_turn is open."""
    from chat_turn_context import get_current_session_id
    assert get_current_session_id() == ""


def test_get_current_session_id_inside_chat_turn():
    from chat_turn_context import chat_turn, get_current_session_id
    with chat_turn(session_id="sess-CTX-123", user_id="u-1"):
        assert get_current_session_id() == "sess-CTX-123"
    # Outside the context manager, it must reset.
    assert get_current_session_id() == ""
