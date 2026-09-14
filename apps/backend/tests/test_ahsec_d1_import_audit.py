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


@pytest.mark.parametrize(
    ("status", "completed", "failed"),
    [("completed", 3, 0), ("failed", 2, 1)],
)
def test_terminal_summary_is_run_scoped_and_safe(
    monkeypatch, tmp_path, status, completed, failed
):
    progress_file = tmp_path / "progress.jsonl"
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    monkeypatch.setattr(importer, "ACTIVE_RUN_ID", "run-terminal")

    importer.record_terminal_summary(
        status,
        completed=completed,
        failed=failed,
    )

    record = read_jsonl(progress_file)[0]
    assert record == {
        "event": "import_terminal_summary",
        "run_id": "run-terminal",
        "timestamp": record["timestamp"],
        "status": status,
        "chapters": completed + failed,
        "completed": completed,
        "failed": failed,
    }
    assert "CLOUDFLARE_API_TOKEN" not in json.dumps(record)
    assert "notes_en" not in json.dumps(record)
    assert "error" not in record


def test_main_records_failed_terminal_summary_without_exception_payload(
    monkeypatch, tmp_path
):
    progress_file = tmp_path / "progress.jsonl"
    approval_file = tmp_path / "approvals.jsonl"
    args = make_args()
    monkeypatch.setattr(importer, "parse_args", lambda: args)
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    monkeypatch.setattr(importer, "APPROVAL_FILE", approval_file)
    monkeypatch.setattr(importer, "CloudflareClient", lambda: object())
    monkeypatch.setattr(
        importer,
        "fetch_chapters",
        lambda _client: [
            {
                "id": "chapter-1",
                "class_name": "HS 1st Year",
                "subject_slug": "chemistry",
            }
        ],
    )

    async def fail_extract_sources(_args):
        raise RuntimeError("secret-token and backup note contents")

    monkeypatch.setattr(importer, "extract_sources", fail_extract_sources)

    with pytest.raises(RuntimeError, match="secret-token"):
        asyncio.run(importer.main())

    records = read_jsonl(progress_file)
    assert records[-1] == {
        "event": "import_terminal_summary",
        "run_id": records[-1]["run_id"],
        "timestamp": records[-1]["timestamp"],
        "status": "failed",
        "chapters": 0,
        "completed": 0,
        "failed": 0,
    }
    assert "secret-token" not in json.dumps(records[-1])
    assert "backup note contents" not in json.dumps(records[-1])
    assert not (tmp_path / "active-run.json").exists()


def test_main_records_completed_terminal_summary(monkeypatch, tmp_path):
    progress_file = tmp_path / "progress.jsonl"
    approval_file = tmp_path / "approvals.jsonl"
    args = make_args(limit=0)
    monkeypatch.setattr(importer, "parse_args", lambda: args)
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    monkeypatch.setattr(importer, "APPROVAL_FILE", approval_file)
    monkeypatch.setattr(importer, "CloudflareClient", lambda: object())
    monkeypatch.setattr(importer, "fetch_chapters", lambda _client: [])

    async def no_sources(_args):
        return {}

    monkeypatch.setattr(importer, "extract_sources", no_sources)

    assert asyncio.run(importer.main()) == 0

    records = read_jsonl(progress_file)
    assert records[-1]["event"] == "import_terminal_summary"
    assert records[-1]["status"] == "completed"
    assert records[-1]["chapters"] == 0
    assert records[-1]["completed"] == 0
    assert records[-1]["failed"] == 0
    assert not (tmp_path / "active-run.json").exists()


def test_normal_import_approves_and_generates_without_cleanup_preview(
    monkeypatch, tmp_path
):
    progress_file = tmp_path / "progress.jsonl"
    approval_file = tmp_path / "approvals.jsonl"
    backup_file = tmp_path / "notes-backup.jsonl"
    args = make_args(limit=1, delay=0, clean_preambles=False)
    chapter = {
        "id": "chapter-normal",
        "subject_id": "subject-1",
        "class_name": "HS 1st Year",
        "subject_name": "Chemistry",
        "subject_slug": "chemistry",
        "title": "Motion",
        "chapter_number": 1,
        "notes_en": "Existing notes",
        "rag_text": "Existing notes",
        "rag_sections_en": "[]",
    }
    source = {
        "title": "Motion",
        "effective_number": 1,
        "body_text": "Official textbook content " * 30,
        "source_pdf_url": "https://example.test/motion.pdf",
    }
    generated_notes = "## Motion\n\n" + ("Generated study notes. " * 60)

    class FakeClient:
        def __init__(self):
            self.generated = []
            self.executed = []

        def generate(self, system_prompt, user_message, *, chapter_id=None):
            self.generated.append((system_prompt, user_message, chapter_id))
            return generated_notes

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

    client = FakeClient()
    monkeypatch.setattr(importer, "parse_args", lambda: args)
    monkeypatch.setattr(importer, "CloudflareClient", lambda: client)
    monkeypatch.setattr(importer, "fetch_chapters", lambda _client: [chapter])

    async def normal_sources(_args):
        return {("11", "chemistry"): [source]}

    monkeypatch.setattr(importer, "extract_sources", normal_sources)
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    monkeypatch.setattr(importer, "APPROVAL_FILE", approval_file)
    monkeypatch.setattr(importer, "BACKUP_FILE", backup_file)

    assert asyncio.run(importer.main()) == 0

    assert len(client.generated) == 1
    assert client.generated[0][2] == "chapter-normal"
    assert len(client.executed) == 2
    approval = read_jsonl(approval_file)[0]
    assert approval["scope"]["clean_preambles"] is False
    assert "cleanup_preview_fingerprint" not in approval["scope"]
    assert "cleanup_preview_generated_at" not in approval["scope"]
    progress = read_jsonl(progress_file)
    assert any(
        row["chapter_id"] == "chapter-normal" and row["status"] == "done"
        for row in progress
    )
    assert progress[-1]["status"] == "completed"
    assert not (tmp_path / importer.CLEANUP_PREVIEW_FILENAME).exists()


def test_normal_import_replaces_index_and_chunk_mappings_without_cleanup_preview(
    monkeypatch, tmp_path
):
    progress_file = tmp_path / "progress.jsonl"
    approval_file = tmp_path / "approvals.jsonl"
    backup_file = tmp_path / "notes-backup.jsonl"
    args = make_args(
        limit=1,
        delay=0,
        skip_index=False,
        clean_preambles=False,
    )
    chapter = {
        "id": "chapter-indexed",
        "subject_id": "subject-1",
        "class_name": "HS 1st Year",
        "subject_name": "Chemistry",
        "subject_slug": "chemistry",
        "title": "Motion",
        "chapter_number": 1,
        "notes_en": "Existing notes",
        "rag_text": "Existing notes",
        "rag_sections_en": "[]",
    }
    source = {
        "title": "Motion",
        "effective_number": 1,
        "body_text": "Official textbook content " * 30,
        "source_pdf_url": "https://example.test/motion.pdf",
    }
    generated_notes = "## Motion\n\n" + ("Generated study notes. " * 60)

    class FakeClient:
        def __init__(self):
            self.generated = []
            self.executed = []
            self.queried = []
            self.embedded = []
            self.deleted_vectors = []
            self.upserted_vectors = []

        def generate(self, system_prompt, user_message, *, chapter_id=None):
            self.generated.append((system_prompt, user_message, chapter_id))
            return generated_notes

        def embed(self, texts):
            self.embedded.append(texts)
            return [[0.1, 0.2] for _ in texts]

        def query(self, sql, params=None):
            self.queried.append((sql, params))
            if "SELECT vector_id FROM chunks" in sql:
                return [{"vector_id": "old-vector"}]
            return []

        def vector_delete(self, vector_ids):
            self.deleted_vectors.append(vector_ids)

        def vector_upsert(self, vectors):
            self.upserted_vectors.append(vectors)

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

    client = FakeClient()
    monkeypatch.setattr(importer, "parse_args", lambda: args)
    monkeypatch.setattr(importer, "CloudflareClient", lambda: client)
    monkeypatch.setattr(importer, "fetch_chapters", lambda _client: [chapter])

    async def normal_sources(_args):
        return {("11", "chemistry"): [source]}

    monkeypatch.setattr(importer, "extract_sources", normal_sources)
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    monkeypatch.setattr(importer, "APPROVAL_FILE", approval_file)
    monkeypatch.setattr(importer, "BACKUP_FILE", backup_file)

    assert asyncio.run(importer.main()) == 0

    assert len(client.generated) == 1
    assert client.generated[0][2] == "chapter-indexed"
    assert len(client.embedded) == 1
    assert client.embedded[0]
    assert client.deleted_vectors == [["old-vector"]]
    assert len(client.upserted_vectors) == 1
    assert client.upserted_vectors[0][0]["id"] == "chapter-indexed_english_notes_0"
    assert client.upserted_vectors[0][0]["metadata"]["chapterId"] == "chapter-indexed"
    assert any("DELETE FROM chunks" in sql for sql, _params in client.executed)
    assert any("INSERT INTO chunks" in sql for sql, _params in client.executed)
    assert any("rag_indexed_at" in sql for sql, _params in client.executed)

    approval = read_jsonl(approval_file)[0]
    assert approval["scope"]["skip_index"] is False
    assert approval["scope"]["clean_preambles"] is False
    assert "cleanup_preview_fingerprint" not in approval["scope"]
    assert "cleanup_preview_generated_at" not in approval["scope"]
    progress = read_jsonl(progress_file)
    done = next(row for row in progress if row["chapter_id"] == "chapter-indexed")
    assert done["status"] == "done"
    assert done["chunks"] == 1
    assert progress[-1]["status"] == "completed"
    assert not (tmp_path / importer.CLEANUP_PREVIEW_FILENAME).exists()


def test_index_failure_after_notes_write_is_a_distinct_repairable_state(
    monkeypatch, tmp_path
):
    progress_file = tmp_path / "progress.jsonl"
    approval_file = tmp_path / "approvals.jsonl"
    backup_file = tmp_path / "notes-backup.jsonl"
    args = make_args(limit=1, delay=0, skip_index=False)
    chapter = {
        "id": "chapter-index-failed",
        "subject_id": "subject-1",
        "class_name": "HS 1st Year",
        "subject_name": "Chemistry",
        "subject_slug": "chemistry",
        "title": "Motion",
        "chapter_number": 1,
        "notes_en": "Existing notes",
        "rag_text": "Existing notes",
        "rag_sections_en": "[]",
    }
    source = {
        "title": "Motion",
        "effective_number": 1,
        "body_text": "Official textbook content " * 30,
        "source_pdf_url": "https://example.test/motion.pdf",
    }
    generated_notes = "## Motion\n\n" + ("Generated study notes. " * 60)

    class FakeClient:
        def __init__(self):
            self.generated = []
            self.executed = []

        def generate(self, system_prompt, user_message, *, chapter_id=None):
            self.generated.append((system_prompt, user_message, chapter_id))
            return generated_notes

        def embed(self, texts):
            raise RuntimeError("Vectorize unavailable in test")

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

    client = FakeClient()
    monkeypatch.setattr(importer, "parse_args", lambda: args)
    monkeypatch.setattr(importer, "CloudflareClient", lambda: client)
    monkeypatch.setattr(importer, "fetch_chapters", lambda _client: [chapter])

    async def normal_sources(_args):
        return {("11", "chemistry"): [source]}

    monkeypatch.setattr(importer, "extract_sources", normal_sources)
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    monkeypatch.setattr(importer, "APPROVAL_FILE", approval_file)
    monkeypatch.setattr(importer, "BACKUP_FILE", backup_file)

    assert asyncio.run(importer.main()) == 1

    assert len(client.generated) == 1
    assert len(client.executed) == 2
    progress = read_jsonl(progress_file)
    failed = next(
        row for row in progress if row["chapter_id"] == "chapter-index-failed"
    )
    assert failed["status"] == "index_failed"
    assert failed["phase"] == "index"
    assert failed["notes_written"] is True
    assert failed["index_attempt"] == 1
    assert "--repair-index chapter-index-failed" in failed["repair_command"]
    assert not any(
        row.get("chapter_id") == "chapter-index-failed" and row["status"] == "done"
        for row in progress
    )
    assert progress[-1]["status"] == "failed"
    assert progress[-1]["completed"] == 0
    assert progress[-1]["failed"] == 1
    assert "Vectorize unavailable" not in json.dumps(progress[-1])


@pytest.mark.parametrize(
    ("failure", "operation", "expected_events"),
    [
        (
            "vector_delete",
            "vector_delete",
            ["write_notes", "write_rag_document", "embed", "query", "vector_delete"],
        ),
        (
            "vector_upsert",
            "vector_upsert",
            [
                "write_notes",
                "write_rag_document",
                "embed",
                "query",
                "vector_delete",
                "vector_upsert",
            ],
        ),
        (
            "chunk_mapping",
            "chunk_mapping_delete",
            [
                "write_notes",
                "write_rag_document",
                "embed",
                "query",
                "vector_delete",
                "vector_upsert",
                "chunk_mapping_delete",
            ],
        ),
    ],
)
def test_index_replacement_failures_are_audited_and_repairable(
    monkeypatch, tmp_path, failure, operation, expected_events
):
    progress_file = tmp_path / "progress.jsonl"
    approval_file = tmp_path / "approvals.jsonl"
    backup_file = tmp_path / "notes-backup.jsonl"
    args = make_args(limit=1, delay=0, skip_index=False)
    chapter = {
        "id": f"chapter-{failure}",
        "subject_id": "subject-1",
        "class_name": "HS 1st Year",
        "subject_name": "Chemistry",
        "subject_slug": "chemistry",
        "title": "Motion",
        "chapter_number": 1,
        "notes_en": "Existing notes",
        "rag_text": "Existing notes",
        "rag_sections_en": "[]",
    }
    source = {
        "title": "Motion",
        "effective_number": 1,
        "body_text": "Official textbook content " * 30,
        "source_pdf_url": "https://example.test/motion.pdf",
    }
    generated_notes = "## Motion\n\n" + ("Generated study notes. " * 60)

    class FakeClient:
        def __init__(self):
            self.events = []

        def generate(self, system_prompt, user_message, *, chapter_id=None):
            return generated_notes

        def embed(self, texts):
            self.events.append("embed")
            return [[0.1, 0.2] for _ in texts]

        def query(self, sql, params=None):
            self.events.append("query")
            if "SELECT vector_id FROM chunks" in sql:
                return [{"vector_id": "old-vector"}]
            return []

        def vector_delete(self, vector_ids):
            self.events.append("vector_delete")
            if failure == "vector_delete":
                raise RuntimeError("delete unavailable")

        def vector_upsert(self, vectors):
            self.events.append("vector_upsert")
            if failure == "vector_upsert":
                raise RuntimeError("upsert unavailable")

        def execute(self, sql, params=None):
            if "UPDATE chapters" in sql:
                self.events.append("write_notes")
            elif "INSERT INTO rag_documents" in sql:
                self.events.append("write_rag_document")
            elif "DELETE FROM chunks" in sql:
                self.events.append("chunk_mapping_delete")
                if failure == "chunk_mapping":
                    raise RuntimeError("chunk mapping unavailable")

    client = FakeClient()
    monkeypatch.setattr(importer, "parse_args", lambda: args)
    monkeypatch.setattr(importer, "CloudflareClient", lambda: client)
    monkeypatch.setattr(importer, "fetch_chapters", lambda _client: [chapter])

    async def normal_sources(_args):
        return {("11", "chemistry"): [source]}

    monkeypatch.setattr(importer, "extract_sources", normal_sources)
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    monkeypatch.setattr(importer, "APPROVAL_FILE", approval_file)
    monkeypatch.setattr(importer, "BACKUP_FILE", backup_file)

    assert asyncio.run(importer.main()) == 1
    assert client.events == expected_events

    records = read_jsonl(progress_file)
    failed = next(
        row for row in records if row.get("chapter_id") == chapter["id"]
    )
    assert failed["status"] == importer.INDEX_FAILED_STATUS
    assert failed["operation"] == operation
    assert failed["notes_written"] is True
    assert failed["repairable"] is True
    assert "unavailable" in failed["error"]
    assert failed["index_attempt"] == 1
    assert f"--repair-index {chapter['id']}" in failed["repair_command"]
    assert records[-1]["event"] == importer.TERMINAL_SUMMARY_EVENT
    assert records[-1]["status"] == "failed"
    assert records[-1]["chapters"] == 1
    assert records[-1]["completed"] == 0
    assert records[-1]["failed"] == 1
    assert not any(
        row.get("chapter_id") == chapter["id"] and row["status"] == "done"
        for row in records
    )
    assert chapter["id"] not in importer.load_done()
    assert not (tmp_path / "active-run.json").exists()


def test_index_repair_reuses_stored_notes_without_regeneration(
    monkeypatch, tmp_path
):
    progress_file = tmp_path / "progress.jsonl"
    approval_file = tmp_path / "approvals.jsonl"
    backup_file = tmp_path / "notes-backup.jsonl"
    args = make_args(
        limit=None,
        delay=0,
        skip_index=False,
        repair_index=["chapter-index-failed"],
    )
    chapter = {
        "id": "chapter-index-failed",
        "subject_id": "subject-1",
        "class_name": "HS 1st Year",
        "subject_name": "Chemistry",
        "subject_slug": "chemistry",
        "title": "Motion",
        "chapter_number": 1,
        "notes_en": "## Motion\n\n" + ("Stored notes. " * 60),
        "rag_text": "Stored notes",
        "rag_sections_en": "[]",
    }

    class FakeClient:
        def __init__(self):
            self.generated = []
            self.executed = []
            self.embedded = []
            self.deleted_vectors = []
            self.upserted_vectors = []

        def generate(self, *args, **kwargs):
            self.generated.append((args, kwargs))
            raise AssertionError("index repair must not regenerate notes")

        def embed(self, texts):
            self.embedded.append(texts)
            return [[0.1, 0.2] for _ in texts]

        def query(self, sql, params=None):
            if "SELECT vector_id FROM chunks" in sql:
                return [{"vector_id": "old-vector"}]
            return []

        def vector_delete(self, vector_ids):
            self.deleted_vectors.append(vector_ids)

        def vector_upsert(self, vectors):
            self.upserted_vectors.append(vectors)

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

    client = FakeClient()
    monkeypatch.setattr(importer, "parse_args", lambda: args)
    monkeypatch.setattr(importer, "CloudflareClient", lambda: client)
    monkeypatch.setattr(importer, "fetch_chapters", lambda _client: [chapter])
    monkeypatch.setattr(
        importer,
        "extract_sources",
        lambda _args: (_ for _ in ()).throw(
            AssertionError("index repair must not extract source PDFs")
        ),
    )
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    monkeypatch.setattr(importer, "APPROVAL_FILE", approval_file)
    monkeypatch.setattr(importer, "BACKUP_FILE", backup_file)
    progress_file.write_text(
        json.dumps(
            {
                "chapter_id": "chapter-index-failed",
                "status": "index_failed",
                "index_attempt": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert asyncio.run(importer.main()) == 0

    assert not client.generated
    assert client.embedded
    assert client.deleted_vectors == [["old-vector"]]
    progress = read_jsonl(progress_file)
    repaired = [
        row
        for row in progress
        if row.get("chapter_id") == "chapter-index-failed"
    ][-1]
    assert repaired["status"] == importer.INDEX_REPAIRED_STATUS
    assert repaired["operation"] == "index_repair"
    assert repaired["index_attempt"] == 2
    assert progress[-1]["status"] == "completed"
    approval = read_jsonl(approval_file)[0]
    assert approval["scope"]["repair_index"] == ["chapter-index-failed"]
    assert importer.load_done() == {"chapter-index-failed"}


def test_latest_index_failure_overrides_an_earlier_done_record(monkeypatch, tmp_path):
    progress_file = tmp_path / "progress.jsonl"
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    progress_file.write_text(
        "\n".join(
            [
                json.dumps({"chapter_id": "chapter-1", "status": "done"}),
                json.dumps(
                    {
                        "chapter_id": "chapter-1",
                        "status": "index_failed",
                        "index_attempt": 1,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert importer.load_done() == set()


def test_index_repair_stops_after_bounded_attempts(monkeypatch, tmp_path):
    progress_file = tmp_path / "progress.jsonl"
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    monkeypatch.setattr(importer, "PROGRESS_FILE", progress_file)
    monkeypatch.setattr(importer, "ACTIVE_RUN_ID", "run-repair-limit")
    progress_file.write_text(
        json.dumps(
            {
                "chapter_id": "chapter-1",
                "status": importer.INDEX_FAILED_STATUS,
                "index_attempt": importer.MAX_INDEX_REPAIR_ATTEMPTS,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    class NoIndexClient:
        def embed(self, _texts):
            raise AssertionError("repair limit should prevent indexing")

    chapter = {
        "id": "chapter-1",
        "notes_en": "## Motion\n\nStored notes",
    }

    assert (
        asyncio.run(
            importer.repair_indexes(NoIndexClient(), [chapter], ["chapter-1"])
        )
        == 1
    )
    records = read_jsonl(progress_file)
    assert records[-2]["status"] == importer.INDEX_FAILED_STATUS
    assert records[-2]["index_attempt"] == importer.MAX_INDEX_REPAIR_ATTEMPTS + 1
    assert records[-1]["status"] == "failed"


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
    preview_path = importer.write_cleanup_preview_report(
        planned,
        report_path=tmp_path / "cleanup-preview.json",
    )
    preview = json.loads(preview_path.read_text())

    class FakeClient:
        def __init__(self):
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

    client = FakeClient()
    affected = importer.apply_preamble_cleanup(
        client,
        planned,
        preview=preview,
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
    chapter = cleanup_chapter()
    planned = importer.build_preamble_cleanup_plan([chapter])

    report_path = importer.write_cleanup_preview_report(planned)
    report = json.loads(report_path.read_text())

    assert report["mode"] == "preview"
    assert report["chapter_ids"] == ["chapter-1"]
    assert report["changes"][0]["removed_chars"] > 0
    assert "preview" in report["changes"][0]
    assert report["changes"][0]["notes_en_digest"] == importer.cleanup_note_digest(
        chapter["notes_en"]
    )
    assert report["scope_fingerprint"]


def test_cleanup_preview_can_be_transferred_to_another_runner(
    monkeypatch, tmp_path
):
    preview_runner_state = tmp_path / "preview-runner"
    apply_runner_state = tmp_path / "apply-runner"
    transferred_report = tmp_path / "artifact-download" / "cleanup-preview.json"
    args = make_args(clean_preambles=True)
    planned = importer.build_preamble_cleanup_plan([cleanup_chapter()])
    scope = importer.cleanup_preview_scope(args, ["chapter-1"])

    monkeypatch.setattr(importer, "STATE_DIR", preview_runner_state)
    report_path = importer.write_cleanup_preview_report(
        planned,
        scope,
        report_path=tmp_path / "artifact-upload" / "cleanup-preview.json",
    )
    original_report = json.loads(report_path.read_text(encoding="utf-8"))
    transferred_report.parent.mkdir(parents=True)
    transferred_report.write_bytes(report_path.read_bytes())

    monkeypatch.setattr(importer, "STATE_DIR", apply_runner_state)
    apply_args = make_args(
        clean_preambles=True,
        cleanup_preview_report=transferred_report,
    )
    validated = importer.validate_cleanup_preview(
        apply_args,
        ["chapter-1"],
        report_path=importer.cleanup_preview_path(apply_args),
        planned=planned,
    )

    assert validated["scope_fingerprint"] == original_report["scope_fingerprint"]
    assert validated["generated_at"] == original_report["generated_at"]
    assert not (apply_runner_state / importer.CLEANUP_PREVIEW_FILENAME).exists()


def test_cleanup_preview_rejects_missing_reviewed_change(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    args = make_args(clean_preambles=True)
    planned = importer.build_preamble_cleanup_plan([cleanup_chapter()])
    scope = importer.cleanup_preview_scope(args, ["chapter-1"])
    report_path = importer.write_cleanup_preview_report(planned, scope)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["changes"] = []
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(RuntimeError, match="does not match"):
        importer.validate_cleanup_preview(
            args,
            ["chapter-1"],
            report_path=report_path,
        )


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
        planned=planned,
    )["scope_fingerprint"] == importer.cleanup_preview_fingerprint(scope)

    with pytest.raises(RuntimeError, match="does not match"):
        importer.validate_cleanup_preview(
            make_args(clean_preambles=True, subject="physics"),
            ["chapter-1"],
            report_path=report_path,
            planned=planned,
        )
    with pytest.raises(RuntimeError, match="does not match"):
        importer.validate_cleanup_preview(
            args,
            ["chapter-2"],
            report_path=report_path,
            planned=planned,
        )


def test_cleanup_preview_rejects_changed_note_content(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(importer, "STATE_DIR", tmp_path)
    args = make_args(clean_preambles=True)
    original_planned = importer.build_preamble_cleanup_plan([cleanup_chapter()])
    scope = importer.cleanup_preview_scope(args, ["chapter-1"])
    report_path = importer.write_cleanup_preview_report(
        original_planned,
        scope,
    )

    changed_notes = cleanup_chapter()["notes_en"].replace(
        "Body content long enough",
        "Changed body content long enough",
    )
    changed_planned = importer.build_preamble_cleanup_plan(
        [cleanup_chapter(notes=changed_notes)]
    )

    with pytest.raises(RuntimeError, match="note content"):
        importer.validate_cleanup_preview(
            args,
            ["chapter-1"],
            report_path=report_path,
            planned=changed_planned,
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
