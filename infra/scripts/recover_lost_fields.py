"""
recover_lost_fields.py
======================
One-time recovery script: patches syrabit_prod chapters with fields that were
silently dropped by earlier migration scripts.

Fields recovered (from legacy source DBs):
  - images          (dropped by unify_databases.py before fix)
  - content_as      (dropped by migrate_chapters_to_ko.py before fix)
  - question_papers (dropped by unify_databases.py before fix)

Sources tried in order for each chapter (matched by slug first, then by
normalised title as a fallback — prod slugs gained subject/class prefixes
during migration, so title matching is required for most chapters):
  1. syrabit        (the oldest legacy DB)
  2. test_database  (second legacy source)
  3. syrabit_dev    (third legacy source)

Usage:
    # Preview — no writes
    python3 infra/scripts/recover_lost_fields.py --dry-run

    # Full recovery
    python3 infra/scripts/recover_lost_fields.py

    # Only recover Assamese content
    python3 infra/scripts/recover_lost_fields.py --fields content_as

    # Only specific source DB
    python3 infra/scripts/recover_lost_fields.py --source syrabit
"""

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timezone

from pymongo import MongoClient, UpdateOne

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

NOW = datetime.now(timezone.utc)

ALL_FIELDS = ["images", "content_as", "question_papers"]
SOURCE_DBS = ["syrabit", "test_database", "syrabit_dev"]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Recover lost chapter fields in syrabit_prod"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be patched — no writes to the database",
    )
    parser.add_argument(
        "--fields",
        default=",".join(ALL_FIELDS),
        help=(
            f"Comma-separated list of fields to recover. "
            f"Default: {','.join(ALL_FIELDS)}"
        ),
    )
    parser.add_argument(
        "--source",
        default=None,
        help=(
            "Only use a single source DB instead of all three. "
            f"Options: {', '.join(SOURCE_DBS)}"
        ),
    )
    return parser.parse_args()


def normalize_title(title: str) -> str:
    """Return a lowercase, whitespace-collapsed version for fuzzy matching."""
    if not title:
        return ""
    return re.sub(r"\s+", " ", title.strip().lower())


def main():
    args = parse_args()
    dry_run: bool = args.dry_run
    fields_to_recover: list[str] = [f.strip() for f in args.fields.split(",") if f.strip()]
    sources: list[str] = [args.source] if args.source else SOURCE_DBS

    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        log.error("MONGODB_URI environment variable is not set")
        sys.exit(1)

    mode_label = "DRY RUN — no writes" if dry_run else "LIVE — writing to syrabit_prod"
    log.info("=" * 60)
    log.info(f"Syrabit Field Recovery  [{mode_label}]")
    log.info(f"Fields: {fields_to_recover}")
    log.info(f"Sources: {sources}")
    log.info("=" * 60)

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000)
    client.admin.command("ping")
    log.info("MongoDB connected\n")

    prod_db = client["syrabit_prod"]

    # ── Build slug → source doc index AND title → source doc index ───────────
    # Later sources override earlier ones so the most data-rich wins.
    # Prod slugs were prefixed with subject/class labels during migration, so
    # plain slug matching misses most chapters. Title matching is the fallback.
    log.info("Building source index from legacy DBs...")
    source_by_slug: dict[str, dict] = {}
    # title index maps normalised title → list of candidate dicts (for ambiguity detection)
    source_by_title_candidates: dict[str, list[dict]] = {}

    def _merge(existing: dict, ch: dict) -> dict:
        merged = dict(existing)
        for field in fields_to_recover:
            val = ch.get(field)
            if val:
                merged[field] = val
        return merged

    for db_name in sources:
        src_db = client[db_name]
        try:
            count = src_db["chapters"].count_documents({})
        except Exception as e:
            log.warning(f"  {db_name}: could not access chapters — {e}")
            continue

        log.info(f"  {db_name}: scanning {count} chapters")
        for ch in src_db["chapters"].find(
            {},
            {"slug": 1, "title": 1, "images": 1, "content_as": 1, "question_papers": 1, "_id": 0},
        ):
            # Only index chapters that carry at least one recoverable field value
            has_data = any(ch.get(f) for f in fields_to_recover)
            if not has_data:
                continue

            slug = ch.get("slug")
            title_key = normalize_title(ch.get("title", ""))

            if slug:
                source_by_slug[slug] = _merge(source_by_slug.get(slug, {}), ch)
            if title_key:
                source_by_title_candidates.setdefault(title_key, []).append(ch)

    # Collapse title candidates: keep only unambiguous (single-candidate) entries.
    # If multiple source docs share a normalised title, merging them risks cross-chapter
    # corruption (e.g. "Unit I" from subject A overwriting "Unit I" from subject B).
    source_by_title: dict[str, dict] = {}
    ambiguous_titles: list[str] = []
    for title_key, candidates in source_by_title_candidates.items():
        if len(candidates) == 1:
            source_by_title[title_key] = candidates[0]
        else:
            # Multiple source docs share this title — attempt field-level merge only if
            # every candidate contributes the SAME non-empty value for each field.
            # If values conflict, mark as ambiguous and skip.
            merged: dict = {}
            conflict = False
            for field in fields_to_recover:
                vals = [str(c[field]) for c in candidates if c.get(field)]
                if not vals:
                    continue
                unique_vals = set(vals)
                if len(unique_vals) == 1:
                    merged[field] = candidates[next(i for i, c in enumerate(candidates) if c.get(field))][field]
                else:
                    conflict = True
                    break
            if conflict or not merged:
                ambiguous_titles.append(title_key)
            else:
                source_by_title[title_key] = merged

    log.info(
        f"Source index built: {len(source_by_slug)} unique slugs, "
        f"{len(source_by_title)} unambiguous titles "
        f"({len(ambiguous_titles)} ambiguous titles skipped)\n"
    )
    if ambiguous_titles:
        log.warning(f"Ambiguous titles skipped (conflicting source data):")
        for t in ambiguous_titles[:10]:
            log.warning(f"  {t!r}")
        if len(ambiguous_titles) > 10:
            log.warning(f"  ... and {len(ambiguous_titles) - 10} more")

    # ── Scan syrabit_prod chapters for missing fields ───────────────────────
    log.info("Scanning syrabit_prod for chapters with missing fields...")

    query_conditions = []
    for field in fields_to_recover:
        if field == "images":
            query_conditions.append({field: {"$in": [None, [], ""]}})
        elif field == "content_as":
            query_conditions.append({field: {"$in": [None, ""]}})
        elif field == "question_papers":
            query_conditions.append({field: {"$in": [None, [], ""]}})

    if not query_conditions:
        log.error("No valid fields specified")
        sys.exit(1)

    projection = {"slug": 1, "title": 1}
    for f in fields_to_recover:
        projection[f] = 1

    prod_chapters = list(
        prod_db["chapters"].find(
            {"$or": query_conditions},
            projection,
        )
    )
    log.info(f"Found {len(prod_chapters)} chapters with at least one missing field\n")

    # ── Build patch operations ──────────────────────────────────────────────
    ops: list[UpdateOne] = []
    stats = {f: 0 for f in fields_to_recover}
    stats["chapters_patched"] = 0
    stats["chapters_skipped_no_source"] = 0
    stats["matched_by_slug"] = 0
    stats["matched_by_title"] = 0

    for ch in prod_chapters:
        slug = ch.get("slug", "")
        title_key = normalize_title(ch.get("title", ""))

        # Try slug first, then title as fallback
        src = source_by_slug.get(slug)
        match_method = "slug"
        if not src:
            src = source_by_title.get(title_key)
            match_method = "title"

        if not src:
            stats["chapters_skipped_no_source"] += 1
            log.debug(f"  {slug!r}: no source data found — skipping")
            continue

        patch: dict = {}
        field_log: list[str] = []

        for field in fields_to_recover:
            current_val = ch.get(field)
            is_missing = not current_val or (isinstance(current_val, list) and len(current_val) == 0)
            if not is_missing:
                continue

            src_val = src.get(field)
            if not src_val or (isinstance(src_val, list) and len(src_val) == 0):
                continue

            patch[field] = src_val
            stats[field] += 1
            if field == "images":
                field_log.append(f"images={len(src_val)}")
            elif field == "question_papers":
                field_log.append(f"question_papers={len(src_val)}")
            elif field == "content_as":
                field_log.append(f"content_as({len(src_val)} chars)")

        if not patch:
            continue

        patch["updated_at"] = NOW
        patch["_recovery_applied"] = NOW

        stats[f"matched_by_{match_method}"] += 1

        title_display = ch.get("title", slug)[:52]
        method_tag = f"[{match_method}]"
        if dry_run:
            log.info(f"  [DRY] {method_tag} {title_display:<52s} → {', '.join(field_log)}")
        else:
            ops.append(
                UpdateOne({"_id": ch["_id"]}, {"$set": patch})
            )
            log.info(f"  ✓ {method_tag} {title_display:<52s} → {', '.join(field_log)}")

        stats["chapters_patched"] += 1

    # ── Write updates ───────────────────────────────────────────────────────
    if ops and not dry_run:
        result = prod_db["chapters"].bulk_write(ops, ordered=False)
        log.info(
            f"\nBulk write: {result.modified_count} modified, "
            f"{result.matched_count} matched"
        )

    # ── Summary ─────────────────────────────────────────────────────────────
    log.info("\n" + "=" * 60)
    log.info(f"RECOVERY {'(DRY RUN)' if dry_run else 'COMPLETE'}")
    log.info(f"  Chapters patched           : {stats['chapters_patched']}")
    log.info(f"    matched by slug          : {stats['matched_by_slug']}")
    log.info(f"    matched by title         : {stats['matched_by_title']}")
    log.info(f"  Chapters skipped (no src)  : {stats['chapters_skipped_no_source']}")
    for field in fields_to_recover:
        label = {
            "images": "Images restored         ",
            "content_as": "Assamese notes restored ",
            "question_papers": "PYQ refs restored       ",
        }.get(field, field)
        log.info(f"  {label} : {stats[field]}")
    log.info("=" * 60)

    client.close()


if __name__ == "__main__":
    main()
