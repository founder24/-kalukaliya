#!/usr/bin/env python3
"""
Pre-flight Gate 2.1.a — `session_id` shape check (Task #363, §2.1.a).

Samples N=1000 documents from the `sessions` collection (or
`conversations` if `sessions` is absent) and asserts that `session_id`
either:
  (a) embeds `user_id` (e.g. matches `^[a-zA-Z0-9]+_[a-zA-Z0-9]+$`
      with the prefix segment present in `user_profile`), OR
  (b) is always co-queried with `user_id` (verified separately by the
      cross-shard transaction audit gate, §2.1.c).

If >5% of sampled session_id values are bare-UUID and not co-queried
with user_id, exits non-zero and prints the legacy session-ID
contingency reminder.

Exit codes:
  0  — gate passed
  2  — gate failed (>5% bare-UUID)
  3  — could not connect to Mongo
  4  — sampling produced 0 docs (Mongo schema may have changed)

Usage:
  MONGO_URL=... python3 scripts/preflight/session_id_shape_check.py
  MONGO_URL=... python3 scripts/preflight/session_id_shape_check.py \
      --sample-size 5000 --threshold-pct 1.0
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Iterable

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
EMBEDDED_RE = re.compile(r"^[A-Za-z0-9]+_[A-Za-z0-9]+$")


def _classify(session_id: str) -> str:
    if not isinstance(session_id, str) or not session_id:
        return "missing"
    if EMBEDDED_RE.match(session_id):
        return "embedded"
    if UUID_RE.match(session_id):
        return "bare_uuid"
    if "_" in session_id:
        return "embedded"
    return "other"


def _sample(coll, sample_size: int) -> Iterable[dict]:
    try:
        yield from coll.aggregate(
            [{"$sample": {"size": sample_size}}],
            allowDiskUse=False,
        )
    except Exception:
        yield from coll.find({}, {"session_id": 1, "user_id": 1}).limit(sample_size)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-size", type=int, default=1000)
    ap.add_argument("--threshold-pct", type=float, default=5.0,
                    help="Fail if >X%% are bare-UUID without co-stored user_id.")
    ap.add_argument("--db", default="syrabit")
    args = ap.parse_args()

    url = os.environ.get("MONGO_URL")
    if not url:
        print("ERROR: MONGO_URL not set", file=sys.stderr)
        return 3

    try:
        from pymongo import MongoClient
    except ImportError:
        print("ERROR: pymongo not installed", file=sys.stderr)
        return 3

    try:
        client = MongoClient(url, serverSelectionTimeoutMS=8000)
        client.admin.command("ping")
    except Exception as e:
        print(f"ERROR: cannot connect to Mongo: {e}", file=sys.stderr)
        return 3

    db = client[args.db]
    cols = set(db.list_collection_names())
    coll_name = "sessions" if "sessions" in cols else (
        "conversations" if "conversations" in cols else None
    )
    if coll_name is None:
        print(f"WARN: neither 'sessions' nor 'conversations' present in db={args.db}; "
              f"nothing to verify, treating as PASS (no shard-key risk yet).")
        return 0

    counts = {"embedded": 0, "bare_uuid": 0, "other": 0, "missing": 0}
    bare_uuid_with_user = 0
    bare_uuid_without_user = 0
    total = 0
    for doc in _sample(db[coll_name], args.sample_size):
        total += 1
        sid = doc.get("session_id")
        bucket = _classify(sid)
        counts[bucket] += 1
        if bucket == "bare_uuid":
            if doc.get("user_id"):
                bare_uuid_with_user += 1
            else:
                bare_uuid_without_user += 1

    if total == 0:
        print(f"ERROR: sampled 0 documents from {coll_name}", file=sys.stderr)
        return 4

    pct_risk = 100.0 * bare_uuid_without_user / total
    print(f"Sampled {total} docs from db={args.db} coll={coll_name}")
    print(f"  embedded               : {counts['embedded']:>6}  "
          f"({100.0 * counts['embedded'] / total:5.2f}%)")
    print(f"  bare_uuid + user_id    : {bare_uuid_with_user:>6}  "
          f"({100.0 * bare_uuid_with_user / total:5.2f}%)  "
          f"-> safe (co-queryable)")
    print(f"  bare_uuid - user_id    : {bare_uuid_without_user:>6}  "
          f"({pct_risk:5.2f}%)  "
          f"-> RISK (would scatter-gather under sharding)")
    print(f"  other                  : {counts['other']:>6}")
    print(f"  missing                : {counts['missing']:>6}")

    if pct_risk > args.threshold_pct:
        print()
        print(f"FAIL: {pct_risk:.2f}% bare-UUID session_id without user_id "
              f"exceeds threshold {args.threshold_pct}%.")
        print()
        print("Apply the legacy session-ID contingency from "
              "infra/capacity-roadmap-363.md §2.1.b BEFORE provisioning the")
        print("sharded cluster:")
        print("  1. Change forward minting to {user_id-prefix}_{ulid} shape.")
        print("  2. Backfill `user_id` onto every legacy conversations doc.")
        print("  3. Set user_id = 'legacy:' + session_id for orphan rows.")
        print("  4. Verify db.conversations.countDocuments("
              "{user_id: {$exists: false}}) == 0.")
        return 2

    print(f"PASS: bare-UUID risk {pct_risk:.2f}% within threshold "
          f"{args.threshold_pct}%.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
