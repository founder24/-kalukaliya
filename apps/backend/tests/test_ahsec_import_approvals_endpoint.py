import argparse
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def admin_cookie():
    from app.config import settings

    expires = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": "admin-test", "type": "admin", "role": "admin", "exp": expires},
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )
    return {"syrabit_admin_session": token}


def _write_jsonl(path, *records):
    path.write_text(
        "\n".join(
            record if isinstance(record, str) else json.dumps(record)
            for record in records
        )
        + "\n",
        encoding="utf-8",
    )


def test_importer_state_directory_is_read_by_approval_history_endpoint(
    client, admin_cookie, monkeypatch, tmp_path
):
    import app.api.v1.admin_content as admin_content
    from scripts import ahsec_d1_import as importer

    state_dir = tmp_path / "import-state"
    approval_file = state_dir / "approvals.jsonl"
    progress_file = state_dir / "progress.jsonl"
    backup_file = state_dir / "notes-backup.jsonl"
    monkeypatch.setattr(importer, "STATE_DIR", state_dir)
    monkeypatch.setattr(importer, "APPROVAL_FILE", approval_file)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    monkeypatch.setattr(importer, "BACKUP_FILE", backup_file)
    monkeypatch.setattr(importer, "ACTIVE_RUN_ID", None)

    args = argparse.Namespace(
        dry_run=False,
        confirm_production_write=True,
        operator="curriculum-reviewer",
        class_level="11",
        subject="chemistry",
        limit=2,
        restart=False,
        skip_index=True,
        clean_preambles=False,
    )
    run_id, started_at = importer.record_production_approval(args)
    monkeypatch.setattr(importer, "ACTIVE_RUN_ID", run_id)
    importer.record_progress("chapter-1", "done")
    importer.backup_existing(
        {
            "id": "chapter-2",
            "subject_id": "subject-1",
            "notes_en": "backup note contents must remain private",
        },
        "https://example.test/book.pdf",
    )
    importer.record_progress(
        "chapter-2",
        "error",
        error="failed chapter details must not be exposed",
    )
    importer.record_terminal_summary("failed", completed=1, failed=1)

    with (
        patch.object(admin_content, "_AHSEC_D1_APPROVAL_FILE", approval_file),
        patch.object(admin_content, "_AHSEC_D1_IMPORT_PROGRESS_FILE", progress_file),
    ):
        response = client.get(
            "/api/v1/admin/content/ahsec-d1-import/approvals",
            cookies=admin_cookie,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["file_exists"] is True
    assert len(body["approvals"]) == 1
    approval = body["approvals"][0]
    assert approval["run_id"] == run_id
    assert approval["operator"] == "curriculum-reviewer"
    assert approval["started_at"] == started_at
    assert approval["scope"] == {
        "class": "11",
        "subject": "chemistry",
        "limit": 2,
        "restart": False,
        "skip_index": True,
        "clean_preambles": False,
    }
    assert approval["progress"]["status"] == "failed"
    assert approval["progress"]["chapters"] == 2
    assert approval["progress"]["completed"] == 1
    assert approval["progress"]["failed"] == 1
    assert approval["progress"]["last_updated_at"]
    assert "backup note contents must remain private" not in response.text
    assert "failed chapter details must not be exposed" not in response.text


def test_import_approvals_show_safe_metadata_and_linked_progress(
    client, admin_cookie, tmp_path
):
    import app.api.v1.admin_content as admin_content

    run_id = "run-new"
    _write_jsonl(
        tmp_path / "approvals.jsonl",
        "not-json",
        {
            "event": "production_write_approved",
            "run_id": "run-old",
            "operator": "old-operator",
            "started_at": "2026-09-10T10:00:00+00:00",
            "scope": {"subject": "physics"},
        },
        {
            "event": "production_write_approved",
            "run_id": run_id,
            "operator": "curriculum-reviewer",
            "started_at": "2026-09-13T10:00:00+00:00",
            "scope": {
                "class": "11",
                "subject": "chemistry",
                "limit": 2,
                "restart": False,
                "skip_index": True,
                "clean_preambles": False,
                "CLOUDFLARE_API_TOKEN": "should-not-leak",
            },
            "CLOUDFLARE_API_TOKEN": "should-not-leak",
        },
    )
    _write_jsonl(
        tmp_path / "progress.jsonl",
        {
            "run_id": run_id,
            "chapter_id": "chapter-1",
            "status": "done",
            "timestamp": "2026-09-13T10:02:00+00:00",
            "notes_en": "backup note contents must not be exposed",
        },
        {
            "run_id": run_id,
            "chapter_id": "chapter-2",
            "status": "error",
            "timestamp": "2026-09-13T10:03:00+00:00",
            "error": "credential-like detail must not be exposed",
        },
        {
            "event": "import_terminal_summary",
            "run_id": run_id,
            "status": "failed",
            "chapters": 4,
            "completed": 3,
            "failed": 1,
            "timestamp": "2026-09-13T10:04:00+00:00",
            "error": "terminal summaries must not copy this",
        },
    )

    with (
        patch.object(admin_content, "_AHSEC_D1_APPROVAL_FILE", tmp_path / "approvals.jsonl"),
        patch.object(
            admin_content,
            "_AHSEC_D1_IMPORT_PROGRESS_FILE",
            tmp_path / "progress.jsonl",
        ),
    ):
        response = client.get(
            "/api/v1/admin/content/ahsec-d1-import/approvals?limit=1",
            cookies=admin_cookie,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["file_exists"] is True
    assert len(body["approvals"]) == 1
    approval = body["approvals"][0]
    assert approval == {
        "run_id": run_id,
        "operator": "curriculum-reviewer",
        "started_at": "2026-09-13T10:00:00+00:00",
        "scope": {
            "class": "11",
            "subject": "chemistry",
            "limit": 2,
            "restart": False,
            "skip_index": True,
            "clean_preambles": False,
        },
        "progress": {
            "status": "failed",
            "chapters": 4,
            "completed": 3,
            "failed": 1,
            "last_updated_at": "2026-09-13T10:04:00+00:00",
        },
    }
    response_text = response.text
    assert "CLOUDFLARE_API_TOKEN" not in response_text
    assert "should-not-leak" not in response_text
    assert "backup note contents" not in response_text
    assert "credential-like detail" not in response_text
    assert "terminal summaries must not copy this" not in response_text


def test_import_approvals_keep_overlapping_chapters_scoped_to_run(
    client, admin_cookie, tmp_path
):
    import app.api.v1.admin_content as admin_content

    approvals = tmp_path / "approvals.jsonl"
    progress = tmp_path / "progress.jsonl"
    _write_jsonl(
        approvals,
        {
            "event": "production_write_approved",
            "run_id": "run-first",
            "operator": "first-operator",
            "started_at": "2026-09-14T10:00:00+00:00",
            "scope": {"class": "11", "subject": "chemistry"},
        },
        {
            "event": "production_write_approved",
            "run_id": "run-second",
            "operator": "second-operator",
            "started_at": "2026-09-14T10:01:00+00:00",
            "scope": {"class": "12", "subject": "physics"},
        },
    )
    _write_jsonl(
        progress,
        {
            "run_id": "run-first",
            "chapter_id": "shared-chapter-1",
            "status": "done",
            "timestamp": "2026-09-14T10:02:00+00:00",
        },
        {
            "run_id": "run-second",
            "chapter_id": "shared-chapter-1",
            "status": "error",
            "timestamp": "2026-09-14T10:03:00+00:00",
        },
        {
            "run_id": "run-first",
            "chapter_id": "shared-chapter-2",
            "status": "error",
            "timestamp": "2026-09-14T10:04:00+00:00",
        },
        {
            "run_id": "run-second",
            "chapter_id": "shared-chapter-2",
            "status": "done",
            "timestamp": "2026-09-14T10:05:00+00:00",
        },
        {
            "event": "import_terminal_summary",
            "run_id": "run-first",
            "status": "failed",
            "chapters": 2,
            "completed": 1,
            "failed": 1,
            "timestamp": "2026-09-14T10:06:00+00:00",
        },
        {
            "event": "import_terminal_summary",
            "run_id": "run-second",
            "status": "completed",
            "chapters": 2,
            "completed": 1,
            "failed": 1,
            "timestamp": "2026-09-14T10:07:00+00:00",
        },
    )

    with (
        patch.object(admin_content, "_AHSEC_D1_APPROVAL_FILE", approvals),
        patch.object(admin_content, "_AHSEC_D1_IMPORT_PROGRESS_FILE", progress),
    ):
        response = client.get(
            "/api/v1/admin/content/ahsec-d1-import/approvals",
            cookies=admin_cookie,
        )

    assert response.status_code == 200, response.text
    assert response.json()["approvals"] == [
        {
            "run_id": "run-second",
            "operator": "second-operator",
            "started_at": "2026-09-14T10:01:00+00:00",
            "scope": {"class": "12", "subject": "physics"},
            "progress": {
                "status": "completed",
                "chapters": 2,
                "completed": 1,
                "failed": 1,
                "last_updated_at": "2026-09-14T10:07:00+00:00",
            },
        },
        {
            "run_id": "run-first",
            "operator": "first-operator",
            "started_at": "2026-09-14T10:00:00+00:00",
            "scope": {"class": "11", "subject": "chemistry"},
            "progress": {
                "status": "failed",
                "chapters": 2,
                "completed": 1,
                "failed": 1,
                "last_updated_at": "2026-09-14T10:06:00+00:00",
            },
        },
    ]


def test_import_approvals_ignore_malformed_progress_lines(
    client, admin_cookie, tmp_path
):
    import app.api.v1.admin_content as admin_content

    run_id = "run-damaged-progress"
    approvals = tmp_path / "approvals.jsonl"
    progress = tmp_path / "progress.jsonl"
    _write_jsonl(
        approvals,
        {
            "event": "production_write_approved",
            "run_id": run_id,
            "operator": "curriculum-reviewer",
            "started_at": "2026-09-14T10:00:00+00:00",
            "scope": {"class": "11", "subject": "chemistry"},
        },
    )
    _write_jsonl(
        progress,
        "malformed progress before valid records",
        {
            "run_id": run_id,
            "chapter_id": "chapter-1",
            "status": "done",
            "timestamp": "2026-09-14T10:01:00+00:00",
        },
        '{"run_id": "incomplete"',
        {
            "run_id": run_id,
            "chapter_id": "chapter-2",
            "status": "error",
            "timestamp": "2026-09-14T10:02:00+00:00",
        },
        {
            "event": "import_terminal_summary",
            "run_id": run_id,
            "status": "failed",
            "chapters": 2,
            "completed": 1,
            "failed": 1,
            "timestamp": "2026-09-14T10:03:00+00:00",
        },
        "malformed progress after valid records",
    )

    with (
        patch.object(admin_content, "_AHSEC_D1_APPROVAL_FILE", approvals),
        patch.object(admin_content, "_AHSEC_D1_IMPORT_PROGRESS_FILE", progress),
    ):
        response = client.get(
            "/api/v1/admin/content/ahsec-d1-import/approvals",
            cookies=admin_cookie,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approvals"] == [
        {
            "run_id": run_id,
            "operator": "curriculum-reviewer",
            "started_at": "2026-09-14T10:00:00+00:00",
            "scope": {"class": "11", "subject": "chemistry"},
            "progress": {
                "status": "failed",
                "chapters": 2,
                "completed": 1,
                "failed": 1,
                "last_updated_at": "2026-09-14T10:03:00+00:00",
            },
        }
    ]


def test_import_approvals_report_pending_run_without_progress(
    client, admin_cookie, tmp_path
):
    import app.api.v1.admin_content as admin_content

    approvals = tmp_path / "approvals.jsonl"
    _write_jsonl(
        approvals,
        {
            "event": "production_write_approved",
            "run_id": "run-pending",
            "operator": "operator",
            "started_at": "2026-09-13T11:00:00+00:00",
            "scope": {"subject": "biology"},
        },
    )

    with patch.object(admin_content, "_AHSEC_D1_APPROVAL_FILE", approvals), patch.object(
        admin_content,
        "_AHSEC_D1_IMPORT_PROGRESS_FILE",
        tmp_path / "missing-progress.jsonl",
    ):
        response = client.get(
            "/api/v1/admin/content/ahsec-d1-import/approvals",
            cookies=admin_cookie,
        )

    assert response.status_code == 200
    assert response.json()["approvals"][0]["progress"] == {
        "status": "approved",
        "chapters": 0,
        "completed": 0,
        "failed": 0,
        "last_updated_at": None,
    }


def test_import_approvals_report_running_run_without_terminal_summary(
    client, admin_cookie, tmp_path
):
    import app.api.v1.admin_content as admin_content

    approvals = tmp_path / "approvals.jsonl"
    progress = tmp_path / "progress.jsonl"
    _write_jsonl(
        approvals,
        {
            "event": "production_write_approved",
            "run_id": "run-running",
            "operator": "operator",
            "started_at": "2026-09-13T12:00:00+00:00",
            "scope": {"subject": "biology"},
        },
    )
    _write_jsonl(
        progress,
        {
            "run_id": "run-running",
            "chapter_id": "chapter-1",
            "status": "done",
            "timestamp": "2026-09-13T12:01:00+00:00",
        },
    )

    with patch.object(admin_content, "_AHSEC_D1_APPROVAL_FILE", approvals), patch.object(
        admin_content,
        "_AHSEC_D1_IMPORT_PROGRESS_FILE",
        progress,
    ):
        response = client.get(
            "/api/v1/admin/content/ahsec-d1-import/approvals",
            cookies=admin_cookie,
        )

    assert response.status_code == 200
    assert response.json()["approvals"][0]["progress"] == {
        "status": "running",
        "chapters": 1,
        "completed": 1,
        "failed": 0,
        "last_updated_at": "2026-09-13T12:01:00+00:00",
    }
