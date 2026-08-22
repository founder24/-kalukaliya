"""Contract coverage for the admin dashboard payloads.

These tests exercise the route handlers with representative MongoDB results.
They deliberately assert the containers the dashboard dereferences so an API
envelope change fails in backend CI before the UI can quietly show no-data
states to operators.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import admin_analytics, admin_cron, admin_dashboard
from app.config import settings


class _AggregateCursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, length):
        return self._rows[:length]


class _AsyncRows:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        async def _iterate():
            for row in self._rows:
                yield row

        return _iterate()


def _mongo_for(db):
    client = MagicMock()
    client.__getitem__.return_value = db
    return MagicMock(return_value=client)


def _dashboard_db(*, populated):
    db = MagicMock()
    db.users.count_documents = AsyncMock(
        side_effect=[321, 72, 14, 89, 232] if populated else [0, 0, 0, 0, 0]
    )
    db.chats.aggregate = AsyncMock(
        side_effect=[
            _AggregateCursor([{"total": 876}]) if populated else _AggregateCursor([]),
            _AggregateCursor([{"total": 54}]) if populated else _AggregateCursor([]),
        ]
    )
    db.transactions.aggregate = AsyncMock(
        side_effect=[
            _AggregateCursor([{"total_paise": 1234500}]) if populated else _AggregateCursor([]),
            _AggregateCursor([{"total_paise": 345600}]) if populated else _AggregateCursor([]),
        ]
    )
    db.chat_feedback.count_documents = AsyncMock(
        side_effect=[17, 14] if populated else [0, 0]
    )
    # The optional token-spend aggregate is intentionally allowed to be absent.
    # The handler must still keep token_spend as an object in that case.
    db.ai_usage_logs.aggregate = MagicMock(side_effect=RuntimeError("not seeded"))
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("populated", "expected_users", "expected_messages", "expected_revenue"),
    [(False, 0, 0, 0), (True, 321, 876, 12345.0)],
)
async def test_dashboard_preserves_required_object_containers(
    monkeypatch, populated, expected_users, expected_messages, expected_revenue
):
    """The overview endpoint keeps its nested dashboard containers for empty and live data."""
    monkeypatch.setattr(
        admin_dashboard,
        "get_mongo_client",
        _mongo_for(_dashboard_db(populated=populated)),
    )

    payload = await admin_dashboard.admin_dashboard(request=MagicMock())

    assert payload["total_users"] == expected_users
    assert payload["total_messages"] == expected_messages
    assert payload["revenue_total"] == expected_revenue
    assert payload["system_health"] == "ok"
    for key in ("feedback", "vector_stats", "token_spend", "top_queries", "chat_fallbacks"):
        assert isinstance(payload[key], dict), f"{key} must remain an object"
    assert isinstance(payload["feedback"]["positive_rate"], (int, float))


def _metrics_db(*, populated):
    db = MagicMock()
    db.users.count_documents = AsyncMock(side_effect=[89, 232] if populated else [0, 0])
    now = datetime.now(timezone.utc)
    db.payments.find = MagicMock(
        return_value=_AsyncRows(
            [{"amount": 12345, "created_at": now}] if populated else []
        )
    )
    db.chapters.count_documents = AsyncMock(side_effect=[45, 38] if populated else [0, 0])
    db.request_logs.count_documents = AsyncMock(return_value=120 if populated else 0)
    db.list_collection_names = AsyncMock(return_value=["request_logs"] if populated else [])
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("populated", "expected_revenue", "expected_paid", "expected_pages"),
    [(False, 0, 0, 0), (True, 12345, 89, 45)],
)
async def test_dashboard_metrics_preserves_required_object_containers(
    monkeypatch, populated, expected_revenue, expected_paid, expected_pages
):
    """The heavy dashboard payload has stable objects whether Mongo is empty or populated."""
    monkeypatch.setattr(
        admin_dashboard,
        "get_mongo_client",
        _mongo_for(_metrics_db(populated=populated)),
    )

    payload = await admin_dashboard.admin_dashboard_metrics()

    assert payload["revenue"]["total_inr"] == expected_revenue
    assert payload["users"]["paid"] == expected_paid
    assert payload["seo"]["published_pages"] == expected_pages
    for key in ("revenue", "users", "seo", "bot_render", "dependencies", "_meta"):
        assert isinstance(payload[key], dict), f"{key} must remain an object"
    assert isinstance(payload["bot_render"]["by_page_type"], dict)
    assert isinstance(payload["_meta"]["heavy_cached_at"], (int, float))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"query": "photosynthesis", "count": 12, "last_seen": datetime(2026, 8, 21, tzinfo=timezone.utc)}],
    ],
)
async def test_top_queries_preserves_array_container_for_empty_and_populated_results(
    monkeypatch, rows
):
    """The query widget must always receive a list, not a missing/null collection."""
    db = MagicMock()
    db.chats.aggregate = AsyncMock(return_value=_AggregateCursor(rows))
    monkeypatch.setattr(admin_analytics, "get_mongo_client", _mongo_for(db))

    payload = await admin_analytics.admin_top_queries(days=7, limit=20)

    assert isinstance(payload["top_queries"], list)
    assert payload["total_returned"] == len(rows)
    if rows:
        assert payload["top_queries"][0]["query"] == "photosynthesis"
        assert payload["top_queries"][0]["last_seen"].endswith("+00:00")


class _CfGraphqlResponse:
    def __init__(self, payload=None):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        if self.payload is not None:
            return self.payload
        return {
            "data": {
                "viewer": {
                    "zones": [{
                        "httpRequests1dGroups": [
                            {
                                "dimensions": {"date": "2026-08-20"},
                                "sum": {"requests": 800, "pageViews": 500, "threats": 2, "bytes": 1024},
                                "uniq": {"uniques": 625},
                            },
                            {
                                "dimensions": {"date": "2026-08-21"},
                                "sum": {"requests": 1200, "pageViews": 876, "threats": 1, "bytes": 2048},
                                "uniq": {"uniques": 940},
                            },
                        ],
                        "httpRequests1hGroups": [
                            {
                                "dimensions": {"datetime": "2026-08-21T10:00:00Z"},
                                "sum": {"requests": 80, "pageViews": 50, "threats": 0, "bytes": 1024},
                                "uniq": {"uniques": 47},
                            },
                            {
                                "dimensions": {"datetime": "2026-08-21T11:00:00Z"},
                                "sum": {"requests": 120, "pageViews": 76, "threats": 1, "bytes": 2048},
                                "uniq": {"uniques": 62},
                            },
                        ],
                        "uniqueVisitors": [{"uniq": {"uniques": 1432}}],
                    }],
                },
            },
        }


class _CfGraphqlClient:
    def __init__(self, requests=None, payload=None):
        self.requests = requests if requests is not None else []
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        self.requests.append(kwargs["json"])
        return _CfGraphqlResponse(self.payload)


@pytest.mark.asyncio
async def test_cf_overview_keeps_widget_containers_when_unavailable(monkeypatch):
    """Missing Cloudflare credentials still produce object/list containers."""
    monkeypatch.setattr(settings, "CF_ANALYTICS_TOKEN", None, raising=False)
    monkeypatch.setattr(settings, "CF_ZONE_ID", None, raising=False)

    payload = await admin_analytics.analytics_cf_overview(range="7d")

    assert payload["connected"] is False
    assert isinstance(payload["totals"], dict)
    assert isinstance(payload["series"], list)
    assert payload["series"] == []


@pytest.mark.asyncio
async def test_cf_status_does_not_treat_configuration_as_a_successful_live_probe(monkeypatch):
    """Operators must see an unprobed integration instead of a false-green status."""
    monkeypatch.setattr(settings, "CF_ANALYTICS_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "CF_ZONE_ID", "test-zone", raising=False)
    monkeypatch.setattr(
        admin_analytics,
        "get_mongo_client",
        MagicMock(side_effect=RuntimeError("Mongo unavailable")),
    )

    payload = await admin_analytics.analytics_cf_status()

    assert payload["configured"] is True
    assert payload["auth_ok"] is False
    assert payload["last_check_at"] is None
    assert "has not run yet" in payload["rotation_hint"]


@pytest.mark.asyncio
async def test_cf_overview_returns_widget_shape_for_populated_traffic(monkeypatch):
    """Live Cloudflare data reaches the exact object/array shape dashboard widgets consume."""
    import httpx

    monkeypatch.setattr(settings, "CF_ANALYTICS_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "CF_ZONE_ID", "test-zone", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _CfGraphqlClient())

    payload = await admin_analytics.analytics_cf_overview(range="7d")

    assert payload["connected"] is True
    assert payload["totals"] == {
        "requests": 2000,
        "bytes": 3072,
        "visitors": 1432,
        "page_views": 1376,
        "threats": 3,
    }
    assert payload["series"] == [
        {"date": "2026-08-20", "requests": 800, "bytes": 1024, "page_views": 500, "threats": 2, "visitors": 625},
        {"date": "2026-08-21", "requests": 1200, "bytes": 2048, "page_views": 876, "threats": 1, "visitors": 940},
    ]
    # Preserve the pre-existing flattened values during the response migration.
    assert payload["requests_24h"] == 2000
    assert payload["page_views_24h"] == 1376


@pytest.mark.asyncio
async def test_cf_24h_overview_uses_hourly_buckets_and_real_visitor_aggregate(monkeypatch):
    """The rolling visitor card must not be built from daily buckets or fabricated zeros."""
    import httpx

    requests = []
    monkeypatch.setattr(settings, "CF_ANALYTICS_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "CF_ZONE_ID", "test-zone", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _CfGraphqlClient(requests))

    payload = await admin_analytics.analytics_cf_overview(range="24h")

    assert "httpRequests1hGroups" in requests[0]["query"]
    assert "httpRequestsAdaptiveGroups" in requests[0]["query"]
    assert "$groupFilter" not in requests[0]["query"]
    assert "$aggregateFilter" not in requests[0]["query"]
    assert 'filter: {datetime_geq: "' in requests[0]["query"]
    assert payload["bucket"] == "hour"
    assert payload["totals"]["visitors"] == 1432
    assert payload["series"][-1] == {
        "date": "2026-08-21T11:00:00Z",
        "requests": 120,
        "bytes": 2048,
        "page_views": 76,
        "threats": 1,
        "visitors": 62,
    }


@pytest.mark.asyncio
async def test_cf_overview_omits_visitors_when_cloudflare_does_not_supply_uniques(monkeypatch):
    """Unavailable unique-IP data must not be silently represented as zero visitors."""
    import httpx

    payload_without_uniques = {
        "data": {
            "viewer": {
                "zones": [{
                    "httpRequests1dGroups": [{
                        "dimensions": {"date": "2026-08-21"},
                        "sum": {"requests": 12, "pageViews": 8, "threats": 0, "bytes": 100},
                    }],
                    "uniqueVisitors": [{"uniq": {}}],
                }],
            },
        },
    }
    monkeypatch.setattr(settings, "CF_ANALYTICS_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "CF_ZONE_ID", "test-zone", raising=False)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: _CfGraphqlClient(payload=payload_without_uniques),
    )

    payload = await admin_analytics.analytics_cf_overview(range="7d")

    assert "visitors" not in payload["totals"]
    assert "visitors" not in payload["series"][0]


@pytest.mark.asyncio
async def test_cf_overview_marks_graphql_errors_unavailable(monkeypatch):
    """Cloudflare validation errors arrive as HTTP 200 and must not masquerade as empty traffic."""
    import httpx

    monkeypatch.setattr(settings, "CF_ANALYTICS_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "CF_ZONE_ID", "test-zone", raising=False)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: _CfGraphqlClient(payload={"errors": [{"message": "Unknown type filter"}]}),
    )

    payload = await admin_analytics.analytics_cf_overview(range="24h")

    assert payload["connected"] is False
    assert payload["source"] == "unavailable"
    assert "Cloudflare GraphQL error" in payload["error"]


@pytest.mark.asyncio
async def test_cf_contract_probe_validates_hourly_buckets_and_unique_aggregate(monkeypatch):
    """The scheduled probe uses the live 24-hour overview query and validates its shape."""
    import httpx

    requests = []
    monkeypatch.setattr(settings, "CF_ANALYTICS_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "CF_ZONE_ID", "test-zone", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _CfGraphqlClient(requests))

    result = await admin_analytics.check_cf_overview_contract()

    assert result["status"] == "healthy"
    assert result["hourly_buckets_returned"] is True
    assert result["hourly_bucket_count"] == 2
    assert result["unique_visitors_supported"] is True
    assert "httpRequests1hGroups" in requests[0]["query"]
    assert "httpRequestsAdaptiveGroups" in requests[0]["query"]


@pytest.mark.asyncio
async def test_cf_contract_probe_alerts_when_provider_omits_required_aggregate(monkeypatch):
    """A successful HTTP response with a missing aggregate must not look healthy."""
    import httpx

    payload_missing_aggregate = {
        "data": {
            "viewer": {
                "zones": [{
                    "httpRequests1hGroups": [],
                }],
            },
        },
    }
    monkeypatch.setattr(settings, "CF_ANALYTICS_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "CF_ZONE_ID", "test-zone", raising=False)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: _CfGraphqlClient(payload=payload_missing_aggregate),
    )

    result = await admin_analytics.check_cf_overview_contract()

    assert result["status"] == "unhealthy"
    assert result["hourly_buckets_returned"] is False
    assert "uniqueVisitors aggregate" in result["error"]
    assert "CF_ANALYTICS_TOKEN" in result["remediation"]


@pytest.mark.asyncio
async def test_cf_contract_probe_reports_unavailable_unique_values_without_false_alert(monkeypatch):
    """A supported aggregate without a count is not a schema/query regression."""
    import httpx

    payload_without_unique_value = {
        "data": {
            "viewer": {
                "zones": [{
                    "httpRequests1hGroups": [],
                    "uniqueVisitors": [{"uniq": {}}],
                }],
            },
        },
    }
    monkeypatch.setattr(settings, "CF_ANALYTICS_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "CF_ZONE_ID", "test-zone", raising=False)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: _CfGraphqlClient(payload=payload_without_unique_value),
    )

    result = await admin_analytics.check_cf_overview_contract()

    assert result["status"] == "healthy"
    assert result["hourly_buckets_returned"] is True
    assert result["unique_visitors_supported"] is False


@pytest.mark.asyncio
async def test_cf_contract_probe_persists_admin_health_and_deduplicated_alert(monkeypatch):
    """A failed scheduled probe is visible in both existing admin health paths."""
    db = MagicMock()
    db.service_health.update_one = AsyncMock()
    db.alerts.update_one = AsyncMock()
    monkeypatch.setattr(admin_analytics, "get_mongo_client", _mongo_for(db))

    await admin_analytics.persist_cf_overview_contract_result(
        {
            "status": "unhealthy",
            "checked_at": "2026-08-21T00:00:00+00:00",
            "error": "Cloudflare GraphQL error: Cannot query field",
            "remediation": "Update the overview query.",
            "needs_rotation": False,
            "hourly_buckets_returned": False,
            "unique_visitors_supported": None,
        }
    )

    health_filter, health_update = db.service_health.update_one.await_args.args
    assert health_filter == {"key": "cloudflare_analytics_overview"}
    assert health_update["$set"]["status"] == "unhealthy"
    alert_filter, alert_update = db.alerts.update_one.await_args.args
    assert alert_filter["dedup_key"] == "cloudflare_analytics_overview_query"
    assert alert_update["$set"]["severity"] == "high"
    assert "Update the overview query." in alert_update["$set"]["message"]


@pytest.mark.asyncio
async def test_cf_status_surfaces_the_persisted_query_remediation(monkeypatch):
    """The existing admin status banner receives the saved query failure details."""
    checked_at = datetime(2026, 8, 21, tzinfo=timezone.utc)
    db = MagicMock()
    db.service_health.find_one = AsyncMock(
        return_value={
            "status": "unhealthy",
            "checked_at": checked_at,
            "error": "Cloudflare GraphQL error: Unknown field",
            "remediation": "Update the overview query.",
            "needs_rotation": False,
            "hourly_buckets_returned": False,
            "unique_visitors_supported": None,
        }
    )
    monkeypatch.setattr(settings, "CF_ANALYTICS_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(settings, "CF_ZONE_ID", "test-zone", raising=False)
    monkeypatch.setattr(admin_analytics, "get_mongo_client", _mongo_for(db))

    payload = await admin_analytics.analytics_cf_status()

    assert payload["auth_ok"] is False
    assert payload["last_error"] == "Cloudflare GraphQL error: Unknown field"
    assert payload["rotation_hint"] == "Update the overview query."
    assert payload["last_check_at"] == checked_at.isoformat()


@pytest.mark.asyncio
async def test_cf_contract_cron_returns_503_after_recording_provider_failure(monkeypatch):
    """Scheduled callers fail loudly only after the health/alert result is recorded."""
    monkeypatch.setattr(settings, "TRANSLATE_CRON_SECRET", "cron-secret", raising=False)
    probe = {
        "status": "unhealthy",
        "checked_at": "2026-08-21T00:00:00+00:00",
        "error": "Cloudflare GraphQL error: Unknown field",
        "remediation": "Update the overview query.",
        "needs_rotation": False,
        "hourly_buckets_returned": False,
        "unique_visitors_supported": None,
    }
    persisted = AsyncMock()
    monkeypatch.setattr(admin_analytics, "check_cf_overview_contract", AsyncMock(return_value=probe))
    monkeypatch.setattr(admin_analytics, "persist_cf_overview_contract_result", persisted)

    request = MagicMock()
    request.headers = {"authorization": "Bearer cron-secret"}
    response = await admin_cron.cron_cloudflare_analytics_health(request)

    assert response.status_code == 503
    assert json.loads(response.body) == probe
    persisted.assert_awaited_once_with(probe)