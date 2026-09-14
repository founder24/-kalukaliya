"""
AHSEC/ASSEB textbook notes importer for the Cloudflare-native production stack.

This script intentionally does not import Beanie or initialize MongoDB. It:
  1. discovers official AHSEC textbook PDFs,
  2. extracts and splits them with the proven PDF helpers,
  3. matches extracted chapters to existing AHSEC rows in D1,
  4. generates English notes with Cloudflare Workers AI,
  5. replaces notes_en/rag_text/rag_sections_en in D1, and
  6. replaces the corresponding Vectorize vectors and D1 chunk mappings.

Existing notes are backed up to JSONL before each write. Progress is also
recorded as JSONL, making interrupted runs safe to resume. Confirmed production
runs are recorded in a separate approval JSONL ledger and share a run ID with
progress and backup records.

Run from apps/backend:
  python3 -m scripts.ahsec_d1_import --dry-run
  python3 -m scripts.ahsec_d1_import --clean-preambles --dry-run
  python3 -m scripts.ahsec_d1_import --limit 1
  python3 -m scripts.ahsec_d1_import --confirm-production-write --limit 1

Cleanup safety:
  * `--clean-preambles --dry-run` creates the preview artifact.
  * A production cleanup requires that artifact to be fresh and to match the
    filters and chapter set being written.
  * Normal imports do not require a cleanup preview. The low-level
    `clean_existing_preambles(..., emergency=True)` compatibility helper is
    reserved for an explicitly authorized emergency operation.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import getpass
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from scripts.ahsec_ingest import (
    _NOTES_SYSTEM_EN,
    build_catalogue,
    extract_pdf_text,
    notes_to_rag_sections,
    split_into_chapters,
)


log = logging.getLogger("ahsec_d1_import")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID") or os.getenv("CF_ACCOUNT_ID")
API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN") or os.getenv("CF_API_TOKEN")
DATABASE_ID = os.getenv(
    "SYRABIT_D1_DATABASE_ID",
    "ff8e76ec-02c5-45f3-92ea-4d67d7d2a510",
)
VECTOR_INDEX = os.getenv("CF_VECTORIZE_INDEX_NAME", "syrabit-rag")

AI_PRIMARY = "@cf/meta/llama-3.1-8b-instruct-fast"
AI_FALLBACK = "@cf/qwen/qwen3-30b-a3b-fp8"
EMBED_MODEL = "@cf/baai/bge-m3"

STATE_DIR = Path(
    os.getenv(
        "AHSEC_D1_STATE_DIR",
        str(Path(__file__).resolve().parent.parent / ".ahsec_d1_state"),
    )
)
PROGRESS_FILE = STATE_DIR / "progress.jsonl"
BACKUP_FILE = STATE_DIR / "notes-backup.jsonl"
APPROVAL_FILE = STATE_DIR / "approvals.jsonl"
CLEANUP_PREVIEW_FILENAME = "preamble-cleanup-preview.json"
CLEANUP_PREVIEW_MAX_AGE_SECONDS = int(
    os.getenv("AHSEC_CLEANUP_PREVIEW_MAX_AGE_SECONDS", "86400")
)
MIN_SOURCE_CHARS = 500
MIN_NOTES_CHARS = 800
MAX_PREAMBLE_DIFF_LINES = 8
MAX_PREAMBLE_DIFF_LINE_CHARS = 240
ACTIVE_RUN_ID: str | None = None


class ModelPreambleError(RuntimeError):
    """Raised when Workers AI adds assistant-style introduction text."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replace AHSEC chapter notes in Cloudflare D1 from official PDFs"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--confirm-production-write",
        action="store_true",
        help=(
            "Explicitly allow writes to live Cloudflare D1 and Vectorize. "
            "Without this flag, non-dry-run execution is refused."
        ),
    )
    parser.add_argument(
        "--operator",
        help=(
            "Name or identifier recorded as the operator approving a production "
            "write (defaults to the local OS user)"
        ),
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--class", dest="class_level", choices=["11", "12"])
    parser.add_argument("--subject", help="D1 subject slug, for example chemistry")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Ignore completed progress records and regenerate matching chapters",
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="Update D1 notes but do not replace Vectorize/D1 chunk mappings",
    )
    parser.add_argument(
        "--clean-preambles",
        action="store_true",
        help=(
            "Clean model-introduction preambles from existing AHSEC notes; "
            "combine with --dry-run to preview changes"
        ),
    )
    return parser.parse_args()


class CloudflareClient:
    def __init__(self) -> None:
        if not ACCOUNT_ID or not API_TOKEN:
            raise RuntimeError(
                "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required"
            )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {API_TOKEN}",
                "Content-Type": "application/json",
            }
        )
        self.api = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"

    def _post(self, url: str, payload: Any, timeout: int = 120) -> dict[str, Any]:
        response = self.session.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        body = response.json()
        if not body.get("success", False):
            raise RuntimeError(f"Cloudflare API failed: {body.get('errors', [])}")
        return body

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        body = self._post(
            f"{self.api}/d1/database/{DATABASE_ID}/query",
            {"sql": sql, "params": params or []},
        )
        statements = body.get("result") or []
        if not statements:
            return []
        statement = statements[0]
        if not statement.get("success", True):
            raise RuntimeError(f"D1 query failed: {statement}")
        return statement.get("results") or []

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        self.query(sql, params)

    def generate(
        self,
        system_prompt: str,
        user_message: str,
        *,
        chapter_id: str | None = None,
    ) -> str:
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": 4096,
            "temperature": 0.2,
        }
        best = ""
        errors: list[str] = []
        for model in (AI_PRIMARY, AI_FALLBACK):
            try:
                body = self._post(f"{self.api}/ai/run/{model}", payload, timeout=180)
                result = body.get("result") or {}
                raw_text = str(result.get("response") or "").strip()
                if chapter_id is not None:
                    validate_generated_notes(chapter_id, raw_text)
                text = raw_text
                text = clean_notes(text)
                if len(text) > len(best):
                    best = text
                if len(text) >= MIN_NOTES_CHARS and text.startswith("##"):
                    return text
            except ModelPreambleError:
                raise
            except Exception as exc:
                errors.append(f"{model}: {exc}")
        if len(best) >= MIN_NOTES_CHARS:
            return best
        raise RuntimeError(
            f"Workers AI returned insufficient notes ({len(best)} chars); "
            + "; ".join(errors)
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        body = self._post(
            f"{self.api}/ai/run/{EMBED_MODEL}",
            {"text": texts},
            timeout=180,
        )
        result = body.get("result") or {}
        data = result.get("data") or []
        vectors: list[list[float]] = []
        for item in data:
            values = item.get("values") if isinstance(item, dict) else item
            if isinstance(values, list) and values:
                vectors.append(values)
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Embedding count mismatch: expected {len(texts)}, got {len(vectors)}"
            )
        return vectors

    def vector_upsert(self, vectors: list[dict[str, Any]]) -> None:
        if not vectors:
            return
        self._post(
            f"{self.api}/vectorize/v2/indexes/{VECTOR_INDEX}/upsert",
            {"vectors": vectors},
        )

    def vector_delete(self, ids: list[str]) -> None:
        if not ids:
            return
        self._post(
            f"{self.api}/vectorize/v2/indexes/{VECTOR_INDEX}/delete_by_ids",
            {"ids": ids},
        )


def clean_notes(text: str) -> str:
    text = re.sub(r"^```(?:markdown)?\s*", "", text.strip(), flags=re.I)
    text = re.sub(r"\s*```$", "", text.strip())
    first_heading = re.search(r"^##\s+\S", text, flags=re.M)
    if first_heading:
        text = text[first_heading.start() :]
    else:
        # Some generations use a one-line introduction but omit Markdown
        # headings. Remove only the known model-style preamble, never the
        # chapter's actual first sentence.
        text = re.sub(
            r"^\s*(?:here|below|the following) are "
            r"(?:comprehensive\s+)?(?:study\s+)?notes?\s+for the chapter"
            r"[^.\n]*[.!?]\s*(?:---\s*)?",
            "",
            text,
            count=1,
            flags=re.I,
        )
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


_MODEL_PREAMBLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "notes_introduction",
        re.compile(
            r"^\s*(?:here|below|the following)\s+(?:are|is)\b"
            r".{0,180}\b(?:study\s+)?notes?\b",
            flags=re.I | re.S,
        ),
    ),
    (
        "assistant_acknowledgement",
        re.compile(
            r"^\s*(?:sure|certainly|of course|absolutely)[!,.]?\s+"
            r"(?:here|below|i(?:'ll| will)\b)",
            flags=re.I,
        ),
    ),
    (
        "ai_disclaimer",
        re.compile(r"^\s*as an ai(?:\s+language)?\s+model\b", flags=re.I),
    ),
    (
        "first_person_offer",
        re.compile(
            r"^\s*i\s+(?:will|'ll)\s+(?:provide|present|give|create)\b",
            flags=re.I,
        ),
    ),
    (
        "notes_summary_introduction",
        re.compile(
            r"^\s*(?:these|the following)\s+(?:study\s+)?notes?\s+"
            r"(?:provide|cover|include|summarize)\b",
            flags=re.I,
        ),
    ),
)


def _bounded_diff_preview(before: str, after: str) -> tuple[str, bool]:
    diff = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="model-output",
            tofile="normalized-output",
            lineterm="",
            n=1,
        )
    )
    preview_lines = [
        line[:MAX_PREAMBLE_DIFF_LINE_CHARS]
        for line in diff[:MAX_PREAMBLE_DIFF_LINES]
    ]
    return "\n".join(preview_lines), len(diff) > MAX_PREAMBLE_DIFF_LINES


def validate_generated_notes(chapter_id: str, raw_notes: str) -> None:
    """Reject known model introductions before notes can reach D1."""
    candidate = raw_notes.lstrip()[:1000]
    for pattern_name, pattern in _MODEL_PREAMBLE_PATTERNS:
        if not pattern.search(candidate):
            continue
        normalized = clean_notes(raw_notes)
        preview, truncated = _bounded_diff_preview(raw_notes, normalized)
        diff_summary = preview or "(normalizer produced no diff)"
        if truncated:
            diff_summary += "\n... diff truncated ..."
        raise ModelPreambleError(
            f"Generated notes rejected for chapter {chapter_id}: "
            f"model preamble '{pattern_name}' detected; "
            f"raw_chars={len(raw_notes)} normalized_chars={len(normalized)}; "
            f"bounded_diff:\n{diff_summary}"
        )


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\b(chapter|unit|lesson|part)\b", " ", value, flags=re.I)
    value = re.sub(r"\b[ivxlcdm]+\b", " ", value, flags=re.I)
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return " ".join(value.split())


def chunk_text(text: str, max_words: int = 400, overlap: int = 50) -> list[str]:
    words = text.strip().split()
    if not words:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(words):
        chunks.append(" ".join(words[start : start + max_words]))
        start += max_words - overlap
    return chunks


def load_done() -> set[str]:
    if not PROGRESS_FILE.exists():
        return set()
    done: set[str] = set()
    for line in PROGRESS_FILE.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "done" and row.get("chapter_id"):
            done.add(str(row["chapter_id"]))
    return done


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def production_scope(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "class": args.class_level,
        "subject": args.subject,
        "limit": args.limit,
        "restart": args.restart,
        "skip_index": args.skip_index,
        "clean_preambles": args.clean_preambles,
    }


def record_production_approval(args: argparse.Namespace) -> tuple[str, str]:
    """Record a confirmed production run before any live data is touched."""
    if args.dry_run or not args.confirm_production_write:
        raise RuntimeError(
            "Production approval records require explicit production-write confirmation"
        )
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    operator = (args.operator or getpass.getuser()).strip()
    if not operator:
        raise RuntimeError(
            "An operator identifier is required for a production approval record"
        )
    append_jsonl(
        APPROVAL_FILE,
        {
            "event": "production_write_approved",
            "run_id": run_id,
            "operator": operator,
            "started_at": started_at,
            "approved_at": started_at,
            "scope": production_scope(args),
        },
    )
    return run_id, started_at


def record_progress(chapter_id: str, status: str, **details: Any) -> None:
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chapter_id": chapter_id,
        "status": status,
        **details,
    }
    if ACTIVE_RUN_ID:
        payload["run_id"] = ACTIVE_RUN_ID
    append_jsonl(
        PROGRESS_FILE,
        payload,
    )


def fetch_chapters(client: CloudflareClient) -> list[dict[str, Any]]:
    return client.query(
        """
        SELECT ch.id, ch.subject_id, ch.title, ch.slug, ch.chapter_number,
               ch.notes_en, ch.rag_text, ch.rag_sections_en,
               s.name AS subject_name, s.slug AS subject_slug,
               st.name AS stream_name, c.name AS class_name
        FROM chapters ch
        JOIN subjects s ON s.id = ch.subject_id
        JOIN streams st ON st.id = s.stream_id
        JOIN classes c ON c.id = st.class_id
        JOIN boards b ON b.id = c.board_id
        WHERE b.slug = 'ahsec'
        ORDER BY c.name, s.slug, ch.chapter_number, ch.title
        """
    )


async def extract_sources(args: argparse.Namespace) -> dict[tuple[str, str], list[dict[str, Any]]]:
    catalogue = build_catalogue(class11=True, class12=True)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in catalogue:
        class_level = entry["class_level"]
        subject_slug = entry["subject_slug"]
        if args.class_level and class_level != args.class_level:
            continue
        if args.subject and subject_slug != args.subject:
            continue
        grouped[(class_level, subject_slug)].append(entry)

    extracted: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for key, entries in sorted(grouped.items()):
        # Prefer official English books. Assamese is a fallback only when no
        # English book exists for this class/subject.
        english = [row for row in entries if row["medium"] == "en"]
        selected = english or [row for row in entries if row["medium"] == "as"]
        selected.sort(key=lambda row: (row["part_num"], row["pdf_url"]))

        source_chapters: list[dict[str, Any]] = []
        running_max = 0
        for entry in selected:
            log.info(
                "Extracting Class %s %s (%s, part %s)",
                key[0],
                key[1],
                entry["medium"],
                entry["part_num"],
            )
            pages = await extract_pdf_text(entry["pdf_url"], entry["medium"])
            split = split_into_chapters(pages, entry["medium"])
            if not split:
                log.warning("No chapters detected in %s", entry["pdf_url"])
                continue
            minimum = min(int(row["chapter_num"]) for row in split)
            offset = running_max if running_max and minimum <= running_max else 0
            for row in split:
                source_chapters.append(
                    {
                        **row,
                        "effective_number": int(row["chapter_num"]) + offset,
                        "source_pdf_url": entry["pdf_url"],
                        "source_medium": entry["medium"],
                        "subject_name": entry["subject_name"],
                    }
                )
            running_max = max(
                running_max,
                max(int(row["chapter_num"]) + offset for row in split),
            )
        if source_chapters:
            extracted[key] = source_chapters
    return extracted


def match_source(
    chapter: dict[str, Any], sources: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, float]:
    wanted_title = normalize(str(chapter.get("title") or ""))
    best: dict[str, Any] | None = None
    best_score = 0.0
    best_title_score = 0.0
    best_number_match = False
    for source in sources:
        source_title = normalize(str(source.get("title") or ""))
        title_score = difflib.SequenceMatcher(None, wanted_title, source_title).ratio()
        number_match = chapter.get("chapter_number") == source.get("effective_number")
        score = title_score
        if number_match:
            score += 0.22
        if score > best_score:
            best, best_score = source, score
            best_title_score = title_score
            best_number_match = number_match
    # Never accept a merely similar title because its chapter number is nearby.
    # A strong title is sufficient; a weaker/OCR-damaged title must also have
    # the exact chapter number detected from the official PDF.
    strong_title = best_title_score >= 0.72
    numbered_ocr_title = best_number_match and best_title_score >= 0.30
    if not (strong_title or numbered_ocr_title):
        return None, best_score
    return best, best_score


def build_prompt(chapter: dict[str, Any], source: dict[str, Any]) -> str:
    source_text = str(source["body_text"]).strip()
    return (
        f"Board: AHSEC/ASSEB\n"
        f"Class: {chapter['class_name']}\n"
        f"Subject: {chapter['subject_name']}\n"
        f"Chapter: {chapter['title']}\n\n"
        f"Use only the official textbook chapter content below. "
        f"Write complete English study notes even when the source text is Assamese. "
        f"Do not add facts that are not supported by the source.\n\n"
        f"--- OFFICIAL CHAPTER CONTENT ---\n{source_text[:15000]}\n\n"
        f"Begin with the first ## heading and no introduction."
    )


def backup_existing(chapter: dict[str, Any], source_url: str) -> None:
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chapter_id": chapter["id"],
        "subject_id": chapter["subject_id"],
        "notes_en": chapter.get("notes_en"),
        "rag_text": chapter.get("rag_text"),
        "rag_sections_en": chapter.get("rag_sections_en"),
        "source_pdf_url": source_url,
    }
    if ACTIVE_RUN_ID:
        payload["run_id"] = ACTIVE_RUN_ID
    append_jsonl(BACKUP_FILE, payload)


def write_notes(
    client: CloudflareClient,
    chapter: dict[str, Any],
    notes: str,
    sections: list[dict[str, str]],
    source_url: str,
) -> None:
    now = int(time.time())
    sections_json = json.dumps(sections, ensure_ascii=False)
    client.execute(
        """
        UPDATE chapters
        SET notes_en = ?, rag_text = ?, rag_sections_en = ?,
            word_count_en = ?, rag_updated_at = ?, updated_at = ?
        WHERE id = ?
        """,
        [
            notes,
            notes,
            sections_json,
            len(notes.split()),
            now,
            now,
            chapter["id"],
        ],
    )
    provenance = {
        "provider": "AHSEC/ASSEB",
        "official": True,
        "sourceUrl": source_url,
        "className": chapter["class_name"],
        "subjectSlug": chapter["subject_slug"],
        "chapterTitle": chapter["title"],
    }
    client.execute(
        """
        INSERT INTO rag_documents
          (id, chapter_id, subject_id, source_type, medium, content, metadata,
           indexed_at, created_at)
        VALUES (?, ?, ?, 'notes', 'english', ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          content = excluded.content,
          metadata = excluded.metadata,
          indexed_at = excluded.indexed_at
        """,
        [
            f"ahsec-notes-en:{chapter['id']}",
            chapter["id"],
            chapter["subject_id"],
            notes,
            json.dumps(provenance, ensure_ascii=False),
            now,
            now,
        ],
    )


def replace_index(
    client: CloudflareClient,
    chapter: dict[str, Any],
    notes: str,
    source_url: str,
) -> int:
    text_chunks = chunk_text(notes)
    embeddings = client.embed(text_chunks)

    old = client.query(
        """
        SELECT vector_id FROM chunks
        WHERE chapter_id = ? AND source_type = 'notes' AND medium = 'english'
        """,
        [chapter["id"]],
    )
    old_ids = [str(row["vector_id"]) for row in old if row.get("vector_id")]
    client.vector_delete(old_ids)

    vectors: list[dict[str, Any]] = []
    rows: list[tuple[str, str, str]] = []
    for index, (content, values) in enumerate(zip(text_chunks, embeddings)):
        vector_id = f"{chapter['id']}_english_notes_{index}"
        metadata = {
            "chapterId": chapter["id"],
            "subjectId": chapter["subject_id"],
            "medium": "english",
            "sourceType": "notes",
            "chunkType": "text",
            "content": content[:512],
        }
        vectors.append({"id": vector_id, "values": values, "metadata": metadata})
        rows.append((vector_id, content, json.dumps({**metadata, "sourceUrl": source_url})))
    client.vector_upsert(vectors)

    client.execute(
        """
        DELETE FROM chunks
        WHERE chapter_id = ? AND source_type = 'notes' AND medium = 'english'
        """,
        [chapter["id"]],
    )
    if rows:
        placeholders = ",".join(["(?, ?, ?, ?, 'notes', 'english', 'text', ?, ?, ?, ?)"] * len(rows))
        params: list[Any] = []
        now = int(time.time())
        for vector_id, content, metadata in rows:
            params.extend(
                [
                    str(uuid.uuid4()),
                    f"ahsec-notes-en:{chapter['id']}",
                    chapter["id"],
                    chapter["subject_id"],
                    content,
                    vector_id,
                    metadata,
                    now,
                ]
            )
        client.execute(
            f"""
            INSERT INTO chunks
              (id, document_id, chapter_id, subject_id, source_type, medium,
               chunk_type, content, vector_id, metadata, created_at)
            VALUES {placeholders}
            """,
            params,
        )
    client.execute(
        "UPDATE chapters SET rag_indexed_at = ? WHERE id = ?",
        [int(time.time()), chapter["id"]],
    )
    return len(rows)


def build_preamble_cleanup_plan(
    chapters: list[dict[str, Any]], max_diff_lines: int = 20
) -> list[dict[str, Any]]:
    """Build the cleanup changes without mutating chapters or external state."""
    planned: list[dict[str, Any]] = []
    for chapter in chapters:
        original = str(chapter.get("notes_en") or "")
        cleaned = clean_notes(original)
        if not original or cleaned == original:
            continue
        sections = notes_to_rag_sections(cleaned)
        if not sections:
            log.warning("Skipping cleanup for %s: no sections after scrub", chapter["id"])
            continue
        diff = list(
            difflib.unified_diff(
                original.splitlines(),
                cleaned.splitlines(),
                fromfile="before",
                tofile="after",
                lineterm="",
                n=1,
            )
        )
        changed_lines = [
            line
            for line in diff
            if (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
        ]
        planned.append(
            {
                **chapter,
                "notes_en": cleaned,
                "_cleanup_original_notes": original,
                "_cleanup_sections": sections,
                "_cleanup_diff": {
                    "original_chars": len(original),
                    "cleaned_chars": len(cleaned),
                    "removed_chars": max(0, len(original) - len(cleaned)),
                    "original_words": len(original.split()),
                    "cleaned_words": len(cleaned.split()),
                    "changed_lines": len(changed_lines),
                    "preview": "\n".join(diff[:max_diff_lines]),
                    "truncated": len(diff) > max_diff_lines,
                },
            }
        )
    return planned


def apply_preamble_cleanup(
    client: CloudflareClient, planned: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply a previously built cleanup plan to D1."""
    affected: list[dict[str, Any]] = []
    for chapter in planned:
        cleaned = str(chapter["notes_en"])
        sections = chapter["_cleanup_sections"]
        backup_existing(
            {
                **chapter,
                "notes_en": chapter["_cleanup_original_notes"],
            },
            "existing-d1-preamble-cleanup",
        )
        now = int(time.time())
        client.execute(
            """
            UPDATE chapters
            SET notes_en = ?, rag_text = ?, rag_sections_en = ?,
                word_count_en = ?, rag_updated_at = ?, updated_at = ?
            WHERE id = ?
            """,
            [
                cleaned,
                cleaned,
                json.dumps(sections, ensure_ascii=False),
                len(cleaned.split()),
                now,
                now,
                chapter["id"],
            ],
        )
        client.execute(
            """
            UPDATE rag_documents
            SET content = ?, indexed_at = ?
            WHERE id = ?
            """,
            [cleaned, now, f"ahsec-notes-en:{chapter['id']}"],
        )
        affected.append(chapter)
        log.info("Removed preamble from %s (%s)", chapter["id"], chapter["title"])
        # The caller reindexes after all D1 updates so cleanup remains bounded.
    return affected


def clean_existing_preambles(
    client: CloudflareClient, chapters: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Compatibility wrapper that plans and applies cleanup in one call."""
    return apply_preamble_cleanup(client, build_preamble_cleanup_plan(chapters))


def cleanup_preview_record(chapter: dict[str, Any]) -> dict[str, Any]:
    diff = chapter["_cleanup_diff"]
    return {
        "chapter_id": str(chapter["id"]),
        "class_name": chapter.get("class_name"),
        "subject": chapter.get("subject_name"),
        "title": chapter.get("title"),
        **diff,
    }


def write_cleanup_preview_report(planned: list[dict[str, Any]]) -> Path:
    """Persist a bounded, non-D1 preview report for operators and automation."""
    report_path = STATE_DIR / "preamble-cleanup-preview.json"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "mode": "preview",
                "changed": len(planned),
                "chapter_ids": [str(chapter["id"]) for chapter in planned],
                "changes": [cleanup_preview_record(chapter) for chapter in planned],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report_path


def log_cleanup_preview(planned: list[dict[str, Any]]) -> None:
    log.info("Preamble cleanup preview: changed=%d", len(planned))
    for chapter in planned:
        diff = chapter["_cleanup_diff"]
        log.info(
            "CLEANUP PREVIEW chapter_id=%s title=%s chars=%d -> %d "
            "(removed=%d, changed_lines=%d)",
            chapter["id"],
            chapter["title"],
            diff["original_chars"],
            diff["cleaned_chars"],
            diff["removed_chars"],
            diff["changed_lines"],
        )
        if diff["preview"]:
            for line in diff["preview"].splitlines():
                log.info("  %s", line)
        if diff["truncated"]:
            log.info("  ... diff truncated in preview report")


async def main() -> int:
    global ACTIVE_RUN_ID
    args = parse_args()
    ACTIVE_RUN_ID = None
    if not args.dry_run and not args.confirm_production_write:
        log.error(
            "Refusing to write live curriculum data without explicit confirmation."
        )
        log.error(
            "Run with --dry-run for a read-only import, or add "
            "--confirm-production-write for a deliberate production import."
        )
        return 2
    if args.dry_run:
        log.info(
            "Read-only dry-run: no Cloudflare D1 or Vectorize writes will be made. "
            "Use --confirm-production-write only for a deliberate production import."
        )
    else:
        ACTIVE_RUN_ID, started_at = record_production_approval(args)
        log.info(
            "Production write approved by %s (run_id=%s, started_at=%s)",
            args.operator or getpass.getuser(),
            ACTIVE_RUN_ID,
            started_at,
        )
    client = CloudflareClient()
    chapters = fetch_chapters(client)
    if args.class_level:
        expected = "HS 1st Year" if args.class_level == "11" else "HS 2nd Year"
        chapters = [row for row in chapters if row["class_name"] == expected]
    if args.subject:
        chapters = [row for row in chapters if row["subject_slug"] == args.subject]

    if args.clean_preambles:
        planned = await asyncio.to_thread(build_preamble_cleanup_plan, chapters)
        if args.dry_run:
            log_cleanup_preview(planned)
            report_path = write_cleanup_preview_report(planned)
            log.info("Cleanup preview report: %s", report_path)
            return 0
        affected = await asyncio.to_thread(apply_preamble_cleanup, client, planned)
        if not args.skip_index:
            for chapter in affected:
                await asyncio.to_thread(
                    replace_index,
                    client,
                    chapter,
                    str(chapter.get("notes_en") or ""),
                    "existing-d1-preamble-cleanup",
                )
        log.info("Preamble cleanup complete: changed=%d", len(affected))
        return 0

    sources = await extract_sources(args)
    done = set() if args.restart else load_done()
    matches: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    unmatched: list[dict[str, Any]] = []
    for chapter in chapters:
        class_level = "11" if chapter["class_name"] == "HS 1st Year" else "12"
        candidates = sources.get((class_level, chapter["subject_slug"]), [])
        source, score = match_source(chapter, candidates)
        if source and len(str(source.get("body_text") or "")) >= MIN_SOURCE_CHARS:
            matches.append((chapter, source, score))
        else:
            unmatched.append(
                {
                    "chapter_id": chapter["id"],
                    "class_name": chapter["class_name"],
                    "subject": chapter["subject_name"],
                    "title": chapter["title"],
                    "best_score": round(score, 3),
                }
            )

    log.info(
        "Inventory: %d D1 chapters, %d matched to official source, %d unmatched",
        len(chapters),
        len(matches),
        len(unmatched),
    )
    if unmatched:
        unmatched_path = STATE_DIR / "unmatched.json"
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        unmatched_path.write_text(
            json.dumps(unmatched, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.warning("Unmatched report: %s", unmatched_path)

    if args.dry_run:
        for chapter, source, score in matches[:20]:
            log.info(
                "MATCH %.2f %s / %s / %s <- %s",
                score,
                chapter["class_name"],
                chapter["subject_name"],
                chapter["title"],
                source["title"],
            )
        return 0

    # The prompt contains destination class/subject/chapter identity. Do not
    # reuse a generation merely because two PDFs happened to expose the same
    # title/number: that can silently write notes for the wrong D1 chapter.
    generated_cache: dict[tuple[str, str, int, str, str], tuple[str, list[dict[str, str]]]] = {}
    processed = 0
    failed = 0
    for chapter, source, score in matches:
        chapter_id = str(chapter["id"])
        if chapter_id in done:
            continue
        if args.limit is not None and processed >= args.limit:
            break
        source_key = (
            str(source["source_pdf_url"]),
            normalize(str(source["title"])),
            int(source["effective_number"]),
            str(chapter["subject_id"]),
            str(chapter["id"]),
        )
        try:
            if source_key not in generated_cache:
                log.info(
                    "Generating %s / %s / %s (match %.2f)",
                    chapter["class_name"],
                    chapter["subject_name"],
                    chapter["title"],
                    score,
                )
                notes = await asyncio.to_thread(
                    client.generate,
                    _NOTES_SYSTEM_EN,
                    build_prompt(chapter, source),
                    chapter_id=chapter_id,
                )
                sections = notes_to_rag_sections(notes)
                if not sections:
                    raise RuntimeError("Generated notes had no usable RAG sections")
                generated_cache[source_key] = (notes, sections)
            notes, sections = generated_cache[source_key]

            backup_existing(chapter, str(source["source_pdf_url"]))
            await asyncio.to_thread(
                write_notes,
                client,
                chapter,
                notes,
                sections,
                str(source["source_pdf_url"]),
            )
            chunk_count = 0
            if not args.skip_index:
                chunk_count = await asyncio.to_thread(
                    replace_index,
                    client,
                    chapter,
                    notes,
                    str(source["source_pdf_url"]),
                )
            record_progress(
                chapter_id,
                "done",
                source_pdf_url=source["source_pdf_url"],
                note_chars=len(notes),
                rag_sections=len(sections),
                chunks=chunk_count,
            )
            processed += 1
            log.info("Updated %s (%d chars, %d chunks)", chapter_id, len(notes), chunk_count)
        except Exception as exc:
            failed += 1
            record_progress(chapter_id, "error", error=str(exc))
            log.exception("Failed chapter %s: %s", chapter_id, exc)
        await asyncio.sleep(max(0.0, args.delay))

    log.info("Run complete: updated=%d failed=%d", processed, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))