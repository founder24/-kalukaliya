#!/usr/bin/env python3
"""
migrate-mongo-to-d1.py — One-shot migration from MongoDB Atlas to Cloudflare D1

Reads all relevant collections from MongoDB (Beanie Document schemas) and writes
them to the D1 database via the Cloudflare REST API in idempotent batches.

Prerequisites:
  pip install pymongo python-dotenv

Usage:
  export MONGODB_URL="mongodb+srv://..."
  export CF_API_TOKEN="..."          # needs D1:edit permission
  export CF_ACCOUNT_ID="..."
  export D1_DATABASE_ID="..."        # from: wrangler d1 list
  python3 scripts/migrate-mongo-to-d1.py [--dry-run] [--collections users,chapters,...]

Safety:
  - All inserts use INSERT OR IGNORE — safe to re-run after partial failure
  - --dry-run prints row counts without writing anything
  - Collections migrated in dependency order (boards before classes before streams ...)

Skipped (already on Cloudflare or not applicable in D1):
  - topic_embeddings  → Vectorize index (no re-embed needed)
  - question_papers   → R2 PDFs; metadata kept in subjects.pyq_papers JSON field
  - knowledge_objects → Legacy render model, superseded by chapters table
  - content_nodes     → Intermediate render model, not part of D1 schema
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    import urllib.request
    import urllib.error
except ImportError:
    pass

try:
    from pymongo import MongoClient
    from bson import ObjectId
except ImportError:
    print("❌  pymongo not installed. Run: pip install pymongo")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

MONGODB_URL    = os.environ.get("MONGODB_URL", "")
CF_API_TOKEN   = os.environ.get("CF_API_TOKEN", "") or os.environ.get("CLOUDFLARE_API_TOKEN", "")
CF_ACCOUNT_ID  = os.environ.get("CF_ACCOUNT_ID", "") or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
D1_DATABASE_ID = os.environ.get("D1_DATABASE_ID", "ff8e76ec-02c5-45f3-92ea-4d67d7d2a510")  # syrabit-db
MONGO_DB_NAME  = os.environ.get("MONGO_DB_NAME", "syrabit_prod")

BATCH_SIZE          = 10     # rows per multi-row INSERT statement (keeps request body < 200 KB)
REQUEST_DELAY       = 0.05   # seconds between D1 API calls
PARALLEL_WORKERS    = 8      # threads for tables with large text content

# Tables where a single row may contain very large text fields (100 KB+).
# These tables use bound-param single-row INSERTs in parallel threads instead
# of multi-row inline-literal INSERTs to avoid SQLITE_TOOBIG SQL length errors.
LARGE_CONTENT_TABLES = {"chapters"}


def die(msg: str) -> None:
    print(f"❌  {msg}", file=sys.stderr)
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Type converters: MongoDB → D1 (SQLite)
# ─────────────────────────────────────────────────────────────────────────────

def to_str(val: Any) -> str | None:
    """Convert ObjectId or any value to string."""
    if val is None:
        return None
    if isinstance(val, ObjectId):
        return str(val)
    return str(val)


def to_int(val: Any) -> int | None:
    """Convert datetime or numeric to Unix epoch integer."""
    if val is None:
        return None
    if isinstance(val, datetime):
        dt = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    if isinstance(val, (int, float)):
        return int(val)
    return None


def to_json(val: Any, default: str = "[]") -> str:
    """Serialize list/dict to JSON string; return default for None."""
    if val is None:
        return default
    if isinstance(val, str):
        return val
    try:
        return json.dumps(val, default=str, ensure_ascii=False)
    except Exception:
        return default


def to_bool_int(val: Any, default: bool = False) -> int:
    if val is None:
        return 1 if default else 0
    return 1 if val else 0


def doc_id(doc: dict) -> str:
    """Extract _id as a plain string regardless of type."""
    raw = doc.get("_id")
    if raw is None:
        return str(uuid.uuid4())
    return str(raw)


def slugify(name: str) -> str:
    """Derive a URL-safe slug from a human-readable name."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"^-+|-+$", "", s)
    return s or "unknown"


def collection_exists(db, name: str) -> bool:
    return name in db.list_collection_names()


# ─────────────────────────────────────────────────────────────────────────────
# D1 REST API helper
# ─────────────────────────────────────────────────────────────────────────────

def _sql_literal(v: Any) -> str:
    """Render a Python value as a SQLite literal for inline multi-row INSERT."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    # String: escape single quotes by doubling them
    return "'" + str(v).replace("'", "''") + "'"


def d1_exec(sql: str, dry_run: bool) -> dict:
    """POST a single pre-rendered SQL statement (no bound params) to D1 REST API."""
    if dry_run:
        return {"success": True, "dry_run": True}

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
        f"/d1/database/{D1_DATABASE_ID}/query"
    )
    payload = json.dumps({"sql": sql}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {CF_API_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"D1 HTTP {e.code}: {body}") from e


def d1_query(sql: str, params: list, dry_run: bool) -> dict:
    """
    POST a single SQL statement with bound params to D1 REST API.

    Bound params keep the SQL string small (only `?` placeholders) while
    passing large field values (e.g. chapter notes) in the JSON body — this
    avoids SQLITE_TOOBIG SQL-length errors that occur when embedding 100 KB+
    of text as inline SQL string literals.
    """
    if dry_run:
        return {"success": True, "dry_run": True}

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
        f"/d1/database/{D1_DATABASE_ID}/query"
    )
    # D1 bound params must be JSON-serialisable; convert None → None (null), etc.
    safe_params = [
        None if v is None
        else (1 if v is True else (0 if v is False else v))
        for v in params
    ]
    payload = json.dumps({"sql": sql, "params": safe_params}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {CF_API_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"D1 HTTP {e.code}: {body}") from e


def _insert_row_with_params(args: tuple) -> bool:
    """Thread-worker: insert one row via bound params. Returns True on success."""
    table, col_list, placeholders, row, dry_run = args
    sql = f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})"
    d1_query(sql, row, dry_run)
    return True


def insert_batch(table: str, columns: list[str], rows: list[list], dry_run: bool) -> int:
    """
    Insert rows using INSERT OR IGNORE — idempotent, safe to retry.

    For LARGE_CONTENT_TABLES (e.g. chapters with 100 KB+ text): uses bound-param
    single-row INSERTs dispatched in parallel via ThreadPoolExecutor. Bound params
    keep the SQL statement string small, avoiding SQLITE_TOOBIG SQL-length errors.

    For all other tables: uses multi-row inline-literal INSERT for speed (10 rows
    per REST call instead of 1).

    Returns the number of rows attempted.
    """
    if not rows:
        return 0

    col_list = ", ".join(columns)

    # ── Path A: large-content tables — bound params + parallel threads ──────────
    if table in LARGE_CONTENT_TABLES:
        placeholders = ", ".join("?" * len(columns))
        work = [(table, col_list, placeholders, row, dry_run) for row in rows]
        inserted = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
            futures = {ex.submit(_insert_row_with_params, w): w for w in work}
            for fut in concurrent.futures.as_completed(futures):
                fut.result()   # re-raises on error
                inserted += 1
        return inserted

    # ── Path B: small-content tables — multi-row inline INSERT ───────────────────
    inserted = 0
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i : i + BATCH_SIZE]
        value_tuples = []
        for row in chunk:
            literals = ", ".join(_sql_literal(v) for v in row)
            value_tuples.append(f"({literals})")
        sql = f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES {', '.join(value_tuples)}"
        try:
            d1_exec(sql, dry_run)
            inserted += len(chunk)
        except RuntimeError as exc:
            if "TOOBIG" not in str(exc) or len(chunk) == 1:
                raise
            # Statement too large: fall back to one row per call for this chunk.
            for row in chunk:
                literals = ", ".join(_sql_literal(v) for v in row)
                single_sql = f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({literals})"
                d1_exec(single_sql, dry_run)
                inserted += 1
                time.sleep(REQUEST_DELAY)
        time.sleep(REQUEST_DELAY)

    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Per-collection migration functions (mapped from actual Beanie schemas)
# ─────────────────────────────────────────────────────────────────────────────

def migrate_boards(db, dry_run: bool) -> int:
    # Board: name, slug, status, created_at, updated_at
    rows = []
    for doc in db["boards"].find():
        rows.append([
            doc_id(doc),
            doc.get("name", ""),
            doc.get("slug") or slugify(doc.get("name", "")),
            doc.get("description"),
            to_int(doc.get("created_at")),
            to_int(doc.get("updated_at")),
        ])
    return insert_batch("boards",
        ["id", "name", "slug", "description", "created_at", "updated_at"],
        rows, dry_run)


def migrate_classes(db, dry_run: bool) -> int:
    # Class: name, board_id, status, created_at, updated_at
    # NOTE: Class has no slug field in MongoDB — derive from name.
    # D1 has a UNIQUE(board_id, slug) index; we suffix collisions with a counter.
    seen: dict[str, int] = {}   # (board_id, slug) → counter
    rows = []
    for doc in db["classes"].find():
        board_id = to_str(doc.get("board_id")) or ""
        base_slug = slugify(doc.get("name", ""))
        key = f"{board_id}:{base_slug}"
        if key in seen:
            seen[key] += 1
            slug = f"{base_slug}-{seen[key]}"
        else:
            seen[key] = 0
            slug = base_slug

        rows.append([
            doc_id(doc),
            board_id,
            doc.get("name", ""),
            slug,
            doc.get("level"),
            to_int(doc.get("created_at")),
        ])
    return insert_batch("classes",
        ["id", "board_id", "name", "slug", "level", "created_at"],
        rows, dry_run)


def migrate_streams(db, dry_run: bool) -> int:
    # Stream: name, class_id, status, created_at, updated_at
    # NOTE: Stream has no slug field in MongoDB — derive from name.
    rows = []
    for doc in db["streams"].find():
        rows.append([
            doc_id(doc),
            to_str(doc.get("class_id")) or "",
            doc.get("name", ""),
            slugify(doc.get("name", "")),
            to_int(doc.get("created_at")),
        ])
    return insert_batch("streams",
        ["id", "class_id", "name", "slug", "created_at"],
        rows, dry_run)


def migrate_subjects(db, dry_run: bool) -> int:
    # Subject: name, name_as, stream_id, status, slug, thumbnail_url (→image_url),
    #          pyq_papers, created_at, updated_at
    rows = []
    for doc in db["subjects"].find():
        slug = doc.get("slug") or slugify(doc.get("name", ""))
        # Mongo has no boolean is_published; derive from status field
        is_published = 1 if doc.get("status", "active") == "active" else 0
        # thumbnail_url is the image field in MongoDB Subject
        image_url = doc.get("thumbnail_url") or doc.get("image_url")
        rows.append([
            doc_id(doc),
            to_str(doc.get("stream_id")),
            doc.get("name", ""),
            slug,
            doc.get("description"),
            image_url,
            to_json(doc.get("pyq_papers") or [], "[]"),
            is_published,
            to_int(doc.get("created_at")),
            to_int(doc.get("updated_at")),
        ])
    return insert_batch("subjects",
        ["id", "stream_id", "name", "slug", "description", "image_url",
         "pyq_papers", "is_published", "created_at", "updated_at"],
        rows, dry_run)


def migrate_chapters(db, dry_run: bool) -> int:
    """
    Chapter field mapping (MongoDB Beanie → D1):
      notes_en        ← notes_en (structured Markdown notes) OR content_en (legacy HTML)
      notes_as        ← notes_as OR content_as
      rag_text        ← rag_text_en   (retrieval-only plain text, English)
      rag_text_as     ← rag_text_as
      rag_sections_en ← rag_sections_en  (Notes RAG structured chunks [{title,content}])
      rag_sections_as ← rag_sections_as
      qa_en           ← qa_text_en    (Q&A Markdown, user-facing)
      qa_as           ← qa_text_as
      published_topics← published_topics (serialised Topic list)
      word_count_en   ← word_count (Mongo stores single count; AS count not tracked)
    """
    rows = []
    for doc in db["chapters"].find():
        # Student-facing notes: prefer structured notes_en over legacy content_en
        notes_en = doc.get("notes_en") or doc.get("content_en")
        notes_as = doc.get("notes_as") or doc.get("content_as")

        # Retrieval plain text (rag_text_en in Beanie schema)
        rag_text    = doc.get("rag_text_en")
        rag_text_as = doc.get("rag_text_as")

        # Q&A content (qa_text_en in Beanie schema)
        qa_en = doc.get("qa_text_en")
        qa_as = doc.get("qa_text_as")

        # Structured RAG sections (both Notes and Q&A sections stored per-language)
        rag_sections_en = doc.get("rag_sections_en") or []
        rag_sections_as = doc.get("rag_sections_as") or []

        # Published topics (list of Topic objects)
        published_topics = doc.get("published_topics") or []

        rows.append([
            doc_id(doc),
            to_str(doc.get("subject_id")) or "",
            doc.get("title", ""),
            doc.get("slug", ""),
            doc.get("slug_as"),
            doc.get("chapter_number"),
            doc.get("status", "draft"),
            doc.get("content_type") or "notes",
            notes_en,
            notes_as,
            rag_text,
            rag_text_as,
            to_int(doc.get("rag_updated_at")),
            to_int(doc.get("rag_indexed_at")),
            to_json(rag_sections_en, "[]"),
            to_json(rag_sections_as, "[]"),
            to_json(published_topics, "[]"),
            qa_en,
            qa_as,
            doc.get("word_count") or 0,
            0,  # word_count_as — not tracked in Mongo
            to_int(doc.get("created_at")),
            to_int(doc.get("updated_at")),
        ])
    return insert_batch("chapters", [
        "id", "subject_id", "title", "slug", "slug_as", "chapter_number",
        "status", "content_type",
        "notes_en", "notes_as",
        "rag_text", "rag_text_as", "rag_updated_at", "rag_indexed_at",
        "rag_sections_en", "rag_sections_as",
        "published_topics",
        "qa_en", "qa_as",
        "word_count_en", "word_count_as",
        "created_at", "updated_at",
    ], rows, dry_run)


def migrate_users(db, dry_run: bool) -> int:
    """
    User field mapping (MongoDB Beanie → D1):
      - bcrypt hashed_password preserved verbatim (no password reset needed)
      - deletion_requested / deletion_scheduled_at → mapped to deleted_at
      - Soft-deleted users ARE included (D1 has deleted_at for this purpose);
        excluding them would orphan related payment/quota rows under FK enforcement.
    """
    rows = []
    # Include ALL users — soft-deleted ones get deleted_at populated.
    # D1's deleted_at column exists specifically to retain these records.
    for doc in db["users"].find():
        # Derive deleted_at from deletion_scheduled_at if flagged
        deleted_at = None
        if doc.get("deletion_requested"):
            deleted_at = to_int(doc.get("deletion_scheduled_at"))

        rows.append([
            doc_id(doc),
            doc.get("email"),
            doc.get("hashed_password"),
            doc.get("auth_provider", "anonymous"),
            doc.get("role", "student"),
            doc.get("subscription_tier", "free"),
            doc.get("subscription_status", "active"),
            doc.get("razorpay_subscription_id"),
            doc.get("razorpay_customer_id"),
            to_int(doc.get("current_period_start")),
            to_int(doc.get("current_period_end")),
            to_bool_int(doc.get("cancel_at_period_end")),
            doc.get("monthly_message_count") or 0,
            to_int(doc.get("last_reset_date")),
            doc.get("total_lifetime_messages") or 0,
            doc.get("credits_remaining") or 0,
            doc.get("credits_used") or 0,
            doc.get("total_tokens_used") or 0,
            doc.get("name"),
            doc.get("avatar_url"),
            to_bool_int(doc.get("consent_dpdp")),
            doc.get("preferred_language", "as"),
            to_bool_int(doc.get("voice_enabled"), default=True),
            doc.get("theme", "light"),
            to_json(doc.get("saved_subjects") or [], "[]"),
            doc.get("phone"),
            to_bool_int(doc.get("onboarding_done")),
            to_bool_int(doc.get("ads_opt_out")),
            doc.get("grade"),
            doc.get("board_id"),
            doc.get("board_name") or doc.get("board"),
            doc.get("class_id"),
            doc.get("class_name"),
            doc.get("stream_id"),
            doc.get("stream_name") or doc.get("stream"),
            deleted_at,
            doc.get("deletion_reason"),
            to_int(doc.get("created_at")),
            to_int(doc.get("updated_at")),
        ])
    return insert_batch("users", [
        "id", "email", "hashed_password", "auth_provider", "role",
        "subscription_tier", "subscription_status",
        "razorpay_subscription_id", "razorpay_customer_id",
        "current_period_start", "current_period_end", "cancel_at_period_end",
        "monthly_message_count", "last_reset_date", "total_lifetime_messages",
        "credits_remaining", "credits_used", "total_tokens_used",
        "name", "avatar_url", "consent_dpdp", "preferred_language",
        "voice_enabled", "theme", "saved_subjects", "phone",
        "onboarding_done", "ads_opt_out",
        "grade", "board_id", "board_name", "class_id", "class_name",
        "stream_id", "stream_name",
        "deleted_at", "deletion_reason",
        "created_at", "updated_at",
    ], rows, dry_run)


def migrate_chats(db, dry_run: bool) -> int:
    """
    MongoDB Chat stores a session document with an embedded `messages` array.
    D1 stores flat rows (one row per message). Each message is exploded here.

    MongoDB schema:
      Chat.session_id, Chat.user_id, Chat.messages: [{role, content, timestamp,
        model_used, latency_ms, rag_sources, source_ctx, feedback}]
    D1 schema:
      chats(id, user_id, session_id, role, content, lang, subject_id,
            chapter_id, metadata, expires_at, created_at)
    """
    if not collection_exists(db, "chats"):
        return 0

    cutoff_dt = datetime.fromtimestamp(time.time() - 90 * 86400, tz=timezone.utc)
    rows = []

    for session in db["chats"].find({"updated_at": {"$gte": cutoff_dt}}):
        session_id = session.get("session_id", "")
        user_id = to_str(session.get("user_id")) or ""
        messages = session.get("messages") or []

        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            content = msg.get("content") or ""
            ts = msg.get("timestamp")
            created_at = None
            if isinstance(ts, str):
                try:
                    created_at = to_int(datetime.fromisoformat(ts.replace("Z", "+00:00")))
                except ValueError:
                    created_at = to_int(session.get("created_at"))
            else:
                created_at = to_int(ts) or to_int(session.get("created_at"))

            # Preserve rag_sources and model_used in metadata JSON
            metadata = {
                "model_used": msg.get("model_used"),
                "latency_ms": msg.get("latency_ms"),
                "rag_sources": msg.get("rag_sources") or [],
                "feedback": msg.get("feedback"),
            }
            # Chat messages expire 90 days after the session's updated_at
            session_updated = session.get("updated_at")
            expires_at = to_int(session_updated) + 90 * 86400 if session_updated else None

            # Deterministic ID from session_id + message index — idempotent on rerun.
            # Using SHA-256 truncated to 32 hex chars to avoid UUID collision format issues.
            raw = hashlib.sha256(f"{session_id}:{idx}:{role}".encode()).hexdigest()
            msg_id = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"

            rows.append([
                msg_id,              # deterministic per (session_id, index, role)
                user_id,
                session_id,
                role,
                content,
                "en",                # lang not stored on individual messages
                None,                # subject_id — not stored in Mongo chat
                None,                # chapter_id — not stored in Mongo chat
                to_json(metadata, "{}"),
                expires_at,
                created_at,
            ])

    return insert_batch("chats", [
        "id", "user_id", "session_id", "role", "content", "lang",
        "subject_id", "chapter_id", "metadata", "expires_at", "created_at",
    ], rows, dry_run)


def migrate_conversation_metadata(db, dry_run: bool) -> int:
    """
    Preserve Mongo Chat session metadata alongside the flattened D1 messages.

    Titles are user-visible saved-history data. INSERT OR IGNORE makes reruns
    idempotent without overwriting titles changed after D1 became authoritative.
    """
    if not collection_exists(db, "chats"):
        return 0

    cutoff_dt = datetime.fromtimestamp(time.time() - 90 * 86400, tz=timezone.utc)
    rows = []
    for session in db["chats"].find({"updated_at": {"$gte": cutoff_dt}}):
        session_id = str(session.get("session_id") or "")
        if not session_id:
            continue
        rows.append([
            to_str(session.get("user_id")) or "",
            session_id,
            session.get("title"),
            0,  # Mongo Chat has no star/archive fields
            0,
            to_int(session.get("updated_at")) or to_int(session.get("created_at")) or 0,
        ])
    return insert_batch(
        "conversation_metadata",
        ["user_id", "session_id", "title", "starred", "archived", "updated_at"],
        rows,
        dry_run,
    )


def migrate_chat_feedback(db, dry_run: bool) -> int:
    """
    MongoDB ChatFeedback: user_id, session_id, message_id, lang, model_provider,
      rating (1/-1), latency_ms, query_text, timestamp, archived, read
    D1 chat_feedback: id, chat_id, user_id, rating, comment, expires_at, created_at
    """
    if not collection_exists(db, "chat_feedback"):
        return 0

    rows = []
    for doc in db["chat_feedback"].find():
        # message_id in Mongo → chat_id in D1 (closest semantic match)
        chat_id = doc.get("message_id") or doc.get("session_id") or ""
        # Rating: Mongo uses 1 / -1; D1 stores as integer (keep as-is for now)
        rating = doc.get("rating") or 0
        # Store lang + model + query context in comment field
        comment_parts = []
        if doc.get("lang"):
            comment_parts.append(f"lang:{doc['lang']}")
        if doc.get("model_provider"):
            comment_parts.append(f"model:{doc['model_provider']}")
        if doc.get("query_text"):
            comment_parts.append(f"query:{doc['query_text'][:100]}")
        comment = " | ".join(comment_parts) or None

        created_at = to_int(doc.get("timestamp"))
        # Feedback expires 30 days after creation
        expires_at = (created_at + 30 * 86400) if created_at else None

        rows.append([
            doc_id(doc),
            chat_id,
            to_str(doc.get("user_id")) or "",
            rating,
            comment,
            expires_at,
            created_at,
        ])
    return insert_batch("chat_feedback",
        ["id", "chat_id", "user_id", "rating", "comment", "expires_at", "created_at"],
        rows, dry_run)


def migrate_quota_usage(db, dry_run: bool) -> int:
    """
    MongoDB QuotaUsage: user_id, month (YYYY-MM), count, expires_at
    D1 quota_usage: id, user_id, period (YYYY-MM), count, updated_at
    """
    if not collection_exists(db, "quota_usage"):
        return 0

    rows = []
    for doc in db["quota_usage"].find():
        rows.append([
            doc_id(doc),
            to_str(doc.get("user_id")) or "",
            doc.get("month", ""),    # already YYYY-MM format — maps to 'period'
            doc.get("count") or 0,
            to_int(doc.get("expires_at")),   # reused as updated_at proxy
        ])
    return insert_batch("quota_usage",
        ["id", "user_id", "period", "count", "updated_at"],
        rows, dry_run)


def migrate_payments(db, dry_run: bool) -> int:
    if not collection_exists(db, "payments"):
        return 0
    rows = []
    for doc in db["payments"].find():
        rows.append([
            doc_id(doc),
            to_str(doc.get("user_id")) or "",
            doc.get("razorpay_payment_id"),
            doc.get("razorpay_order_id"),
            doc.get("razorpay_subscription_id"),
            doc.get("amount"),
            doc.get("currency", "INR"),
            doc.get("status", "unknown"),
            doc.get("plan"),
            to_json(doc.get("metadata") or {}, "{}"),
            to_int(doc.get("created_at")),
        ])
    return insert_batch("payments", [
        "id", "user_id", "razorpay_payment_id", "razorpay_order_id",
        "razorpay_subscription_id", "amount", "currency", "status",
        "plan", "metadata", "created_at",
    ], rows, dry_run)


def migrate_transactions(db, dry_run: bool) -> int:
    if not collection_exists(db, "transactions"):
        return 0
    rows = []
    for doc in db["transactions"].find():
        rows.append([
            doc_id(doc),
            to_str(doc.get("user_id")) or "",
            doc.get("type", "unknown"),
            doc.get("amount") or 0,
            to_json(doc.get("metadata") or {}, "{}"),
            to_int(doc.get("created_at")),
        ])
    return insert_batch("transactions",
        ["id", "user_id", "type", "amount", "metadata", "created_at"],
        rows, dry_run)


def migrate_rag_documents(db, dry_run: bool) -> int:
    """
    MongoDB RagDocument: subject_id, chapter_id, medium, source_type,
      file_url, original_filename, page_count, status, error_message,
      ingested_at, created_at, updated_at
    D1 rag_documents: id, chapter_id, subject_id, source_type, medium,
      content, metadata, indexed_at, created_at
    """
    if not collection_exists(db, "rag_documents"):
        return 0

    rows = []
    for doc in db["rag_documents"].find():
        metadata = {
            "file_url": doc.get("file_url"),
            "original_filename": doc.get("original_filename"),
            "page_count": doc.get("page_count"),
            "status": doc.get("status", "pending"),
            "error_message": doc.get("error_message"),
        }
        rows.append([
            doc_id(doc),
            doc.get("chapter_id"),
            doc.get("subject_id"),
            doc.get("source_type", "book_pdf"),
            doc.get("medium", "english"),
            None,          # content — not stored on RagDocument (lives in Chunks)
            to_json(metadata, "{}"),
            to_int(doc.get("ingested_at")),
            to_int(doc.get("created_at")),
        ])
    return insert_batch("rag_documents",
        ["id", "chapter_id", "subject_id", "source_type", "medium",
         "content", "metadata", "indexed_at", "created_at"],
        rows, dry_run)


def migrate_chunks(db, dry_run: bool) -> int:
    """
    MongoDB Chunk: document_id, subject_id, chapter_id, topic_id, medium,
      source_type, chunk_type, chunk_text, chunk_index, token_count,
      vector_id, embedding_model, created_at, updated_at
    D1 chunks: id, document_id, chapter_id, subject_id, source_type, medium,
      chunk_type, content, vector_id, metadata, created_at
    """
    if not collection_exists(db, "chunks"):
        return 0

    rows = []
    for doc in db["chunks"].find():
        metadata = {
            "topic_id": doc.get("topic_id"),
            "chunk_index": doc.get("chunk_index", 0),
            "token_count": doc.get("token_count", 0),
            "embedding_model": doc.get("embedding_model", "cf/baai/bge-m3"),
        }
        rows.append([
            doc_id(doc),
            doc.get("document_id"),
            doc.get("chapter_id"),
            doc.get("subject_id"),
            doc.get("source_type", "book_pdf"),
            doc.get("medium", "english"),
            doc.get("chunk_type", "topic_chunk"),
            doc.get("chunk_text", ""),   # chunk_text in Beanie → content in D1
            doc.get("vector_id"),
            to_json(metadata, "{}"),
            to_int(doc.get("created_at")),
        ])
    return insert_batch("chunks",
        ["id", "document_id", "chapter_id", "subject_id", "source_type",
         "medium", "chunk_type", "content", "vector_id", "metadata", "created_at"],
        rows, dry_run)


def migrate_publish_jobs(db, dry_run: bool) -> int:
    """
    MongoDB PublishJob: chapter_id, chapter_title, triggered_by, status,
      steps (list[PublishJobStep]), error, started_at, finished_at,
      created_at, updated_at
    D1 publish_jobs: id, chapter_id, status, progress, error_log,
      created_at, updated_at, completed_at
    """
    if not collection_exists(db, "publish_jobs"):
        return 0

    rows = []
    for doc in db["publish_jobs"].find():
        # Collapse steps list into the progress JSON field
        steps = doc.get("steps") or []
        progress: dict[str, Any] = {}
        for step in steps:
            if isinstance(step, dict):
                name = step.get("name", "")
                progress[name] = {
                    "status": step.get("status", "pending"),
                    "error": step.get("error"),
                }
        rows.append([
            doc_id(doc),
            doc.get("chapter_id", ""),
            doc.get("status", "pending"),
            to_json(progress, "{}"),
            doc.get("error"),
            to_int(doc.get("created_at")),
            to_int(doc.get("updated_at")),
            to_int(doc.get("finished_at")),
        ])
    return insert_batch("publish_jobs",
        ["id", "chapter_id", "status", "progress", "error_log",
         "created_at", "updated_at", "completed_at"],
        rows, dry_run)


def migrate_seed_runs(db, dry_run: bool) -> int:
    """
    MongoDB SeedRun: status, run_type (→medium), started_at, finished_at,
      total (→total_chapters), completed (→processed), failed, skipped,
      errors (→log), concurrency, force, current
    D1 seed_runs: id, medium, status, total_chapters, processed, failed,
      log, started_at, completed_at, expires_at
    """
    if not collection_exists(db, "seed_runs"):
        return 0

    rows = []
    for doc in db["seed_runs"].find():
        # run_type: "notes" → "en", "assamese" → "as"
        run_type = doc.get("run_type", "notes")
        medium = "as" if "assam" in run_type.lower() else "en"

        # Build log entries from errors list
        errors = doc.get("errors") or []
        log_entries = [
            {"level": "error", "message": str(e.get("error") or e), "chapter": e.get("chapter_id")}
            for e in errors if isinstance(e, dict)
        ]

        # Seed run history expires 90 days after completion
        finished_at = to_int(doc.get("finished_at"))
        expires_at = (finished_at + 90 * 86400) if finished_at else None

        rows.append([
            doc_id(doc),
            medium,
            doc.get("status", "completed"),
            doc.get("total") or 0,
            doc.get("completed") or 0,
            doc.get("failed") or 0,
            to_json(log_entries, "[]"),
            to_int(doc.get("started_at")),
            finished_at,
            expires_at,
        ])
    return insert_batch("seed_runs",
        ["id", "medium", "status", "total_chapters", "processed", "failed",
         "log", "started_at", "completed_at", "expires_at"],
        rows, dry_run)


def migrate_ai_usage_logs(db, dry_run: bool) -> int:
    """
    MongoDB AiUsageLog: user_id, session_id, provider, model, lang,
      input_tokens, output_tokens, total_tokens, latency_ms, cost_usd, created_at
    D1 ai_usage_logs: id, user_id, provider, model, input_tokens, output_tokens,
      latency_ms, request_id, expires_at, created_at
    """
    if not collection_exists(db, "ai_usage_logs"):
        return 0

    # Only migrate recent logs (last 90 days — older data is past the D1 TTL anyway)
    cutoff_dt = datetime.fromtimestamp(time.time() - 90 * 86400, tz=timezone.utc)
    rows = []
    for doc in db["ai_usage_logs"].find({"created_at": {"$gte": cutoff_dt}}):
        created_at = to_int(doc.get("created_at"))
        expires_at = (created_at + 90 * 86400) if created_at else None
        rows.append([
            doc_id(doc),
            to_str(doc.get("user_id")),
            doc.get("provider", ""),
            doc.get("model", ""),
            doc.get("input_tokens") or 0,
            doc.get("output_tokens") or 0,
            doc.get("latency_ms") or 0,
            doc.get("session_id"),    # request_id — closest available field
            expires_at,
            created_at,
        ])
    return insert_batch("ai_usage_logs",
        ["id", "user_id", "provider", "model", "input_tokens", "output_tokens",
         "latency_ms", "request_id", "expires_at", "created_at"],
        rows, dry_run)


def migrate_content_audit_log(db, dry_run: bool) -> int:
    """
    MongoDB ContentAuditLog: chapter_id, subject_id, action, actor_id,
      actor_email, version_before, version_after, changes, created_at
    D1 content_audit_log: id, user_id, action, target_type, target_id,
      diff, expires_at, created_at
    """
    if not collection_exists(db, "content_audit_log"):
        return 0

    rows = []
    for doc in db["content_audit_log"].find():
        created_at = to_int(doc.get("created_at"))
        expires_at = (created_at + 180 * 86400) if created_at else None
        diff = {
            "actor_email": doc.get("actor_email"),
            "version_before": doc.get("version_before"),
            "version_after": doc.get("version_after"),
            "changes": doc.get("changes"),
        }
        rows.append([
            doc_id(doc),
            doc.get("actor_id"),          # actor_id → user_id
            doc.get("action", ""),
            "chapter",                    # target_type always chapter in Mongo
            doc.get("chapter_id"),        # target_id
            to_json(diff, "{}"),
            expires_at,
            created_at,
        ])
    return insert_batch("content_audit_log",
        ["id", "user_id", "action", "target_type", "target_id",
         "diff", "expires_at", "created_at"],
        rows, dry_run)


def migrate_memory_brain(db, dry_run: bool) -> int:
    """Migrate per-user AI memory (if collection exists under any name)."""
    for coll_name in ["memory_brain", "memories", "user_memories"]:
        if not collection_exists(db, coll_name):
            continue
        rows = []
        for doc in db[coll_name].find():
            rows.append([
                doc_id(doc),
                to_str(doc.get("user_id")) or "",
                doc.get("key", ""),
                doc.get("value"),
                to_int(doc.get("updated_at")),
            ])
        return insert_batch("memory_brain",
            ["id", "user_id", "key", "value", "updated_at"], rows, dry_run)
    return 0


def migrate_dead_letters(db, dry_run: bool) -> int:
    """
    MongoDB dead_letters: user_id, message, lang, error, timestamp, status,
      retry_count, both_providers_down
    D1 dead_letters: id, job_type, payload, error, attempts, expires_at, created_at
    """
    if not collection_exists(db, "dead_letters"):
        return 0

    rows = []
    for doc in db["dead_letters"].find():
        created_at = to_int(doc.get("timestamp"))
        expires_at = (created_at + 30 * 86400) if created_at else None
        payload = {
            "user_id": doc.get("user_id"),
            "message": doc.get("message", "")[:200],
            "lang": doc.get("lang", "en"),
            "status": doc.get("status", "pending"),
            "both_providers_down": doc.get("both_providers_down", False),
        }
        rows.append([
            doc_id(doc),
            "chat_failure",          # job_type — all dead letters are chat failures
            to_json(payload, "{}"),
            doc.get("error", ""),
            (doc.get("retry_count") or 0) + 1,  # attempts = retry_count + 1 (initial attempt)
            expires_at,
            created_at,
        ])
    return insert_batch("dead_letters",
        ["id", "job_type", "payload", "error", "attempts", "expires_at", "created_at"],
        rows, dry_run)


def migrate_payments_pending(db, dry_run: bool) -> int:
    """
    MongoDB payments_pending: order_id, user_id, amount, plan, metadata,
      expires_at, created_at (and various payment-type-specific fields)
    D1 payments_pending: id, order_id, user_id, metadata, expires_at, created_at
    """
    if not collection_exists(db, "payments_pending"):
        return 0

    rows = []
    for doc in db["payments_pending"].find():
        order_id = doc.get("order_id") or doc.get("razorpay_order_id") or ""
        # Collect all payment-context fields into metadata
        metadata = {
            k: v for k, v in doc.items()
            if k not in ("_id", "order_id", "user_id", "expires_at", "created_at")
            and not callable(v)
        }
        rows.append([
            doc_id(doc),
            str(order_id),
            to_str(doc.get("user_id")) or "",
            to_json(metadata, "{}"),
            to_int(doc.get("expires_at")),
            to_int(doc.get("created_at")),
        ])
    return insert_batch("payments_pending",
        ["id", "order_id", "user_id", "metadata", "expires_at", "created_at"],
        rows, dry_run)


def migrate_admin_config(db, dry_run: bool) -> int:
    """Migrate admin configuration key-value pairs."""
    for coll_name in ["admin_config", "config"]:
        if not collection_exists(db, coll_name):
            continue
        rows = []
        for doc in db[coll_name].find():
            key = doc.get("key") or str(doc.get("_id", ""))
            value = doc.get("value")
            if isinstance(value, (dict, list)):
                value = json.dumps(value, default=str)
            rows.append([
                key,
                str(value) if value is not None else None,
                to_int(doc.get("updated_at")),
            ])
        return insert_batch("admin_config",
            ["key", "value", "updated_at"], rows, dry_run)
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

# Migration steps in dependency order (parents before children)
MIGRATION_STEPS: list[tuple[str, Any]] = [
    # Content hierarchy (FK order)
    ("boards",              migrate_boards),
    ("classes",             migrate_classes),
    ("streams",             migrate_streams),
    ("subjects",            migrate_subjects),
    ("chapters",            migrate_chapters),
    # Users (no FK deps in D1 schema)
    ("users",               migrate_users),
    # Content pipeline
    ("rag_documents",       migrate_rag_documents),
    ("chunks",              migrate_chunks),
    ("publish_jobs",        migrate_publish_jobs),
    ("seed_runs",           migrate_seed_runs),
    ("content_audit_log",   migrate_content_audit_log),
    # Chat & quotas
    ("chats",               migrate_chats),
    ("conversation_metadata", migrate_conversation_metadata),
    ("chat_feedback",       migrate_chat_feedback),
    ("quota_usage",         migrate_quota_usage),
    # Payments
    ("payments",            migrate_payments),
    ("transactions",        migrate_transactions),
    ("payments_pending",    migrate_payments_pending),
    # Analytics & admin
    ("ai_usage_logs",       migrate_ai_usage_logs),
    ("memory_brain",        migrate_memory_brain),
    ("admin_config",        migrate_admin_config),
    # Operational queues
    ("dead_letters",        migrate_dead_letters),
]

# D1 tables with no MongoDB equivalent — these start empty in D1 (managed by API Worker):
#   password_reset_tokens   — short-lived; newly issued after cutover
#   refund_requests         — no Mongo collection; new feature in D1
#   email_failure_events    — operational TTL queue; starts empty
#   email_alert_state       — singleton; starts at default
#   schema_migrations       — managed by wrangler d1 migrations apply


def validate_env(dry_run: bool) -> None:
    if not MONGODB_URL:
        die("MONGODB_URL environment variable is required.")
    if not dry_run:
        if not CF_API_TOKEN:
            die("CF_API_TOKEN (or CLOUDFLARE_API_TOKEN) is required.")
        if not CF_ACCOUNT_ID:
            die("CF_ACCOUNT_ID (or CLOUDFLARE_ACCOUNT_ID) is required.")
        if not D1_DATABASE_ID:
            die("D1_DATABASE_ID is required. Get it from: wrangler d1 list")


def validate_d1_integrity() -> None:
    """
    Run post-migration referential integrity checks against D1.

    Checks:
      1. Row counts for all migrated tables.
      2. Orphaned chapters — chapters.subject_id not present in subjects.id.
      3. Orphaned chunks   — chunks.subject_id not present in subjects.id.
      4. NULL-stream subjects — subjects with stream_id IS NULL (expected: 45).

    Exits with code 1 if any referential integrity check fails.
    """
    print("=" * 64)
    print("D1 Migration Validation")
    print(f"  D1 DB ID : {D1_DATABASE_ID}")
    print("=" * 64)

    def q(sql: str) -> list[dict]:
        import urllib.request
        import urllib.error
        body = json.dumps({"sql": sql}).encode()
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{D1_DATABASE_ID}/query",
            data=body,
            headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as r:
                data = json.loads(r.read())
                if not data.get("success"):
                    raise RuntimeError(str(data.get("errors", "")))
                return data["result"][0]["results"]
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"D1 HTTP {exc.code}: {exc.read().decode()[:200]}") from exc

    # Row counts
    tables = [
        "boards", "classes", "streams", "subjects", "chapters",
        "users", "chats", "conversation_metadata", "payments", "transactions", "chunks",
    ]
    print("\n  Table row counts:")
    for t in tables:
        count = q(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
        print(f"    {t:<24}  {count:>8,}")

    # Referential integrity checks
    print("\n  Referential integrity:")
    errors: list[str] = []

    orphaned_chapters = q(
        "SELECT COUNT(*) AS n FROM chapters c "
        "LEFT JOIN subjects s ON c.subject_id = s.id WHERE s.id IS NULL"
    )[0]["n"]
    status = "✅" if orphaned_chapters == 0 else "❌"
    print(f"    {status}  Orphaned chapters: {orphaned_chapters}")
    if orphaned_chapters:
        errors.append(f"Orphaned chapters: {orphaned_chapters}")

    orphaned_chunks = q(
        "SELECT COUNT(*) AS n FROM chunks c "
        "LEFT JOIN subjects s ON c.subject_id = s.id WHERE s.id IS NULL AND c.subject_id IS NOT NULL"
    )[0]["n"]
    status = "✅" if orphaned_chunks == 0 else "❌"
    print(f"    {status}  Orphaned chunks: {orphaned_chunks}")
    if orphaned_chunks:
        errors.append(f"Orphaned chunks: {orphaned_chunks}")

    null_stream_subjects = q(
        "SELECT COUNT(*) AS n FROM subjects WHERE stream_id IS NULL"
    )[0]["n"]
    # 45 NULL-stream subjects are expected (NEP college subjects from MongoDB)
    print(f"    ℹ️   NULL-stream subjects: {null_stream_subjects} (expected: 45 NEP college subjects)")

    # Schema migrations applied
    migrations = q("SELECT version FROM schema_migrations ORDER BY version")
    print(f"\n  Applied migrations: {[m['version'] for m in migrations]}")

    print()
    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print(f"  • {e}")
        sys.exit(1)
    else:
        print("Validation passed ✅")
    print("=" * 64)


def validate_mongo_d1_parity(db, wanted: set[str], sample_size: int) -> None:
    """
    Prove that the data selected for a migration exists in D1 with the same
    count and a deterministic ID sample. This is deliberately stricter than
    the D1-only integrity check: an unexpected D1 row also blocks cutover.

    Run this while writes are paused or dual-written. Once a datastore accepts
    independent production writes, an exact snapshot comparison is no longer a
    meaningful cutover gate.
    """
    def metadata_sample_value(user_id: str, session_id: str, title: Any, updated_at: int) -> str:
        """Retain the difference between a NULL title and an empty title."""
        return "\x1f".join((
            user_id,
            session_id,
            json.dumps(title, ensure_ascii=False, separators=(",", ":")),
            str(updated_at),
        ))

    migration_tables = {
        "boards": "boards",
        "classes": "classes",
        "streams": "streams",
        "subjects": "subjects",
        "chapters": "chapters",
        "users": "users",
        "rag_documents": "rag_documents",
        "chunks": "chunks",
        "publish_jobs": "publish_jobs",
        "seed_runs": "seed_runs",
        "content_audit_log": "content_audit_log",
        "chats": "chats",
        "conversation_metadata": "conversation_metadata",
        "chat_feedback": "chat_feedback",
        "quota_usage": "quota_usage",
        "payments": "payments",
        "transactions": "transactions",
        "payments_pending": "payments_pending",
        "ai_usage_logs": "ai_usage_logs",
        "memory_brain": "memory_brain",
        "admin_config": "admin_config",
        "dead_letters": "dead_letters",
    }

    def d1_query(sql: str) -> list[dict]:
        body = json.dumps({"sql": sql}).encode()
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{D1_DATABASE_ID}/query",
            data=body,
            headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"D1 HTTP {exc.code}: {exc.read().decode()[:200]}") from exc
        if not payload.get("success"):
            raise RuntimeError(f"D1 query failed: {payload.get('errors', '')}")
        return (payload.get("result") or [{}])[0].get("results", [])

    def source_collection(step: str):
        candidates = {
            "memory_brain": ["memory_brain", "memories", "user_memories"],
            "admin_config": ["admin_config", "config"],
            "conversation_metadata": ["chats"],
        }.get(step, [step])
        for candidate in candidates:
            if collection_exists(db, candidate):
                return db[candidate]
        return None

    print(f"\nMongo → D1 parity (deterministic sample size: {sample_size}):")
    errors: list[str] = []
    for step, table in migration_tables.items():
        if wanted and step not in wanted:
            continue
        collection = source_collection(step)
        if collection is None:
            print(f"  – {step:<22} source collection absent (not migrated)")
            continue

        if step == "conversation_metadata":
            cutoff = datetime.fromtimestamp(time.time() - 90 * 86400, tz=timezone.utc)
            metadata_rows = []
            for session in collection.find(
                {"updated_at": {"$gte": cutoff}},
                {"_id": 0, "user_id": 1, "session_id": 1, "title": 1, "updated_at": 1, "created_at": 1},
            ).sort("session_id", 1):
                session_id = str(session.get("session_id") or "")
                if not session_id:
                    continue
                metadata_rows.append((
                    to_str(session.get("user_id")) or "",
                    session_id,
                    session.get("title"),
                    to_int(session.get("updated_at")) or to_int(session.get("created_at")) or 0,
                ))
            metadata_rows.sort(key=lambda row: (row[0], row[1]))
            source_count = len(metadata_rows)
            source_ids = [
                metadata_sample_value(user_id, session_id, title, updated_at)
                for user_id, session_id, title, updated_at in metadata_rows[:sample_size]
            ]
            id_column = None
        elif step == "chats":
            cutoff = datetime.fromtimestamp(time.time() - 90 * 86400, tz=timezone.utc)
            all_ids: list[str] = []
            for session in collection.find(
                {"updated_at": {"$gte": cutoff}},
                {"_id": 0, "session_id": 1, "messages": 1},
            ).sort("session_id", 1):
                session_id = session.get("session_id", "")
                for index, message in enumerate(session.get("messages") or []):
                    if not isinstance(message, dict):
                        continue
                    role = message.get("role", "user")
                    raw = hashlib.sha256(f"{session_id}:{index}:{role}".encode()).hexdigest()
                    all_ids.append(
                        f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"
                    )
            source_count = len(all_ids)
            source_ids = sorted(all_ids)[:sample_size]
            id_column = "id"
        elif step == "admin_config":
            source_ids = sorted(
                str(doc.get("key") or doc.get("_id", ""))
                for doc in collection.find({}, {"_id": 1, "key": 1})
            )
            source_count = len(source_ids)
            id_column = "key"
        else:
            query = {}
            if step == "ai_usage_logs":
                cutoff = datetime.fromtimestamp(time.time() - 90 * 86400, tz=timezone.utc)
                query = {"created_at": {"$gte": cutoff}}
            source_count = collection.count_documents(query)
            source_ids = [
                # Use the exact migration identity transform rather than
                # assuming every source `_id` is copied verbatim to D1.
                doc_id(doc)
                for doc in collection.find(query, {"_id": 1}).sort("_id", 1).limit(sample_size)
                if doc.get("_id") is not None
            ]
            id_column = "id"

        target_count = d1_query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        if step == "conversation_metadata":
            target_ids = [
                metadata_sample_value(
                    str(row["user_id"]),
                    str(row["session_id"]),
                    row["title"],
                    int(row["updated_at"]),
                )
                for row in d1_query(
                    "SELECT user_id, session_id, title, updated_at "
                    "FROM conversation_metadata ORDER BY user_id ASC, session_id ASC "
                    f"LIMIT {sample_size}"
                )
            ]
        else:
            target_ids = [
                str(row["source_id"])
                for row in d1_query(
                    f"SELECT {id_column} AS source_id FROM {table} "
                    f"ORDER BY {id_column} ASC LIMIT {sample_size}"
                )
            ]
        expected_ids = source_ids[:sample_size]
        count_matches = source_count == target_count
        sample_matches = expected_ids == target_ids
        marker = "✅" if count_matches and sample_matches else "❌"
        print(
            f"  {marker} {step:<22} Mongo={source_count:,} D1={target_count:,} "
            f"sample={'match' if sample_matches else 'mismatch'}"
        )
        if not count_matches:
            errors.append(f"{step}: Mongo has {source_count} rows; D1 has {target_count}")
        if not sample_matches:
            errors.append(
                f"{step}: leading IDs differ "
                f"(Mongo={expected_ids[:3]}, D1={target_ids[:3]})"
            )

    if errors:
        raise RuntimeError("Mongo→D1 parity validation failed:\n  • " + "\n  • ".join(errors))
    print("Mongo → D1 parity passed ✅")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate MongoDB data to Cloudflare D1"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Count documents without writing to D1"
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Run D1 integrity plus strict Mongo→D1 count/sample parity checks and exit"
    )
    parser.add_argument(
        "--sample-size", type=int, default=20,
        help="Number of deterministic source IDs to compare per collection during --validate"
    )
    parser.add_argument(
        "--collections", default="",
        help="Comma-separated list of step names to run (default: all)"
    )
    parser.add_argument(
        "--db-name", default=MONGO_DB_NAME,
        help=f"MongoDB database name (default: {MONGO_DB_NAME})"
    )
    args = parser.parse_args()

    validate_env(args.dry_run)

    wanted = set(c.strip() for c in args.collections.split(",") if c.strip()) if args.collections else set()

    if args.validate:
        if args.sample_size < 1 or args.sample_size > 500:
            die("--sample-size must be between 1 and 500")
        client = MongoClient(MONGODB_URL, serverSelectionTimeoutMS=10_000)
        db = client[args.db_name]
        try:
            client.admin.command("ping")
            validate_d1_integrity()
            validate_mongo_d1_parity(db, wanted, args.sample_size)
        finally:
            client.close()
        return

    print("=" * 64)
    print("Syrabit: MongoDB → D1 Migration")
    print(f"  Source DB : {args.db_name}")
    print(f"  D1 DB ID  : {D1_DATABASE_ID or '(dry-run — no writes)'}")
    print(f"  Mode      : {'DRY RUN — no writes' if args.dry_run else 'LIVE'}")
    if wanted:
        print(f"  Filter    : {', '.join(sorted(wanted))}")
    print("=" * 64)

    client = MongoClient(MONGODB_URL, serverSelectionTimeoutMS=10_000)
    db = client[args.db_name]

    try:
        client.admin.command("ping")
        print("✅  MongoDB connected\n")
    except Exception as exc:
        die(f"Cannot connect to MongoDB: {exc}")

    total_rows = 0
    errors: list[str] = []

    for name, fn in MIGRATION_STEPS:
        if wanted and name not in wanted:
            continue

        print(f"  ▶  {name:<24}", end="", flush=True)
        try:
            n = fn(db, args.dry_run)
            label = "counted" if args.dry_run else "inserted"
            print(f"  {n:>6,} rows {label}")
            total_rows += n
        except Exception as exc:
            msg = f"{name}: {exc}"
            errors.append(msg)
            print(f"\n     ⚠️  FAILED — {msg}")

    print()
    print("=" * 64)
    if args.dry_run:
        print("DRY RUN COMPLETE (no data written)")
    else:
        print("MIGRATION COMPLETE")
    print(f"  Total rows : {total_rows:,}")

    if errors:
        print(f"  Failures   : {len(errors)}")
        for e in errors:
            print(f"    • {e}")
        sys.exit(1)
    else:
        print("  All steps succeeded ✅")
        if not args.dry_run:
            print()
            print("Next steps:")
            print("  1. Verify row counts with wrangler d1 execute:")
            print("     cd apps/api && wrangler d1 execute syrabit-db --remote \\")
            print('       --command "SELECT COUNT(*) FROM users; SELECT COUNT(*) FROM chapters;"')
            print("  2. Deploy the API Worker:")
            print("     cd apps/api && wrangler deploy --env production")
            print("  3. Validate API Worker health:")
            print("     curl https://syrabit-api-prod.<account>.workers.dev/health")
            print("  4. Set API_WORKER_LIVE=true secret on the edge worker to activate")
            print("     the service binding (only after API Worker route parity is confirmed):")
            print("     cd apps/edge && wrangler secret put API_WORKER_LIVE --env production")
            print("     (enter: true)")
    print("=" * 64)


if __name__ == "__main__":
    main()
