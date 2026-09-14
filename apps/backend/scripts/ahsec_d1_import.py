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
    filters, chapter set, and note content being written.
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
from datetime import datetime, timedelta, timezone
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
from app.services.ai.note_quality import (
    ModelPreambleError,
    validate_generated_notes as _validate_generated_notes,
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
ARCHIVE_RETENTION_DAYS = int(os.getenv("AHSEC_D1_ARCHIVE_RETENTION_DAYS", "90"))
CLEANUP_PREVIEW_FILENAME = "preamble-cleanup-preview.json"
CLEANUP_PREVIEW_MAX_AGE_SECONDS = int(
    os.getenv("AHSEC_CLEANUP_PREVIEW_MAX_AGE_SECONDS", "86400")
)
MIN_SOURCE_CHARS = 500
MIN_NOTES_CHARS = 800
TERMINAL_SUMMARY_EVENT = "import_terminal_summary"
INDEX_FAILED_STATUS = "index_failed"
INDEX_REPAIRED_STATUS = "index_repaired"
MAX_INDEX_REPAIR_CHAPTERS = 10
MAX_INDEX_REPAIR_ATTEMPTS = 3
ACTIVE_RUN_ID: str | None = None
ACTIVE_RUN_COUNTS = {"completed": 0, "failed": 0}


class IndexReplacementError(RuntimeError):
    """Identify which non-atomic index replacement step failed."""

    def __init__(self, operation: str, cause: Exception) -> None:
        self.operation = operation
        super().__init__(f"{operation} failed: {cause}")


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
        "--repair-index",
        "--retry-index",
        dest="repair_index",
        action="append",
        metavar="CHAPTER_ID",
        help=(
            "Rebuild Vectorize and D1 chunk mappings from the chapter's stored "
            "notes after an index_failed result; may be repeated up to "
            f"{MAX_INDEX_REPAIR_CHAPTERS} times"
        ),
    )
    parser.add_argument(
        "--clean-preambles",
        action="store_true",
        help=(
            "Clean model-introduction preambles from existing AHSEC notes; "
            "combine with --dry-run to preview changes"
        ),
    )
    parser.add_argument(
        "--cleanup-preview-report",
        type=Path,
        help=(
            "Path to the cleanup preview JSON artifact. Preview mode writes to "
            "this path and production cleanup reads the transferred artifact "
            "from the same path; defaults to the local importer state directory."
        ),
    )
    parser.add_argument(
        "--archive-history",
        action="store_true",
        help=(
            "Archive approval, progress, and backup records older than the "
            "retention window; combine with --dry-run to preview only"
        ),
    )
    parser.add_argument(
        "--archive-before-days",
        type=int,
        default=ARCHIVE_RETENTION_DAYS,
        help=(
            "Keep this many days of live audit history when --archive-history "
            "is used (default: %(default)s)"
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


def validate_generated_notes(chapter_id: str, raw_notes: str) -> None:
    """Reject known model introductions before notes can reach D1."""
    _validate_generated_notes(
        chapter_id,
        raw_notes,
        normalizer=clean_notes,
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


def _progress_files() -> list[Path]:
    progress_files: list[Path] = []
    archive_dir = STATE_DIR / "archive"
    if archive_dir.exists():
        progress_files.extend(sorted(archive_dir.glob("*/progress.jsonl")))
    progress_files.append(PROGRESS_FILE)
    return progress_files


def _latest_progress_by_chapter() -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for progress_file in _progress_files():
        if not progress_file.exists():
            continue
        for line in progress_file.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            chapter_id = str(row.get("chapter_id") or "").strip()
            if chapter_id:
                latest[chapter_id] = row
    return latest


def load_done() -> set[str]:
    done: set[str] = set()
    latest = _latest_progress_by_chapter()
    for chapter_id, row in latest.items():
        if row.get("status") in {"done", INDEX_REPAIRED_STATUS}:
            done.add(chapter_id)
    return done


def _repair_index_ids(args: argparse.Namespace) -> list[str]:
    raw_ids = getattr(args, "repair_index", None) or []
    chapter_ids = list(
        dict.fromkeys(
            str(value).strip() for value in raw_ids if str(value).strip()
        )
    )
    if len(chapter_ids) > MAX_INDEX_REPAIR_CHAPTERS:
        raise ValueError(
            f"At most {MAX_INDEX_REPAIR_CHAPTERS} chapters may be repaired per run"
        )
    return chapter_ids


def _next_index_attempt(chapter_id: str) -> int:
    previous = _latest_progress_by_chapter().get(str(chapter_id))
    if not previous or previous.get("status") != INDEX_FAILED_STATUS:
        return 1
    try:
        return max(1, int(previous.get("index_attempt") or 1) + 1)
    except (TypeError, ValueError):
        return 2


def _record_index_failure(
    chapter_id: str,
    error: Exception | str,
    *,
    attempt: int,
    operation: str,
    notes_written: bool = True,
    source_pdf_url: str | None = None,
) -> None:
    details: dict[str, Any] = {
        "phase": "index",
        "operation": operation,
        "notes_written": notes_written,
        "index_attempt": attempt,
        "error": str(error),
        "repairable": True,
        "repair_command": (
            "python3 -m scripts.ahsec_d1_import "
            "--confirm-production-write --repair-index "
            f"{chapter_id}"
        ),
    }
    if source_pdf_url:
        details["source_pdf_url"] = source_pdf_url
    record_progress(chapter_id, INDEX_FAILED_STATUS, **details)


def _index_failure_operation(error: Exception, fallback: str) -> str:
    operation = getattr(error, "operation", None)
    return str(operation or fallback)


def _index_repair_attempt(chapter_id: str) -> int:
    previous = _latest_progress_by_chapter().get(str(chapter_id))
    if not previous or previous.get("status") != INDEX_FAILED_STATUS:
        return 1
    try:
        attempt = int(previous.get("index_attempt") or 1)
    except (TypeError, ValueError):
        attempt = 1
    return attempt + 1


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _active_run_path() -> Path:
    return STATE_DIR / "active-run.json"


def _set_active_run(run_id: str, started_at: str) -> None:
    """Publish the run before its approval record so archival fails closed."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _active_run_path()
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(
            {"run_id": run_id, "started_at": started_at, "pid": os.getpid()},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _clear_active_run(run_id: str | None = None) -> None:
    path = _active_run_path()
    if not path.exists():
        return
    try:
        active = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if run_id and str(active.get("run_id") or "") != run_id:
        return
    path.unlink(missing_ok=True)


def _active_run_ids() -> set[str]:
    active_ids: set[str] = set()
    if ACTIVE_RUN_ID:
        active_ids.add(ACTIVE_RUN_ID)
    path = _active_run_path()
    if not path.exists():
        return active_ids
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"*"}
    run_id = str(record.get("run_id") or "").strip()
    if run_id:
        active_ids.add(run_id)
    return active_ids


def _jsonl_lines(path: Path) -> list[tuple[str, dict[str, Any] | None]]:
    if not path.exists():
        return []
    lines: list[tuple[str, dict[str, Any] | None]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            record = None
        lines.append((raw_line, record if isinstance(record, dict) else None))
    return lines


def _approval_run_ids_before(
    cutoff: datetime,
    approval_lines: list[tuple[str, dict[str, Any] | None]],
) -> set[str]:
    eligible: set[str] = set()
    for _, record in approval_lines:
        if not record or record.get("event") != "production_write_approved":
            continue
        run_id = str(record.get("run_id") or "").strip()
        started_at = record.get("started_at")
        if not run_id or not isinstance(started_at, str):
            continue
        try:
            approved_at = datetime.fromisoformat(started_at)
        except ValueError:
            continue
        if approved_at.tzinfo is None:
            approved_at = approved_at.replace(tzinfo=timezone.utc)
        if approved_at.astimezone(timezone.utc) < cutoff:
            eligible.add(run_id)
    return eligible


def archive_history(before_days: int, dry_run: bool = False) -> dict[str, Any]:
    """Archive old audit records while preserving active-run state."""
    if before_days < 1:
        raise ValueError("--archive-before-days must be at least 1")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=before_days)
    approval_lines = _jsonl_lines(APPROVAL_FILE)
    candidate_run_ids = _approval_run_ids_before(cutoff, approval_lines)
    protected_run_ids = _active_run_ids()
    eligible_run_ids = (
        set()
        if "*" in protected_run_ids
        else candidate_run_ids - protected_run_ids
    )

    files = {
        "approvals": APPROVAL_FILE,
        "progress": PROGRESS_FILE,
        "notes-backup": BACKUP_FILE,
    }
    selected: dict[str, list[str]] = {}
    retained: dict[Path, list[str]] = {}
    counts: dict[str, int] = {}
    for name, path in files.items():
        selected[name] = []
        retained[path] = []
        for raw_line, record in _jsonl_lines(path):
            run_id = str(record.get("run_id") or "").strip() if record else ""
            if run_id in eligible_run_ids:
                selected[name].append(raw_line)
            else:
                retained[path].append(raw_line)
        counts[name] = len(selected[name])

    summary: dict[str, Any] = {
        "before_days": before_days,
        "cutoff": cutoff.isoformat(),
        "candidate_runs": len(candidate_run_ids),
        "archivable_runs": len(eligible_run_ids),
        "protected_runs": sorted(
            run_id for run_id in protected_run_ids if run_id != "*"
        ),
        "records": counts,
        "dry_run": dry_run,
    }
    if dry_run or not eligible_run_ids:
        return summary

    archive_root = STATE_DIR / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    batch_name = now.strftime("%Y%m%dT%H%M%SZ")
    batch_dir = archive_root / batch_name
    suffix = 1
    while batch_dir.exists():
        batch_dir = archive_root / f"{batch_name}-{suffix}"
        suffix += 1
    batch_dir.mkdir()

    for name, lines in selected.items():
        if lines:
            (batch_dir / f"{name}.jsonl").write_text(
                "".join(lines), encoding="utf-8"
            )

    manifest = {
        "archived_at": now.isoformat(),
        "cutoff": cutoff.isoformat(),
        "before_days": before_days,
        "run_ids": sorted(eligible_run_ids),
        "records": counts,
        "source_files": {name: str(path) for name, path in files.items()},
        "active_runs_protected": summary["protected_runs"],
    }
    (batch_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for path, lines in retained.items():
        if not path.exists():
            continue
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text("".join(lines), encoding="utf-8")
        os.replace(temporary, path)
    summary["archive_dir"] = str(batch_dir)
    return summary


def production_scope(args: argparse.Namespace) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "class": args.class_level,
        "subject": args.subject,
        "limit": args.limit,
        "restart": args.restart,
        "skip_index": args.skip_index,
        "clean_preambles": args.clean_preambles,
    }
    repair_index = _repair_index_ids(args)
    if repair_index:
        scope["repair_index"] = repair_index
    return scope


def cleanup_preview_scope(
    args: argparse.Namespace, chapter_ids: list[str]
) -> dict[str, Any]:
    """Return the canonical filters and exact chapter set for cleanup."""
    return {
        **production_scope(args),
        "chapter_ids": sorted({str(chapter_id) for chapter_id in chapter_ids}),
    }


def cleanup_preview_fingerprint(scope: dict[str, Any]) -> str:
    encoded = json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def cleanup_preview_path(args: argparse.Namespace) -> Path:
    """Resolve the preview artifact path shared by preview and apply runners."""
    configured_path = getattr(args, "cleanup_preview_report", None)
    if configured_path:
        return Path(configured_path).expanduser()
    return STATE_DIR / CLEANUP_PREVIEW_FILENAME


def record_production_approval(
    args: argparse.Namespace, extra_scope: dict[str, Any] | None = None
) -> tuple[str, str]:
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
    _set_active_run(run_id, started_at)
    scope = production_scope(args)
    if extra_scope:
        scope.update(extra_scope)
    append_jsonl(
        APPROVAL_FILE,
        {
            "event": "production_write_approved",
            "run_id": run_id,
            "operator": operator,
            "started_at": started_at,
            "approved_at": started_at,
            "scope": scope,
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


def record_terminal_summary(
    status: str,
    *,
    completed: int,
    failed: int,
) -> None:
    """Append bounded run-level state without copying chapter data or errors."""
    if not ACTIVE_RUN_ID:
        return
    if status not in {"completed", "failed"}:
        raise ValueError(f"Unsupported terminal status: {status}")
    completed = max(0, int(completed))
    failed = max(0, int(failed))
    append_jsonl(
        PROGRESS_FILE,
        {
            "event": TERMINAL_SUMMARY_EVENT,
            "run_id": ACTIVE_RUN_ID,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "chapters": completed + failed,
            "completed": completed,
            "failed": failed,
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
    try:
        client.vector_delete(old_ids)
    except Exception as exc:
        raise IndexReplacementError("vector_delete", exc) from exc

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
    try:
        client.vector_upsert(vectors)
    except Exception as exc:
        raise IndexReplacementError("vector_upsert", exc) from exc

    try:
        client.execute(
            """
            DELETE FROM chunks
            WHERE chapter_id = ? AND source_type = 'notes' AND medium = 'english'
            """,
            [chapter["id"]],
        )
    except Exception as exc:
        raise IndexReplacementError("chunk_mapping_delete", exc) from exc
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
        try:
            client.execute(
                f"""
                INSERT INTO chunks
                  (id, document_id, chapter_id, subject_id, source_type, medium,
                   chunk_type, content, vector_id, metadata, created_at)
                VALUES {placeholders}
                """,
                params,
            )
        except Exception as exc:
            raise IndexReplacementError("chunk_mapping_insert", exc) from exc
    try:
        client.execute(
            "UPDATE chapters SET rag_indexed_at = ? WHERE id = ?",
            [int(time.time()), chapter["id"]],
        )
    except Exception as exc:
        raise IndexReplacementError("chunk_mapping_timestamp", exc) from exc
    return len(rows)


async def repair_indexes(
    client: CloudflareClient,
    chapters: list[dict[str, Any]],
    chapter_ids: list[str],
) -> int:
    """Retry indexing from stored notes without regenerating or rewriting notes."""
    global ACTIVE_RUN_COUNTS

    chapters_by_id = {str(chapter["id"]): chapter for chapter in chapters}
    missing = [
        chapter_id for chapter_id in chapter_ids if chapter_id not in chapters_by_id
    ]
    if missing:
        raise RuntimeError(
            "Requested index repair chapter(s) were not found in the selected "
            f"AHSEC chapters: {', '.join(missing)}"
        )

    repaired = 0
    failed = 0
    for chapter_id in chapter_ids:
        chapter = chapters_by_id[chapter_id]
        attempt = _index_repair_attempt(chapter_id)
        if attempt > MAX_INDEX_REPAIR_ATTEMPTS:
            error = (
                f"Index repair attempt limit reached ({MAX_INDEX_REPAIR_ATTEMPTS}); "
                "inspect the Vectorize/D1 failure before retrying"
            )
            failed += 1
            _record_index_failure(
                chapter_id,
                error,
                attempt=attempt,
                operation="index_repair",
                notes_written=bool(str(chapter.get("notes_en") or "").strip()),
            )
            log.error("%s: %s", chapter_id, error)
            continue

        notes = str(chapter.get("notes_en") or "").strip()
        try:
            if not notes:
                raise RuntimeError("Chapter has no stored English notes to index")
            chunk_count = await asyncio.to_thread(
                replace_index,
                client,
                chapter,
                notes,
                "index-repair",
            )
            record_progress(
                chapter_id,
                INDEX_REPAIRED_STATUS,
                operation="index_repair",
                index_attempt=attempt,
                note_chars=len(notes),
                chunks=chunk_count,
            )
            repaired += 1
            log.info(
                "Repaired index for %s (%d chars, %d chunks)",
                chapter_id,
                len(notes),
                chunk_count,
            )
        except Exception as exc:
            failed += 1
            _record_index_failure(
                chapter_id,
                exc,
                attempt=attempt,
                operation=_index_failure_operation(exc, "index_repair"),
                notes_written=bool(notes),
            )
            log.exception("Index repair failed for %s: %s", chapter_id, exc)

    ACTIVE_RUN_COUNTS["completed"] = repaired
    ACTIVE_RUN_COUNTS["failed"] = failed
    record_terminal_summary(
        "failed" if failed else "completed",
        completed=repaired,
        failed=failed,
    )
    _clear_active_run(ACTIVE_RUN_ID)
    return 1 if failed else 0


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


def cleanup_note_digest(notes: str | None) -> str:
    """Return a stable digest for the note content reviewed by cleanup."""
    return hashlib.sha256(str(notes or "").encode("utf-8")).hexdigest()


def cleanup_plan_digests(planned: list[dict[str, Any]]) -> dict[str, str]:
    """Return the original note digest for every planned chapter."""
    return {
        str(chapter["id"]): cleanup_note_digest(
            str(
                chapter.get(
                    "_cleanup_original_notes",
                    chapter.get("notes_en") or "",
                )
            )
        )
        for chapter in planned
    }


def validate_cleanup_preview_content(
    preview: dict[str, Any],
    planned: list[dict[str, Any]],
) -> None:
    """Ensure a preview describes the exact note content being cleaned."""
    changes = preview.get("changes")
    if not isinstance(changes, list):
        raise RuntimeError(
            "Cleanup preview is missing per-chapter note content evidence. "
            "Regenerate it with --clean-preambles --dry-run."
        )
    preview_digests = {
        str(change.get("chapter_id")): change.get("notes_en_digest")
        for change in changes
        if isinstance(change, dict) and change.get("chapter_id") is not None
    }
    expected_digests = cleanup_plan_digests(planned)
    if (
        set(preview_digests) != set(expected_digests)
        or any(
            preview_digests.get(chapter_id) != digest
            for chapter_id, digest in expected_digests.items()
        )
    ):
        raise RuntimeError(
            "Cleanup preview does not match the current chapter note content. "
            "The reviewed notes changed after preview generation; regenerate "
            "it with --clean-preambles --dry-run."
        )


def apply_preamble_cleanup(
    client: CloudflareClient,
    planned: list[dict[str, Any]],
    *,
    preview: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Apply a cleanup plan after the caller proves preview evidence."""
    if preview is None:
        raise RuntimeError(
            "Cleanup writes require a matching preview artifact. "
            "Run --clean-preambles --dry-run first."
        )
    if not preview.get("emergency"):
        validate_cleanup_preview_content(preview, planned)
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
    client: CloudflareClient,
    chapters: list[dict[str, Any]],
    *,
    emergency: bool = False,
) -> list[dict[str, Any]]:
    """Compatibility wrapper for an explicitly authorized emergency cleanup."""
    if not emergency:
        raise RuntimeError(
            "The compatibility cleanup helper is emergency-only. "
            "Use the preview workflow or pass emergency=True explicitly."
        )
    return apply_preamble_cleanup(
        client,
        build_preamble_cleanup_plan(chapters),
        preview={"emergency": True},
    )


def cleanup_preview_record(chapter: dict[str, Any]) -> dict[str, Any]:
    diff = chapter["_cleanup_diff"]
    return {
        "chapter_id": str(chapter["id"]),
        "class_name": chapter.get("class_name"),
        "subject": chapter.get("subject_name"),
        "title": chapter.get("title"),
        "notes_en_digest": cleanup_note_digest(
            str(chapter.get("_cleanup_original_notes") or "")
        ),
        **diff,
    }


def write_cleanup_preview_report(
    planned: list[dict[str, Any]],
    scope: dict[str, Any] | None = None,
    report_path: Path | None = None,
) -> Path:
    """Persist a bounded, non-D1 preview report for operators and automation."""
    report_path = report_path or STATE_DIR / CLEANUP_PREVIEW_FILENAME
    report_path = Path(report_path).expanduser()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_ids = sorted({str(chapter["id"]) for chapter in planned})
    resolved_scope = scope or {
        "chapter_ids": chapter_ids,
    }
    generated_at = datetime.now(timezone.utc)
    report = {
        "generated_at": generated_at.isoformat(),
        "mode": "preview",
        "changed": len(planned),
        "chapter_ids": chapter_ids,
        "scope": resolved_scope,
        "scope_fingerprint": cleanup_preview_fingerprint(resolved_scope),
        "changes": [cleanup_preview_record(chapter) for chapter in planned],
    }
    temporary = report_path.with_name(f".{report_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, report_path)
    finally:
        temporary.unlink(missing_ok=True)
    return report_path


def validate_cleanup_preview(
    args: argparse.Namespace,
    chapter_ids: list[str],
    *,
    report_path: Path | None = None,
    now: datetime | None = None,
    planned: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Require a recent preview with the exact scope and note content."""
    path = Path(report_path or cleanup_preview_path(args)).expanduser()
    if not path.exists():
        raise RuntimeError(
            "Cleanup preview is required before a production cleanup. "
            "Run --clean-preambles --dry-run with the same filters first."
        )
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cleanup preview cannot be read safely: {path}"
        ) from exc
    if report.get("mode") != "preview":
        raise RuntimeError("Cleanup preview is invalid: expected mode=preview.")

    generated_at_raw = report.get("generated_at")
    try:
        generated_at = datetime.fromisoformat(str(generated_at_raw))
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "Cleanup preview is invalid: generated_at is missing or malformed."
        ) from exc
    current_time = now or datetime.now(timezone.utc)
    age_seconds = (current_time - generated_at).total_seconds()
    if age_seconds < 0 or age_seconds > CLEANUP_PREVIEW_MAX_AGE_SECONDS:
        raise RuntimeError(
            "Cleanup preview is stale or from the future. "
            "Regenerate it with --clean-preambles --dry-run."
        )

    expected_scope = cleanup_preview_scope(args, chapter_ids)
    expected_fingerprint = cleanup_preview_fingerprint(expected_scope)
    report_scope = report.get("scope")
    report_ids = report.get("chapter_ids")
    changes = report.get("changes")
    change_ids = [
        str(change.get("chapter_id"))
        for change in changes
        if isinstance(change, dict) and change.get("chapter_id") is not None
    ] if isinstance(changes, list) else None
    if (
        report_scope != expected_scope
        or report_ids != expected_scope["chapter_ids"]
        or report.get("scope_fingerprint") != expected_fingerprint
        or report.get("changed") != len(expected_scope["chapter_ids"])
        or not isinstance(changes, list)
        or sorted(change_ids) != expected_scope["chapter_ids"]
    ):
        raise RuntimeError(
            "Cleanup preview does not match the current filters or chapter set. "
            "Regenerate it with --clean-preambles --dry-run."
        )
    if planned is not None:
        validate_cleanup_preview_content(report, planned)
    return report


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


async def _run_main() -> int:
    global ACTIVE_RUN_ID, ACTIVE_RUN_COUNTS
    args = parse_args()
    ACTIVE_RUN_ID = None
    ACTIVE_RUN_COUNTS = {"completed": 0, "failed": 0}
    try:
        repair_index_ids = _repair_index_ids(args)
    except ValueError as exc:
        log.error("%s", exc)
        return 2
    if repair_index_ids and args.skip_index:
        log.error("--repair-index cannot be combined with --skip-index")
        return 2
    if getattr(args, "archive_history", False):
        try:
            summary = archive_history(
                getattr(args, "archive_before_days", ARCHIVE_RETENTION_DAYS),
                dry_run=args.dry_run,
            )
        except ValueError as exc:
            log.error("%s", exc)
            return 2
        action = "would archive" if args.dry_run else "archived"
        log.info(
            "Audit history %s: runs=%d records=%s protected=%s%s",
            action,
            summary["archivable_runs"],
            summary["records"],
            summary["protected_runs"],
            f" archive_dir={summary['archive_dir']}"
            if summary.get("archive_dir")
            else "",
        )
        return 0
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
    client = CloudflareClient()
    chapters = fetch_chapters(client)
    if args.class_level:
        expected = "HS 1st Year" if args.class_level == "11" else "HS 2nd Year"
        chapters = [row for row in chapters if row["class_name"] == expected]
    if args.subject:
        chapters = [row for row in chapters if row["subject_slug"] == args.subject]

    if repair_index_ids:
        if args.dry_run:
            log.info(
                "Read-only index repair preview: would rebuild %d chapter index(es): %s",
                len(repair_index_ids),
                ", ".join(repair_index_ids),
            )
            return 0
        ACTIVE_RUN_ID, started_at = record_production_approval(args)
        log.info(
            "Index repair approved by %s (run_id=%s, started_at=%s)",
            args.operator or getpass.getuser(),
            ACTIVE_RUN_ID,
            started_at,
        )
        return await repair_indexes(client, chapters, repair_index_ids)

    if args.clean_preambles:
        planned = await asyncio.to_thread(build_preamble_cleanup_plan, chapters)
        chapter_ids = [str(chapter["id"]) for chapter in planned]
        preview_scope = cleanup_preview_scope(args, chapter_ids)
        if args.dry_run:
            log_cleanup_preview(planned)
            report_path = write_cleanup_preview_report(
                planned,
                preview_scope,
                cleanup_preview_path(args),
            )
            log.info("Cleanup preview report: %s", report_path)
            return 0
        preview = validate_cleanup_preview(
            args,
            chapter_ids,
            report_path=cleanup_preview_path(args),
            planned=planned,
        )
        ACTIVE_RUN_ID, started_at = record_production_approval(
            args,
            {
                "cleanup_preview_fingerprint": preview["scope_fingerprint"],
                "cleanup_preview_generated_at": preview["generated_at"],
            },
        )
        log.info(
            "Production cleanup approved by %s (run_id=%s, started_at=%s, "
            "preview_fingerprint=%s)",
            args.operator or getpass.getuser(),
            ACTIVE_RUN_ID,
            started_at,
            preview["scope_fingerprint"],
        )
        affected = await asyncio.to_thread(
            apply_preamble_cleanup,
            client,
            planned,
            preview=preview,
        )
        cleanup_failed = 0
        cleanup_completed = 0
        if args.skip_index:
            for chapter in affected:
                record_progress(
                    str(chapter["id"]),
                    "done",
                    operation="cleanup",
                    note_chars=len(str(chapter.get("notes_en") or "")),
                    chunks=0,
                )
            cleanup_completed = len(affected)
        else:
            for chapter in affected:
                chapter_id = str(chapter["id"])
                try:
                    chunk_count = await asyncio.to_thread(
                        replace_index,
                        client,
                        chapter,
                        str(chapter.get("notes_en") or ""),
                        "existing-d1-preamble-cleanup",
                    )
                    record_progress(
                        chapter_id,
                        "done",
                        operation="cleanup",
                        note_chars=len(str(chapter.get("notes_en") or "")),
                        chunks=chunk_count,
                    )
                    cleanup_completed += 1
                except Exception as exc:
                    cleanup_failed += 1
                    _record_index_failure(
                        chapter_id,
                        exc,
                        attempt=_next_index_attempt(chapter_id),
                        operation=_index_failure_operation(exc, "cleanup"),
                        source_pdf_url="existing-d1-preamble-cleanup",
                    )
                    log.exception(
                        "Indexing failed after cleanup notes write for %s: %s",
                        chapter_id,
                        exc,
                    )
        ACTIVE_RUN_COUNTS["completed"] = cleanup_completed
        ACTIVE_RUN_COUNTS["failed"] = cleanup_failed
        log.info(
            "Preamble cleanup complete: changed=%d indexed=%d failed=%d",
            len(affected),
            cleanup_completed,
            cleanup_failed,
        )
        record_terminal_summary(
            "failed" if cleanup_failed else "completed",
            completed=cleanup_completed,
            failed=cleanup_failed,
        )
        _clear_active_run(ACTIVE_RUN_ID)
        return 1 if cleanup_failed else 0

    if not args.dry_run:
        ACTIVE_RUN_ID, started_at = record_production_approval(args)
        log.info(
            "Production write approved by %s (run_id=%s, started_at=%s)",
            args.operator or getpass.getuser(),
            ACTIVE_RUN_ID,
            started_at,
        )

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
                index_attempt = _next_index_attempt(chapter_id)
                try:
                    chunk_count = await asyncio.to_thread(
                        replace_index,
                        client,
                        chapter,
                        notes,
                        str(source["source_pdf_url"]),
                    )
                except Exception as exc:
                    failed += 1
                    ACTIVE_RUN_COUNTS["failed"] = failed
                    _record_index_failure(
                        chapter_id,
                        exc,
                        attempt=index_attempt,
                        operation=_index_failure_operation(exc, "import"),
                        source_pdf_url=str(source["source_pdf_url"]),
                    )
                    log.exception("Indexing failed after notes write for %s: %s", chapter_id, exc)
                    await asyncio.sleep(max(0.0, args.delay))
                    continue
            record_progress(
                chapter_id,
                "done",
                source_pdf_url=source["source_pdf_url"],
                note_chars=len(notes),
                rag_sections=len(sections),
                chunks=chunk_count,
            )
            processed += 1
            ACTIVE_RUN_COUNTS["completed"] = processed
            log.info("Updated %s (%d chars, %d chunks)", chapter_id, len(notes), chunk_count)
        except Exception as exc:
            failed += 1
            ACTIVE_RUN_COUNTS["failed"] = failed
            record_progress(chapter_id, "error", error=str(exc))
            log.exception("Failed chapter %s: %s", chapter_id, exc)
        await asyncio.sleep(max(0.0, args.delay))

    log.info("Run complete: updated=%d failed=%d", processed, failed)
    record_terminal_summary(
        "failed" if failed else "completed",
        completed=processed,
        failed=failed,
    )
    _clear_active_run(ACTIVE_RUN_ID)
    return 1 if failed else 0


async def main() -> int:
    """Run an import and close an approved run even on an unexpected failure."""
    try:
        return await _run_main()
    except Exception:
        if ACTIVE_RUN_ID:
            record_terminal_summary(
                "failed",
                completed=ACTIVE_RUN_COUNTS["completed"],
                failed=ACTIVE_RUN_COUNTS["failed"],
            )
            _clear_active_run(ACTIVE_RUN_ID)
        raise


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
