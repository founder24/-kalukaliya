#!/usr/bin/env python3
"""Regression test for preserving Mongo Chat titles in D1 saved history."""

import importlib.util
import json
from pathlib import Path


module_path = Path(__file__).with_name("migrate-mongo-to-d1.py")
spec = importlib.util.spec_from_file_location("migrate_mongo_to_d1", module_path)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class Cursor(list):
    def sort(self, *_args):
        return self


class Collection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, *_args, **_kwargs):
        return Cursor(self.docs)


class Database(dict):
    def list_collection_names(self):
        return list(self.keys())


def main() -> None:
    db = Database({
        "chats": Collection([{
            "_id": "chat-1",
            "user_id": "student-1",
            "session_id": "session-1",
            "title": "Custom exam revision plan",
            "created_at": 1_700_000_000,
            "updated_at": 1_700_000_100,
            "messages": [{"role": "user", "content": "Help me revise"}],
        }]),
    })
    captured = {}

    def capture(table, columns, rows, dry_run):
        captured.update(table=table, columns=columns, rows=rows, dry_run=dry_run)
        return len(rows)

    original = migration.insert_batch
    migration.insert_batch = capture
    try:
        assert migration.migrate_conversation_metadata(db, False) == 1
    finally:
        migration.insert_batch = original

    assert captured["table"] == "conversation_metadata"
    assert captured["columns"] == [
        "user_id", "session_id", "title", "starred", "archived", "updated_at",
    ]
    assert captured["rows"] == [[
        "student-1", "session-1", "Custom exam revision plan", 0, 0, 1_700_000_100,
    ]]

    class D1Response:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def mismatched_title_d1(request, timeout):
        sql = json.loads(request.data.decode())["sql"]
        rows = [{"n": 1}] if "COUNT(*)" in sql else [{
            "user_id": "student-1",
            "session_id": "session-1",
            "title": "Incorrect title",
            "updated_at": 1_700_000_100,
        }]
        return D1Response({"success": True, "result": [{"results": rows}]})

    original_urlopen = migration.urllib.request.urlopen
    migration.urllib.request.urlopen = mismatched_title_d1
    try:
        try:
            migration.validate_mongo_d1_parity(db, {"conversation_metadata"}, 1)
            raise AssertionError("title mismatch must fail parity validation")
        except RuntimeError as exc:
            assert "leading IDs differ" in str(exc)
    finally:
        migration.urllib.request.urlopen = original_urlopen

    print("conversation metadata migration mapper passed")


if __name__ == "__main__":
    main()