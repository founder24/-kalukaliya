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
recorded as JSONL, making interrupted runs safe to resume.

Run from apps/backend:
  python3 -m scripts.ahsec_d1_import --dry-run
  python3 -m scripts.ahsec_d1_import --limit 1
  python3 -m scripts.ahsec_d1_import
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
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
MIN_SOURCE_CHARS = 500
MIN_NOTES_CHARS = 800


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replace AHSEC chapter notes in Cloudflare D1 from official PDFs"
    )
    parser.add_argument("--dry-run", action="store_true")
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

    def generate(self, system_prompt: str, user_message: str) -> str:
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
                text = str(result.get("response") or "").strip()
                text = clean_notes(text)
                if len(text) > len(best):
                    best = text
                if len(text) >= MIN_NOTES_CHARS and text.startswith("##"):
                    return text
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
            f"{self.api}/vectorize/v2/indexes/{VECTOR_INDEX}/delete-by-ids",
            {"ids": ids},
        )


def clean_notes(text: str) -> str:
    text = re.sub(r"^```(?:markdown)?\s*", "", text.strip(), flags=re.I)
    text = re.sub(r"\s*```$", "", text.strip())
    first_heading = re.search(r"^##\s+\S", text, flags=re.M)
    if first_heading:
        text = text[first_heading.start() :]
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


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


def record_progress(chapter_id: str, status: str, **details: Any) -> None:
    append_jsonl(
        PROGRESS_FILE,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "chapter_id": chapter_id,
            "status": status,
            **details,
        },
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
    append_jsonl(
        BACKUP_FILE,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "chapter_id": chapter["id"],
            "subject_id": chapter["subject_id"],
            "notes_en": chapter.get("notes_en"),
            "rag_text": chapter.get("rag_text"),
            "rag_sections_en": chapter.get("rag_sections_en"),
            "source_pdf_url": source_url,
        },
    )


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


async def main() -> int:
    args = parse_args()
    client = CloudflareClient()
    chapters = fetch_chapters(client)
    if args.class_level:
        expected = "HS 1st Year" if args.class_level == "11" else "HS 2nd Year"
        chapters = [row for row in chapters if row["class_name"] == expected]
    if args.subject:
        chapters = [row for row in chapters if row["subject_slug"] == args.subject]

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

    generated_cache: dict[tuple[str, str, int], tuple[str, list[dict[str, str]]]] = {}
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