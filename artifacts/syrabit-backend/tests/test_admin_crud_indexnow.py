"""End-to-end style tests verifying that admin subject/chapter CRUD endpoints
fire IndexNow notifications. Calls the actual route handler functions with
mocked DB and a recording IndexNow batcher.
"""
import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock


from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()


def _make_fake_batcher():
    queued: list[list[str]] = []
    flush_calls: list[str] = []

    fake = MagicMock()

    async def _queue(paths):
        queued.append(list(paths))

    async def _flush(source: str = ""):
        flush_calls.append(source)

    fake.queue_raw_paths = AsyncMock(side_effect=_queue)
    fake.flush = AsyncMock(side_effect=_flush)
    return fake, queued, flush_calls


def _install_batcher(fake):
    from routes import bot_discovery
    bot_discovery.indexnow_batcher = fake


async def _drain():
    for _ in range(15):
        await asyncio.sleep(0)


def test_admin_patch_subject_endpoint_fires_indexnow():
    """Calling PATCH /admin/content/subjects/{id} should trigger IndexNow
    with the subject's full path (board/class/subject)."""
    async def run():
        fake, queued, flush_calls = _make_fake_batcher()
        _install_batcher(fake)

        from routes import admin_content

        fake_db = MagicMock()
        fake_db.subjects.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )
        fake_db.subjects.find_one = AsyncMock(return_value={
            "board_slug": "ahsec",
            "class_slug": "class-12",
            "slug": "physics",
        })
        admin_content.db = fake_db
        admin_content._invalidate_content_cache = lambda *a, **kw: None
        admin_content._schedule_d1_sync_fire = lambda *a, **kw: None

        result = await admin_content.admin_patch_subject(
            "subj-id-1",
            {"name": "Physics Updated"},
            admin={"id": "admin"},
        )
        await _drain()

        assert result == {"message": "Subject updated"}
        fake_db.subjects.update_one.assert_awaited_once()
        assert queued == [["/ahsec/class-12/physics"]]
        assert flush_calls == ["admin_subject_update"]

    asyncio.run(run())


def test_admin_patch_subject_404_does_not_fire_indexnow():
    """If the subject doesn't exist (HTTP 404), no IndexNow call should be made."""
    async def run():
        fake, queued, flush_calls = _make_fake_batcher()
        _install_batcher(fake)

        from fastapi import HTTPException
        from routes import admin_content

        fake_db = MagicMock()
        fake_db.subjects.update_one = AsyncMock(
            return_value=MagicMock(matched_count=0)
        )
        fake_db.subjects.find_one = AsyncMock()
        admin_content.db = fake_db
        admin_content._invalidate_content_cache = lambda *a, **kw: None
        admin_content._schedule_d1_sync_fire = lambda *a, **kw: None

        try:
            await admin_content.admin_patch_subject(
                "missing", {"name": "X"}, admin={"id": "admin"}
            )
            raised = False
        except HTTPException as exc:
            raised = exc.status_code == 404
        await _drain()

        assert raised is True
        assert queued == []
        assert flush_calls == []

    asyncio.run(run())


def test_admin_update_chapter_endpoint_fires_indexnow():
    """Calling PATCH /admin/content/chapters/{id} should trigger IndexNow
    with the chapter's full path (board/class/subject/chapter)."""
    async def run():
        fake, queued, flush_calls = _make_fake_batcher()
        _install_batcher(fake)

        from routes import admin_content

        fake_db = MagicMock()
        fake_db.chapters.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )
        # Chapter find_one used twice: once for slug-dup check (skipped — no slug
        # in payload), once for indexnow at line 1813.
        fake_db.chapters.find_one = AsyncMock(return_value={
            "subject_id": "subj-1",
            "slug": "algebra",
        })
        # Subject lookup performed inside _schedule_indexnow_for_chapter
        fake_db.subjects.find_one = AsyncMock(return_value={
            "board_slug": "seba",
            "class_slug": "class-10",
            "slug": "maths",
        })
        admin_content.db = fake_db
        admin_content._invalidate_content_cache = lambda *a, **kw: None
        admin_content._schedule_d1_sync_fire = lambda *a, **kw: None
        # Ensure the deps stub also serves the same DB for the chapter helper
        sys.modules["deps"].db = fake_db

        result = await admin_content.admin_update_chapter(
            "chap-id-1",
            {"description": "Updated description"},
            admin={"id": "admin"},
        )
        await _drain()

        assert result.get("message") == "Chapter updated"
        fake_db.chapters.update_one.assert_awaited_once()
        assert queued == [["/seba/class-10/maths/algebra"]]
        assert flush_calls == ["admin_chapter_update"]

    asyncio.run(run())


def test_admin_update_chapter_404_does_not_fire_indexnow():
    async def run():
        fake, queued, flush_calls = _make_fake_batcher()
        _install_batcher(fake)

        from fastapi import HTTPException
        from routes import admin_content

        fake_db = MagicMock()
        fake_db.chapters.update_one = AsyncMock(
            return_value=MagicMock(matched_count=0)
        )
        fake_db.chapters.find_one = AsyncMock()
        admin_content.db = fake_db
        admin_content._invalidate_content_cache = lambda *a, **kw: None
        admin_content._schedule_d1_sync_fire = lambda *a, **kw: None

        try:
            await admin_content.admin_update_chapter(
                "missing", {"description": "x"}, admin={"id": "admin"}
            )
            raised = False
        except HTTPException as exc:
            raised = exc.status_code == 404
        await _drain()

        assert raised is True
        assert queued == []
        assert flush_calls == []

    asyncio.run(run())


def test_update_page_status_bumps_updated_at_and_fires_indexnow():
    """Phase B / Plan 6: exercise a real AI-rewrite-adjacent endpoint
    (`update_page_status` flipping a draft page to published — the same
    helper called from the admin review queue and from quality rescore
    paths) and assert (a) the persisted `$set` includes a fresh
    `updated_at` and (b) `notify_indexnow_for_page` is scheduled with
    the correct slug fields. Together with `test_diff_repushes_seo_page_
    after_ai_rewrite` in test_indexnow_sitemap_diff.py, this covers both
    halves of the rewrite → IndexNow loop required by Phase B.
    """
    import asyncio as _asyncio
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, MagicMock, patch

    from tests._deps_stub import install_deps_stub
    install_deps_stub()
    import seo_engine

    captured_set: dict = {}

    fake_db = MagicMock()
    async def _update_one(_q, payload):
        captured_set.update(payload["$set"])
        return MagicMock(matched_count=1)
    fake_db.seo_pages.update_one = AsyncMock(side_effect=_update_one)

    page_doc = {
        "board_slug": "ahsec", "class_slug": "class-12",
        "subject_slug": "physics", "topic_slug": "newtons-laws",
        "page_type": "notes",
    }
    fake_db.seo_pages.find_one = AsyncMock(return_value=page_doc)
    seo_engine._db = fake_db

    notified: list[dict] = []
    async def _fake_notify(p):
        notified.append(p)

    with patch("routes.bot_discovery.notify_indexnow_for_page", side_effect=_fake_notify):
        before = datetime.now(timezone.utc)
        result = _asyncio.run(
            seo_engine.update_page_status(
                page_id="page-xyz", status="published", _admin={"sub": "admin"}
            )
        )
        # Drain the asyncio.create_task scheduled inside update_page_status
        async def _drain():
            for _ in range(20):
                await _asyncio.sleep(0)
        _asyncio.run(_drain())

    assert result == {"message": "Status updated to published"}
    # (a) updated_at was bumped to a fresh ISO 8601 timestamp ≥ before
    assert "updated_at" in captured_set, "publish must bump updated_at"
    bumped = datetime.fromisoformat(captured_set["updated_at"])
    assert bumped >= before, "updated_at must be >= test start"
    assert captured_set["status"] == "published"
    assert captured_set["in_sitemap"] is True
    # (b) notify_indexnow_for_page was scheduled with the page slug fields
    assert len(notified) == 1
    n = notified[0]
    assert n["board_slug"] == "ahsec" and n["topic_slug"] == "newtons-laws"


def test_update_page_status_indexnow_failure_is_non_blocking():
    """IndexNow push_indexnow raising must not surface to the caller of
    update_page_status.  The function wraps the notification in both
    notify_indexnow_for_page (which swallows its own errors) and
    asyncio.create_task (fire-and-forget).  If either layer fails the
    HTTP response must still be {"message": "Status updated to published"}.
    This covers the 'silently ignores failures (non-critical)' requirement
    from Task #261."""
    import asyncio as _asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from tests._deps_stub import install_deps_stub
    install_deps_stub()
    import seo_engine

    fake_db = MagicMock()
    fake_db.seo_pages.update_one = AsyncMock(
        return_value=MagicMock(matched_count=1)
    )
    fake_db.seo_pages.find_one = AsyncMock(return_value={
        "board_slug": "cbse", "class_slug": "class-10",
        "subject_slug": "maths", "topic_slug": "polynomials",
        "page_type": "notes",
    })
    seo_engine._db = fake_db

    async def _exploding_notify(p):
        raise RuntimeError("IndexNow endpoint unreachable — non-blocking test")

    with patch("routes.bot_discovery.notify_indexnow_for_page",
               side_effect=_exploding_notify):
        result = _asyncio.run(
            seo_engine.update_page_status(
                page_id="page-fail", status="published", _admin={"sub": "admin"}
            )
        )
        async def _drain():
            for _ in range(20):
                await _asyncio.sleep(0)
        _asyncio.run(_drain())

    # Publication must succeed even though IndexNow threw
    assert result == {"message": "Status updated to published"}
