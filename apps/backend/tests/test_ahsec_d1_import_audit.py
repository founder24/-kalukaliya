import argparse
import asyncio
import json
from datetime import timedelta
from datetime import datetime, timedelta, timezone

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
    assert json.loads((tmp_path / "active-run.json").read_text())["run_id"] == run_id

def approval_record(run_id, started_at):
    return {
        "event": "production_write_approved",
        "run_id": run_id,
        "operator": "curriculum-reviewer",
        "started_at": started_at,
        "approved_at": started_at,
        "scope": {"subject": "chemistry"},
    }
@pytest.mark.parametrize(
    "preamble",
    [
        "Here are comprehensive study notes for the chapter Motion.",
        "Below are detailed study notes for Motion.",
        "Certainly! Here are the notes for this chapter.",
        "As an AI language model, I will provide study notes.",
        "I will provide complete study notes for the chapter.",
        "These notes cover the key concepts from the chapter.",
    ],
)
def test_generated_note_preamble_fixtures_are_rejected_with_bounded_diff(preamble):
    raw = f"{preamble}\n\n## Motion\n\nBody content."

    with pytest.raises(importer.ModelPreambleError) as exc_info:
        importer.validate_generated_notes("chapter-42", raw)

    message = str(exc_info.value)
    assert "chapter-42" in message
    assert "bounded_diff:" in message
    assert len(message) < 1800


def test_generated_note_validation_accepts_notes_without_preamble():
    importer.validate_generated_notes(
        "chapter-42",
        "## Motion\n\nBody content starts directly with the chapter notes.",
    )


def test_client_rejects_preamble_before_cleaning_without_credentials(monkeypatch):
    client = importer.CloudflareClient.__new__(importer.CloudflareClient)
    client.api = "https://example.test"
    responses = iter(
        [
            {
                "result": {
                    "response": (
                        "Here are comprehensive study notes for the chapter Motion.\n\n"
                        "## Motion\n\nBody content long enough for validation."
                    )
                }
            }
        ]
    )
    monkeypatch.setattr(client, "_post", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(importer.ModelPreambleError, match="chapter-42"):
        client.generate("system", "prompt", chapter_id="chapter-42")


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
    affected = importer.apply_preamble_cleanup(
        client,
        planned,
        preview={"scope_fingerprint": "test-preview"},
    )

    assert [row["id"] for row in affected] == ["chapter-1"]
    assert len(client.executed) == 2
    backup = read_jsonl(backup_file)[0]
    assert backup["notes_en"] == chapter["notes_en"]
    assert backup["chapter_id"] == "chapter-1"


def test_cleanup_apply_requires_preview_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(importer, "BACKUP_FILE", tmp_path / "notes-backup.jsonl")
    chapter = cleanup_chapter()
    planned = importer.build_preamble_cleanup_plan([chapter])

    class FakeClient:
        def execute(self, sql, params=None):
            raise AssertionError("D1 must not be written without preview evidence")

    with pytest.raises(RuntimeError, match="matching preview artifact"):
        importer.apply_preamble_cleanup(FakeClient(), planned)


def test_cleanup_preview_report_contains_ids_and_diff_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    planned = importer.build_preamble_cleanup_plan([cleanup_chapter()])

    report_path = importer.write_cleanup_preview_report(planned)
    report = json.loads(report_path.read_text())

    assert report["mode"] == "preview"
    assert report["chapter_ids"] == ["chapter-1"]
    assert report["changes"][0]["removed_chars"] > 0
    assert "preview" in report["changes"][0]
    assert report["scope_fingerprint"]


def test_cleanup_preview_must_match_filters_and_chapter_set(monkeypatch, tmp_path):
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    args = make_args(clean_preambles=True)
    planned = importer.build_preamble_cleanup_plan([cleanup_chapter()])
    scope = importer.cleanup_preview_scope(args, ["chapter-1"])
    report_path = importer.write_cleanup_preview_report(planned, scope)

    assert importer.validate_cleanup_preview(
        args,
        ["chapter-1"],
        report_path=report_path,
    )["scope_fingerprint"] == importer.cleanup_preview_fingerprint(scope)

    with pytest.raises(RuntimeError, match="does not match"):
        importer.validate_cleanup_preview(
            make_args(clean_preambles=True, subject="physics"),
            ["chapter-1"],
            report_path=report_path,
        )
    with pytest.raises(RuntimeError, match="does not match"):
        importer.validate_cleanup_preview(
            args,
            ["chapter-2"],
            report_path=report_path,
        )


def test_cleanup_preview_must_be_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    args = make_args(clean_preambles=True)
    planned = importer.build_preamble_cleanup_plan([cleanup_chapter()])
    scope = importer.cleanup_preview_scope(args, ["chapter-1"])
    report_path = importer.write_cleanup_preview_report(planned, scope)
    generated_at = json.loads(report_path.read_text())["generated_at"]
    generated = importer.datetime.fromisoformat(generated_at)

    with pytest.raises(RuntimeError, match="stale"):
        importer.validate_cleanup_preview(
            args,
            ["chapter-1"],
            report_path=report_path,
            now=generated + timedelta(seconds=importer.CLEANUP_PREVIEW_MAX_AGE_SECONDS + 1),
        )


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

def test_archive_history_keeps_active_run_and_correlated_ledgers(
    monkeypatch, tmp_path
):
    now = datetime.now(timezone.utc)
    old_started = (now - timedelta(days=120)).isoformat()
    recent_started = (now - timedelta(days=2)).isoformat()
    old_run = "run-old"
    active_run = "run-active"
    recent_run = "run-recent"
    approval_file = tmp_path / "approvals.jsonl"
    progress_file = tmp_path / "progress.jsonl"
    backup_file = tmp_path / "notes-backup.jsonl"
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "APPROVAL_FILE", approval_file)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    monkeypatch.setattr(importer, "BACKUP_FILE", backup_file)

    approval_file.write_text(
        "\n".join(
            [
                json.dumps(approval_record(old_run, old_started)),
                json.dumps(approval_record(active_run, old_started)),
                json.dumps(approval_record(recent_run, recent_started)),
            ]
        )
        + "\nnot-json\n",
        encoding="utf-8",
    )
    progress_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "run_id": old_run,
                        "chapter_id": "chapter-old",
                        "status": "done",
                        "timestamp": old_started,
                    }
                ),
                json.dumps(
                    {
                        "run_id": active_run,
                        "chapter_id": "chapter-active",
                        "status": "done",
                        "timestamp": old_started,
                    }
                ),
                json.dumps(
                    {
                        "run_id": recent_run,
                        "chapter_id": "chapter-recent",
                        "status": "done",
                        "timestamp": recent_started,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    backup_file.write_text(
        json.dumps({"run_id": old_run, "chapter_id": "chapter-old"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "active-run.json").write_text(
        json.dumps({"run_id": active_run, "started_at": old_started}) + "\n",
        encoding="utf-8",
    )

    preview = importer.archive_history(90, dry_run=True)
    assert preview["archivable_runs"] == 1
    assert preview["protected_runs"] == [active_run]
    assert preview["records"] == {
        "approvals": 1,
        "progress": 1,
        "notes-backup": 1,
    }
    assert "run-old" in approval_file.read_text(encoding="utf-8")

    result = importer.archive_history(90)
    archive_dir = tmp_path / "archive" / result["archive_dir"].split("/")[-1]
    assert json.loads((archive_dir / "manifest.json").read_text())["run_ids"] == [
        old_run
    ]
    for name in ("approvals.jsonl", "progress.jsonl", "notes-backup.jsonl"):
        assert json.loads((archive_dir / name).read_text())["run_id"] == old_run

    live_approval_text = approval_file.read_text(encoding="utf-8")
    assert old_run not in live_approval_text
    assert active_run in live_approval_text
    assert recent_run in live_approval_text
    assert "not-json" in live_approval_text
    assert active_run in (tmp_path / "active-run.json").read_text(encoding="utf-8")
    assert "chapter-old" in importer.load_done()
