"""Tests for the Azure-native AI features admin proxy (Task #338).

Guards four contracts:

1. ``FEATURES`` catalogue and ``azure_ai_metrics.FEATURE_KEYS`` stay
   in lockstep — drift was the most likely silent regression.
2. ``GET /admin/azure/ai/health`` renders all 10 rows even when
   Mongo is absent (degraded-but-not-blank invariant).
3. ``POST /admin/azure/ai/toggle`` writes to ``db.azure_ai_settings``
   via upsert and invalidates the runtime cache.
4. Toggle on an unknown feature returns 404, not 500.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


def _load(rel: str, name: str):
    path = Path(__file__).resolve().parent.parent / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeAdmin:
    is_admin = True


async def _fake_require_admin():
    return _FakeAdmin()


class _FakeCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.invalidated: list[str] = []

    async def find_one(self, query):
        return self.docs.get(query["key"])

    async def update_one(self, query, update, upsert=False):
        key = query["key"]
        new = update["$set"]
        self.docs[key] = new
        return mock.MagicMock(upserted_id="x")

    def find(self, *_a, **_kw):  # for metrics rollup + anomalies
        return _AsyncCursor([])


class _AsyncCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    def __aiter__(self):
        self._iter = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeDb:
    def __init__(self):
        self.azure_ai_settings = _FakeCollection()
        self.azure_ai_metrics_rollup = _FakeCollection()
        self.azure_ai_anomalies = _FakeCollection()


class AdminAzureAiRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Stub auth_deps before importing the route.
        auth_deps = mock.MagicMock()
        auth_deps.get_admin_user = _fake_require_admin
        self._patch = mock.patch.dict(sys.modules, {"auth_deps": auth_deps})
        self._patch.start()

        self.metrics = _load("azure_ai_metrics.py", "azure_ai_metrics")
        self.metrics.reset_for_tests()
        self.runtime = _load("azure_ai_runtime.py", "azure_ai_runtime")
        self.route = _load("routes/admin_azure_ai.py", "routes.admin_azure_ai_under_test")

        self.db = _FakeDb()

        async def _get_db():
            return self.db

        self._db_patch = mock.patch.object(self.route, "_get_db", _get_db)
        self._db_patch.start()

    def tearDown(self):
        self._db_patch.stop()
        self._patch.stop()

    def test_catalogue_matches_metrics(self):
        catalogue = sorted(f["key"] for f in self.route.FEATURES)
        self.assertEqual(catalogue, sorted(self.metrics.FEATURE_KEYS))

    def test_catalogue_has_ten_features(self):
        self.assertEqual(len(self.route.FEATURES), 10)
        for f in self.route.FEATURES:
            for required in ("displayName", "settingKey", "failureMode", "spendBudgetUsd"):
                self.assertIn(required, f)

    async def test_health_renders_with_empty_mongo(self):
        payload = await self.route.get_health(_admin=_FakeAdmin())
        self.assertEqual(len(payload["features"]), 10)
        self.assertTrue(all(f["enabled"] for f in payload["features"]))
        self.assertEqual(payload["compositeAlert"], "ok")
        self.assertEqual(payload["anomalies"], [])

    async def test_health_works_when_db_unavailable(self):
        async def _no_db():
            return None

        with mock.patch.object(self.route, "_get_db", _no_db):
            payload = await self.route.get_health(_admin=_FakeAdmin())
        self.assertEqual(len(payload["features"]), 10)
        self.assertTrue(all(f["enabled"] for f in payload["features"]))

    async def test_toggle_persists_to_collection(self):
        result = await self.route.toggle(
            self.route.ToggleRequest(feature="translator", enabled=False),
            _admin=_FakeAdmin(),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["settingKey"], "azure.translator.enabled")
        self.assertEqual(result["value"], False)
        stored = self.db.azure_ai_settings.docs["azure.translator.enabled"]
        self.assertEqual(stored["value"], False)
        self.assertIn("updatedAt", stored)

    async def test_toggle_string_valued_uses_settingValueOn(self):
        result = await self.route.toggle(
            self.route.ToggleRequest(feature="search", enabled=True),
            _admin=_FakeAdmin(),
        )
        self.assertEqual(result["value"], "azure-search")
        result_off = await self.route.toggle(
            self.route.ToggleRequest(feature="search", enabled=False),
            _admin=_FakeAdmin(),
        )
        self.assertEqual(result_off["value"], "pinecone")

    async def test_toggle_reflected_in_health(self):
        await self.route.toggle(
            self.route.ToggleRequest(feature="openai", enabled=False),
            _admin=_FakeAdmin(),
        )
        payload = await self.route.get_health(_admin=_FakeAdmin())
        openai_row = next(f for f in payload["features"] if f["key"] == "openai")
        self.assertFalse(openai_row["enabled"])

    async def test_toggle_invalidates_runtime_cache(self):
        # Seed the cache as if a chain just read it.
        self.runtime._seed_for_tests("azure.translator.enabled", True)
        self.assertTrue(self.runtime.is_enabled_sync("translator"))
        await self.route.toggle(
            self.route.ToggleRequest(feature="translator", enabled=False),
            _admin=_FakeAdmin(),
        )
        # Cache should be flushed by the toggle handler.
        self.assertNotIn("azure.translator.enabled", self.runtime._cache)
        # The next async call would re-read from deps.db; we point
        # deps.db at our fake to confirm the value lands as False.
        fake_deps = mock.MagicMock()
        fake_deps.db = self.db
        with mock.patch.dict(sys.modules, {"deps": fake_deps}):
            is_on = await self.runtime.is_enabled("translator")
        self.assertFalse(is_on)

    async def test_toggle_unknown_feature_404(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as cm:
            await self.route.toggle(
                self.route.ToggleRequest(feature="not-a-feature", enabled=True),
                _admin=_FakeAdmin(),
            )
        self.assertEqual(cm.exception.status_code, 404)

    async def test_toggle_503_when_db_down(self):
        from fastapi import HTTPException

        async def _no_db():
            return None

        with mock.patch.object(self.route, "_get_db", _no_db):
            with self.assertRaises(HTTPException) as cm:
                await self.route.toggle(
                    self.route.ToggleRequest(feature="openai", enabled=False),
                    _admin=_FakeAdmin(),
                )
        self.assertEqual(cm.exception.status_code, 503)


class MetricsCounterTests(unittest.TestCase):
    def setUp(self):
        self.metrics = _load("azure_ai_metrics.py", "azure_ai_metrics_under_test")
        self.metrics.reset_for_tests()

    def test_throttle_window_counts_recent(self):
        self.metrics.record_throttle("openai")
        self.metrics.record_throttle("openai")
        snap = self.metrics.SNAPSHOT["openai"]
        self.assertEqual(snap["throttle15m"], 2)

    def test_latency_percentiles(self):
        for value in (50, 100, 200, 500, 800):
            self.metrics.record_latency("speech", value)
        snap = self.metrics.SNAPSHOT["speech"]
        self.assertIsNotNone(snap["latencyP50Ms"])
        self.assertIsNotNone(snap["latencyP95Ms"])
        self.assertGreaterEqual(snap["latencyP95Ms"], snap["latencyP50Ms"])

    def test_unknown_feature_does_not_crash(self):
        self.metrics.record_throttle("not-a-feature")
        self.assertEqual(self.metrics.SNAPSHOT["openai"]["throttle15m"], 0)


if __name__ == "__main__":
    unittest.main()
