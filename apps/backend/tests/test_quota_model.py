from datetime import datetime, timezone

from app.models.quota import QuotaUsage


def test_quota_count_alias_keeps_existing_mongo_field_name():
    usage = QuotaUsage.model_construct(
        user_id="test-user",
        month="2026-10",
        quota_count=7,
        expires_at=datetime.now(timezone.utc),
    )

    assert usage.model_dump()["quota_count"] == 7
    assert usage.model_dump(by_alias=True)["count"] == 7
    assert "quota_count" not in usage.model_dump(by_alias=True)
