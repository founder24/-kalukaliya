"""Task #795 — admin write routes must trigger Cloudflare cache purge.

Each previously-unwired write route (subject thumbnail upload, AI thumbnail
generation, bulk thumbnail generation, chapter-card thumbnails, content
chunks, file upload, reset-and-seed, MongoDB → D1 sync, subject document
upload/delete) is now expected to fan out a cache invalidation. These
tests call each route handler directly with a mocked DB, capture the
purge calls, and assert the right content prefixes were invalidated so
this wiring can't silently regress.

The purge dispatch path itself (`_invalidate_content_cache` →
`_fire_cf_edge_purge` → `purge_content_prefixes` + `purge_worker_cache`)
is already covered by `test_chapter_by_slug_regression.py` and
`tests/test_admin_draft_served_subjects.py`; here we only verify the
admin-route → invalidation hook fires with the right prefix list.
"""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()

# Smallest possible PNG (1x1 transparent) used by tests that exercise
# code paths which decode the subject's `data:image/png;base64,...`
# thumbnail through PIL. Hand-crafted to keep the test self-contained
# rather than pulling Pillow in test setup.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkAA"
    "IAAAUAAarVyFEAAAAASUVORK5CYII="
)


def _install_purge_recorder(mod):
    """Replace `_invalidate_content_cache` and `_purge_for_route` on the
    module with recorders so we can assert what was purged. Returns the
    recording lists.
    """
    invalidations: list[str] = []
    audit_calls: list[tuple[str, list, str]] = []

    mod._invalidate_content_cache = lambda prefix: invalidations.append(prefix)

    # Re-bind the helper to use the patched _invalidate so the audit log
    # path still runs but we capture it separately too.
    real_purge = mod._purge_for_route

    def _spy(route, prefixes, content_id=""):
        audit_calls.append((route, list(prefixes), content_id))
        for p in prefixes:
            mod._invalidate_content_cache(p)

    mod._purge_for_route = _spy
    return invalidations, audit_calls, real_purge


async def _drain():
    for _ in range(5):
        await asyncio.sleep(0)


def test_subject_thumbnail_upload_purges_subjects():
    async def run():
        from routes import admin_content
        from fastapi import UploadFile
        import io

        invalidations, audit, _ = _install_purge_recorder(admin_content)

        fake_db = MagicMock()
        fake_db.subjects.find_one = AsyncMock(return_value={"id": "subj-1"})
        fake_db.subjects.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )
        admin_content.db = fake_db

        png_bytes = b"\x89PNG\r\n\x1a\n" + b"fake-png-payload" * 4
        upload = UploadFile(filename="t.png", file=io.BytesIO(png_bytes))
        upload.headers = {"content-type": "image/png"}
        # Force the content_type seen by the handler
        object.__setattr__(upload, "_content_type", "image/png")

        class _Stub:
            content_type = "image/png"

            async def read(self):
                return png_bytes

        result = await admin_content.upload_subject_thumbnail(
            "subj-1", file=_Stub(), admin={"id": "admin"}
        )
        await _drain()

        assert "thumbnailUrl" in result
        assert "subjects" in invalidations, invalidations
        routes = [r for (r, _p, _i) in audit]
        assert "admin.subject_thumbnail_upload" in routes

    asyncio.run(run())


def test_chunk_create_purges_chapters():
    async def run():
        from routes import admin_content
        from routes.admin_content import ChunkCreate

        invalidations, audit, _ = _install_purge_recorder(admin_content)

        fake_db = MagicMock()
        fake_db.chunks.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id="chunk-mongo-id")
        )
        admin_content.db = fake_db

        data = ChunkCreate(
            chapter_id="chap-1", content="x" * 50, category="notes", tags=[]
        )
        result = await admin_content.admin_create_chunk(data, admin={"id": "admin"})
        await _drain()

        assert result["chapter_id"] == "chap-1"
        assert "chapters" in invalidations
        routes = [r for (r, _p, _i) in audit]
        assert "admin.chunk_create" in routes

    asyncio.run(run())


def test_d1_sync_purges_worker_cache():
    """After admin_trigger_d1_sync we expect a worker purge_all call.

    Task #428 — the response also includes an ``extended_mirror`` block
    summarizing the post-sync extended mirror run so a manual dry-run
    can verify success + row_counts without a separate /admin/cf-health
    round-trip.
    """
    async def run():
        from routes import admin_content

        # Stub the d1_sync module surface
        d1_stub = MagicMock()
        d1_stub.is_d1_configured = lambda: True
        d1_stub.sync_full = AsyncMock(return_value={"ok": True, "tables": 5})
        d1_stub.sync_tables = AsyncMock(return_value={"ok": True})
        sys.modules["d1_sync"] = d1_stub

        # Stub the d1_mirror.sync_extended call so the route's
        # post-sync_full extended-mirror hook returns a deterministic
        # summary we can assert on.
        mirror_stub = MagicMock()
        mirror_stub.sync_extended = AsyncMock(return_value={
            "success": True,
            "tables": ["seo_meta", "audit_log", "syllabus_map"],
            "row_counts": {"seo_meta": 12, "audit_log": 7, "syllabus_map": 3},
        })
        sys.modules["d1_mirror"] = mirror_stub

        # Capture worker purge calls
        cf_stub = MagicMock()
        cf_stub.purge_worker_cache = AsyncMock(return_value=True)
        sys.modules["cloudflare_client"] = cf_stub

        result = await admin_content.admin_trigger_d1_sync(
            admin={"id": "admin"}, tables=None
        )
        await _drain()

        assert result["ok"] is True
        assert result["tables"] == 5
        ext = result["extended_mirror"]
        assert ext["success"] is True
        assert ext["row_counts"] == {
            "seo_meta": 12, "audit_log": 7, "syllabus_map": 3,
        }
        assert ext["tables"] == ["seo_meta", "audit_log", "syllabus_map"]
        mirror_stub.sync_extended.assert_awaited_once()
        cf_stub.purge_worker_cache.assert_awaited_once()
        kwargs = cf_stub.purge_worker_cache.await_args.kwargs
        assert kwargs.get("purge_all") is True

    asyncio.run(run())


def test_reset_and_seed_purges_everything():
    async def run():
        from routes import admin_content

        invalidations, audit, _ = _install_purge_recorder(admin_content)

        fake_db = MagicMock()
        fake_db.chapters.delete_many = AsyncMock(
            return_value=MagicMock(deleted_count=10)
        )
        fake_db.content_uploads.delete_many = AsyncMock(
            return_value=MagicMock(deleted_count=5)
        )

        # Async cursor for db.subjects.find().limit().to_list()
        subj_cursor = MagicMock()
        subj_cursor.limit = MagicMock(return_value=subj_cursor)
        subj_cursor.to_list = AsyncMock(return_value=[
            {"id": "s1", "name": "Math"},
            {"id": "s2", "name": "Physics"},
        ])
        fake_db.subjects.find = MagicMock(return_value=subj_cursor)
        fake_db.subjects.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )
        fake_db.chapters.insert_one = AsyncMock(return_value=MagicMock())
        admin_content.db = fake_db

        cf_stub = MagicMock()
        cf_stub.purge_all_content_cache = AsyncMock(return_value=True)
        sys.modules["cloudflare_client"] = cf_stub

        # Suppress prerender background task
        async def _noop(*a, **kw):
            return None
        admin_content._trigger_prerender_now = _noop

        result = await admin_content.reset_and_seed_content(admin={"id": "admin"})
        await _drain()

        assert result["chapters"] == 6  # 2 subjects × 3 chapters each
        cf_stub.purge_all_content_cache.assert_awaited_once()
        assert "chapters" in invalidations
        assert "subjects" in invalidations
        routes = [r for (r, _p, _i) in audit]
        assert "admin.reset_and_seed" in routes

    asyncio.run(run())


def test_subject_document_upload_purges_subjects_in_content_router():
    async def run():
        from routes import content as content_mod

        invalidations: list[str] = []
        content_mod._invalidate_content_cache = lambda prefix: invalidations.append(prefix)

        fake_db = MagicMock()
        fake_db.subjects.find_one = AsyncMock(return_value={"id": "subj-A"})
        fake_db.subjects.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )
        content_mod.db = fake_db

        from models import DocumentUpload

        payload = DocumentUpload(
            subject_id="subj-A",
            document_name="syllabus.txt",
            document_text="Hello world. " * 5,
            document_type="text",
        )
        result = await content_mod.upload_subject_document(
            "subj-A", payload, admin={"id": "admin"}
        )
        assert result["message"] == "Document uploaded"
        assert "subjects" in invalidations

        # And the DELETE side
        invalidations.clear()
        result = await content_mod.delete_subject_document(
            "subj-A", admin={"id": "admin"}
        )
        assert result["message"] == "Document removed"
        assert "subjects" in invalidations

    asyncio.run(run())


def test_content_manual_create_purges_subjects():
    """`/admin/content/uploads/manual` writes to content_uploads which feeds
    the document fallback at `/api/content/subjects/{id}/document` — and
    that path is edge-cached under the `/api/content/subjects` prefix."""
    async def run():
        from routes import admin_content

        invalidations, audit, _ = _install_purge_recorder(admin_content)

        fake_db = MagicMock()
        fake_db.content_uploads.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id="ins")
        )
        admin_content.db = fake_db

        result = await admin_content.create_content_manual(
            {
                "subject_id": "subj-X",
                "title": "Manual chapter",
                "content": "x" * 100,
                "content_type": "chapter",
            },
            admin={"id": "admin", "email": "a@x"},
        )
        await _drain()

        assert result["subject_id"] == "subj-X"
        assert "subjects" in invalidations
        routes = [r for (r, _p, _i) in audit]
        assert "admin.content_manual_create" in routes

    asyncio.run(run())


def test_content_manual_delete_purges_subjects_and_resolves_subject_id():
    """The DELETE looks up subject_id BEFORE deleting so the audit log can
    record which subject's edge cache it invalidated."""
    async def run():
        from routes import admin_content

        invalidations, audit, _ = _install_purge_recorder(admin_content)

        fake_db = MagicMock()
        fake_db.content_uploads.find_one = AsyncMock(
            return_value={"subject_id": "subj-Q"}
        )
        fake_db.content_uploads.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=1)
        )
        admin_content.db = fake_db

        result = await admin_content.delete_content_upload(
            "content-id-1", admin={"id": "admin"}
        )
        await _drain()

        assert result["message"] == "Content deleted"
        assert "subjects" in invalidations
        # Verify subject_id flowed through to the audit record
        match = [a for a in audit if a[0] == "admin.content_manual_delete"]
        assert match and match[0][2] == "subj-Q"

    asyncio.run(run())


def test_content_manual_delete_404_does_not_purge():
    """If the upload doesn't exist, the route raises 404 and we should not
    fan out a no-op cache purge / audit line."""
    async def run():
        from fastapi import HTTPException
        from routes import admin_content

        invalidations, audit, _ = _install_purge_recorder(admin_content)

        fake_db = MagicMock()
        fake_db.content_uploads.find_one = AsyncMock(return_value=None)
        fake_db.content_uploads.delete_one = AsyncMock(
            return_value=MagicMock(deleted_count=0)
        )
        admin_content.db = fake_db

        try:
            await admin_content.delete_content_upload(
                "missing", admin={"id": "admin"}
            )
            raised = False
        except HTTPException as exc:
            raised = exc.status_code == 404
        await _drain()

        assert raised is True
        assert invalidations == []
        assert audit == []

    asyncio.run(run())


def test_thumbnail_generate_bulk_purges_only_when_at_least_one_done():
    """Bulk AI thumbnail generation fans out to ≤50 subjects. We expect:
    - one purge call for the whole batch when done_count > 0;
    - zero purge calls when every subject was skipped (no data: thumbnail)."""
    async def run():
        from routes import admin_content

        # Case A: nothing skip-worthy, all 3 subjects lack a data: thumbnail
        invalidations, audit, _ = _install_purge_recorder(admin_content)
        fake_db = MagicMock()
        fake_db.subjects.find_one = AsyncMock(side_effect=[
            {"thumbnailUrl": "https://example/x.png", "name": "A"},
            {"thumbnailUrl": "", "name": "B"},
            None,
        ])
        admin_content.db = fake_db

        result = await admin_content.generate_ai_thumbnails_bulk(
            data={"subject_ids": ["s1", "s2", "s3"]}, admin={"id": "admin"}
        )
        await _drain()
        assert result["done"] == 0
        assert all(r["status"] == "skipped" for r in result["results"])
        assert invalidations == []
        assert audit == []

        # Case B: one subject succeeds (we patch the heavy vision/PIL work
        # so the success branch runs without external deps).
        invalidations.clear(); audit.clear()
        # Reset find_one with one successful subject and force the loop body
        # to short-circuit at update_one (fake success). We patch the
        # internal helpers to no-op.
        fake_db.subjects.find_one = AsyncMock(return_value={
            "thumbnailUrl": "data:image/png;base64," + _TINY_PNG_B64,
            "name": "Math",
        })
        fake_db.subjects.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )
        admin_content._extract_dominant_colors = lambda _b: ["#000"]
        admin_content._sanitize_text_regions = lambda r: r or []

        async def _fake_vision(_b64, _mime):
            return {"dominant_colors": ["#fff"], "text_regions": []}
        admin_content._analyze_with_groq_vision = _fake_vision
        admin_content._remove_text_variant = lambda _b, _r, _i: "data:image/png;base64,Zm9v"

        result = await admin_content.generate_ai_thumbnails_bulk(
            data={"subject_ids": ["s1"]}, admin={"id": "admin"}
        )
        await _drain()
        assert result["done"] == 1
        assert "subjects" in invalidations
        routes = [r for (r, _p, _i) in audit]
        assert "admin.thumbnail_generate_bulk" in routes

    asyncio.run(run())


def test_chapter_card_thumbnails_purges_chapters_and_subjects():
    """Chapter-card generation rewrites `card_thumbnails` + `thumbnailUrl`
    on each chapter — we expect a purge of both `chapters` and `subjects`
    (the latter because the library bundle nests chapters under subjects)."""
    async def run():
        from routes import admin_content

        invalidations, audit, _ = _install_purge_recorder(admin_content)

        fake_db = MagicMock()
        fake_db.subjects.find_one = AsyncMock(return_value={"name": "Physics"})
        ch_cursor = MagicMock()
        ch_cursor.to_list = AsyncMock(return_value=[
            {"id": "ch-1", "title": "Mechanics"},
            {"id": "ch-2", "title": "Optics"},
        ])
        fake_db.chapters.find = MagicMock(return_value=ch_cursor)
        fake_db.chapters.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )
        admin_content.db = fake_db

        # Patch the heavy wallpaper renderer
        admin_content._generate_chapter_card_wallpaper = (
            lambda _t, _s, _v=0: "data:image/png;base64,Zm9v"
        )

        result = await admin_content.generate_chapter_card_thumbnails(
            data={"subject_id": "subj-P", "chapter_ids": ["ch-1", "ch-2"]},
            admin={"id": "admin"},
        )
        await _drain()

        assert result["done"] == 2
        assert "chapters" in invalidations
        assert "subjects" in invalidations
        routes = [r for (r, _p, _i) in audit]
        assert "admin.thumbnail_generate_chapter_cards" in routes

    asyncio.run(run())


def test_chapter_card_thumbnails_no_chapters_skips_purge():
    """When the chapter list is empty, no DB writes happen and we should
    not issue a wasted purge."""
    async def run():
        from routes import admin_content

        invalidations, audit, _ = _install_purge_recorder(admin_content)

        fake_db = MagicMock()
        fake_db.subjects.find_one = AsyncMock(return_value={"name": "Physics"})
        ch_cursor = MagicMock()
        ch_cursor.to_list = AsyncMock(return_value=[])
        fake_db.chapters.find = MagicMock(return_value=ch_cursor)
        admin_content.db = fake_db

        result = await admin_content.generate_chapter_card_thumbnails(
            data={"subject_id": "subj-P"}, admin={"id": "admin"}
        )
        await _drain()

        assert result["done"] == 0
        assert invalidations == []
        assert audit == []

    asyncio.run(run())


def test_upload_content_file_purges_subjects_and_chapters():
    """Generic content file upload rewrites `has_document` on the subject
    AND inserts a content_uploads row that the chapter-list fallback
    reads — both prefixes must be invalidated."""
    async def run():
        from routes import admin_content

        invalidations, audit, _ = _install_purge_recorder(admin_content)

        fake_db = MagicMock()
        fake_db.content_uploads.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id="up-1")
        )
        fake_db.subjects.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )
        admin_content.db = fake_db
        admin_content._schedule_prerender_refresh = lambda *a, **kw: None

        class _FakeUpload:
            filename = "notes.txt"
            content_type = "text/plain"

            async def read(self):
                return b"hello world content"

        result = await admin_content.upload_content_file(
            file=_FakeUpload(),
            subject_id="subj-Z",
            content_type="document",
            title=None,
            description="",
            tags="",
            year="",
            admin={"id": "admin", "email": "a@x"},
        )
        await _drain()

        assert result["message"] == "Upload successful"
        assert "subjects" in invalidations
        assert "chapters" in invalidations
        routes = [r for (r, _p, _i) in audit]
        assert "admin.content_upload" in routes

    asyncio.run(run())


def test_thumbnail_generate_single_purges_subjects():
    """Single-subject AI thumbnail generation overwrites `thumbnailUrl`
    and `thumbnail_variants` — purge subjects so the public payload
    refreshes."""
    async def run():
        from routes import admin_content

        invalidations, audit, _ = _install_purge_recorder(admin_content)

        # Use an existing data: URL on the subject so the handler skips
        # the file/upload branch and reuses the embedded image.
        fake_db = MagicMock()
        fake_db.subjects.find_one = AsyncMock(return_value={
            "id": "subj-T",
            "thumbnailUrl": "data:image/png;base64," + ("AAAA" * 10),
        })
        fake_db.subjects.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1)
        )
        admin_content.db = fake_db

        # Patch heavy bits
        admin_content._extract_dominant_colors = lambda _b: ["#000"]
        admin_content._sanitize_text_regions = lambda r: r or []

        async def _fake_vision(_b64, _mime):
            return {"dominant_colors": ["#fff"], "text_regions": []}
        admin_content._analyze_with_groq_vision = _fake_vision
        admin_content._remove_text_variant = lambda _b, _r, _i: "data:image/png;base64,Zm9v"

        result = await admin_content.generate_ai_thumbnails(
            subject_id="subj-T", file=None, admin={"id": "admin"}
        )
        await _drain()

        assert "variants" in result
        assert "subjects" in invalidations
        routes = [r for (r, _p, _i) in audit]
        assert "admin.thumbnail_generate" in routes

    asyncio.run(run())


def test_purge_for_route_is_safe_when_invalidate_raises():
    """The helper must never raise — purges are best-effort hygiene, not
    a correctness barrier. A failing prefix should be logged and the
    audit line should still fire for the remaining prefixes."""
    async def run():
        from routes import admin_content

        calls: list[str] = []

        def _broken(prefix):
            if prefix == "boards":
                raise RuntimeError("redis down")
            calls.append(prefix)

        admin_content._invalidate_content_cache = _broken

        # Restore the real _purge_for_route in case a previous test
        # replaced it with a recorder
        import importlib
        importlib.reload(admin_content)
        admin_content._invalidate_content_cache = _broken

        admin_content._purge_for_route(
            "test.broken_prefix", ["boards", "subjects", "chapters"]
        )

        assert calls == ["subjects", "chapters"]

    asyncio.run(run())
