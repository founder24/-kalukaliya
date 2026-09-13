import argparse
import asyncio
import json

import pytest

from scripts import ahsec_d1_import as importer


def make_args(**overrides):
    values = {
        "dry_run": False,
        "confirm_production_write": True,
        "operator": "curriculum-reviewer",
        "class_level": "11",
        "subject": "chemistry",
        "limit": 2,
        "restart": False,
        "skip_index": True,
        "clean_preambles": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_production_approval_records_scope_without_credentials(monkeypatch, tmp_path):
    approval_file = tmp_path / "approvals.jsonl"
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "APPROVAL_FILE", approval_file)

    run_id, started_at = importer.record_production_approval(make_args())

    record = read_jsonl(approval_file)[0]
    assert record == {
        "event": "production_write_approved",
        "run_id": run_id,
        "operator": "curriculum-reviewer",
        "started_at": started_at,
        "approved_at": started_at,
        "scope": {
            "class": "11",
            "subject": "chemistry",
            "limit": 2,
            "restart": False,
            "skip_index": True,
            "clean_preambles": False,
        },
    }
    assert "CLOUDFLARE_API_TOKEN" not in json.dumps(record)
    assert "CLOUDFLARE_ACCOUNT_ID" not in json.dumps(record)


@pytest.mark.parametrize(
    "overrides",
    [{"dry_run": True}, {"confirm_production_write": False}],
)
def test_approval_requires_explicit_production_confirmation(
    monkeypatch, tmp_path, overrides
):
    approval_file = tmp_path / "approvals.jsonl"
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "APPROVAL_FILE", approval_file)

    with pytest.raises(RuntimeError, match="explicit production-write confirmation"):
        importer.record_production_approval(make_args(**overrides))

    assert not approval_file.exists()


def test_progress_and_backup_records_share_approval_run_id(monkeypatch, tmp_path):
    approval_file = tmp_path / "approvals.jsonl"
    progress_file = tmp_path / "progress.jsonl"
    backup_file = tmp_path / "notes-backup.jsonl"
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "APPROVAL_FILE", approval_file)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    monkeypatch.setattr(importer, "BACKUP_FILE", backup_file)

    run_id, _ = importer.record_production_approval(make_args())
    monkeypatch.setattr(importer, "ACTIVE_RUN_ID", run_id)
    importer.record_progress("chapter-1", "done")
    importer.backup_existing(
        {"id": "chapter-1", "subject_id": "subject-1", "notes_en": "old"},
        "https://example.test/book.pdf",
    )

    assert read_jsonl(progress_file)[0]["run_id"] == run_id
    assert read_jsonl(backup_file)[0]["run_id"] == run_id


def cleanup_chapter(
    notes=(
        "Here are comprehensive study notes for the chapter Motion.\n\n"
        "## Motion\n\nBody content long enough for section extraction."
    )
):
    return {
        "id": "chapter-1",
        "subject_id": "subject-1",
        "class_name": "HS 1st Year",
        "subject_name": "Physics",
        "subject_slug": "physics",
        "title": "Motion",
        "notes_en": notes,
        "rag_text": notes,
        "rag_sections_en": '[{"heading":"Motion"}]',
    }


def test_preamble_cleanup_plan_is_read_only_and_contains_bounded_diff():
    chapter = cleanup_chapter()
    planned = importer.build_preamble_cleanup_plan([chapter])

    assert len(planned) == 1
    assert planned[0]["id"] == "chapter-1"
    assert planned[0]["notes_en"].startswith("## Motion")
    assert planned[0]["_cleanup_diff"]["removed_chars"] > 0
    assert "-Here are comprehensive study notes for the chapter Motion." in (
        planned[0]["_cleanup_diff"]["preview"]
    )
    assert chapter["notes_en"].startswith("Here are comprehensive")


def test_cleanup_apply_uses_plan_and_backs_up_original_note(monkeypatch, tmp_path):
    backup_file = tmp_path / "notes-backup.jsonl"
    monkeypatch.setattr(importer, "BACKUP_FILE", backup_file)
    chapter = cleanup_chapter()
    planned = importer.build_preamble_cleanup_plan([chapter])

    class FakeClient:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

    client = FakeClient()
    affected = importer.apply_preamble_cleanup(client, planned)

    assert [row["id"] for row in affected] == ["chapter-1"]
    assert len(client.executed) == 2
    backup = read_jsonl(backup_file)[0]
    assert backup["notes_en"] == chapter["notes_en"]
    assert backup["chapter_id"] == "chapter-1"


def test_cleanup_preview_report_contains_ids_and_diff_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    planned = importer.build_preamble_cleanup_plan([cleanup_chapter()])

    report_path = importer.write_cleanup_preview_report(planned)
    report = json.loads(report_path.read_text())

    assert report["mode"] == "preview"
    assert report["chapter_ids"] == ["chapter-1"]
    assert report["changes"][0]["removed_chars"] > 0
    assert "preview" in report["changes"][0]


def test_cleanup_dry_run_does_not_write_d1(monkeypatch, tmp_path):
    class FakeClient:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

    client = FakeClient()
    args = make_args(
        dry_run=True,
        confirm_production_write=False,
        clean_preambles=True,
        class_level=None,
        subject=None,
    )
    monkeypatch.setattr(importer, "parse_args", lambda: args)
    monkeypatch.setattr(importer, "CloudflareClient", lambda: client)
    monkeypatch.setattr(
        importer, "fetch_chapters", lambda _client: [cleanup_chapter()]
    )
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)

    assert asyncio.run(importer.main()) == 0
    assert client.executed == []
    report = json.loads((tmp_path / "preamble-cleanup-preview.json").read_text())
    assert report["chapter_ids"] == ["chapter-1"]


def test_cleanup_apply_requires_production_confirmation(monkeypatch):
    args = make_args(
        dry_run=False,
        confirm_production_write=False,
        clean_preambles=True,
    )
    monkeypatch.setattr(importer, "parse_args", lambda: args)

    assert asyncio.run(importer.main()) == 2