"""Task #494 — wiring invariants for the content_formatter dispatcher.

Pins two complementary invariants that the strict architect review
called out as missing:

  (a) Every required STORE-TIME content-generation path routes its
      polish step through `content_formatter.format_content` (directly
      or via `llm.polish_notes_with_format` / `llm.polish_notes_with_vertex`,
      both of which delegate to the dispatcher). The audit `formatted_by`
      field that lands in Mongo is meaningless if a caller silently
      bypassed the dispatcher.

  (b) STREAMING / chat hot-path code MUST NOT call
      `content_formatter.format_content`. The dispatcher carries
      Llama-70b fallback latency + formatter telemetry that would
      regress TTFT on the chat path. The Assamese chat translate-polish
      is the documented exception that calls `vertex_format` directly
      (chat-path bypass) and is allowlisted in
      `scripts/check_dead_providers.py::VERTEX_FORMAT_DIRECT_CALLERS`.

These invariants are enforced by static text scans of the production
modules so a future caller cannot quietly violate the contract.
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="ignore")


# ── (a) Required store-time polish callers route via the dispatcher ──────────
REQUIRED_STORE_TIME_CALLERS = {
    "routes/admin_pipeline.py": (
        # Notes-publish + reflow + Assamese bulk-translate use the
        # dict-returning wrapper so they can persist `formatted_by`.
        "polish_notes_with_format",
    ),
    "routes/admin_advanced.py": (
        "polish_notes_with_format",
    ),
}


def test_store_time_callers_use_dispatcher_wrapper():
    failures: list[str] = []
    for rel, expected_symbols in REQUIRED_STORE_TIME_CALLERS.items():
        body = _read(rel)
        for sym in expected_symbols:
            if sym not in body:
                failures.append(
                    f"{rel}: store-time polish must use {sym!r} "
                    f"(Task #494 V4 §15 §6) — symbol not found"
                )
        # Belt-and-braces: the same module must NOT directly import
        # `format_with_vertex` because the dispatcher is the only
        # sanctioned audit-emitting surface for store-time polish.
        if re.search(r"from\s+vertex_format\s+import\s+[^#\n]*\bformat_with_vertex\b", body):
            failures.append(
                f"{rel}: store-time module must NOT import "
                f"vertex_format.format_with_vertex directly — route "
                f"through content_formatter.format_content"
            )
    assert not failures, "wiring invariant (a) violated:\n  " + "\n  ".join(failures)


# ── (b) Chat / streaming hot-path must NOT call the dispatcher ───────────────
# Modules that serve the chat / streaming hot-path. Polishing here would
# add Llama-70b fallback + formatter telemetry to a TTFT-critical request.
CHAT_HOT_PATH_MODULES = (
    "routes/ai_chat.py",
    "pipeline.py",
)


def test_chat_hot_path_does_not_invoke_dispatcher():
    failures: list[str] = []
    forbidden = re.compile(
        r"from\s+content_formatter\s+import\s+[^#\n]*\bformat_content\b"
        r"|content_formatter\.format_content\("
    )
    for rel in CHAT_HOT_PATH_MODULES:
        body = _read(rel)
        for ln, line in enumerate(body.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue  # comments / docstrings are fine
            if forbidden.search(line):
                failures.append(f"{rel}:{ln}: chat hot-path must NOT call format_content → {line.strip()[:120]}")
    assert not failures, "wiring invariant (b) violated:\n  " + "\n  ".join(failures)


# ── Audit-field persistence — formatted_by lands in the Mongo write ───────────
def test_admin_pipeline_persists_formatted_by_to_mongo():
    """`routes/admin_pipeline.py` must write the audit `formatted_by`
    (or per-field `*_formatted_by` for Assamese bulk translate) into
    Mongo so the admin-health panel and downstream observability can
    attribute every polished doc to its actual formatter."""
    body = _read("routes/admin_pipeline.py")
    assert "formatted_by" in body, (
        "routes/admin_pipeline.py must persist a `formatted_by` audit "
        "field on every polished chapter / translation Mongo write "
        "(Task #494 V4 §15 §6)."
    )


def test_admin_advanced_persists_formatted_by_in_atomic_chapter_write():
    body = _read("routes/admin_advanced.py")
    assert "formatted_by" in body and "chapter_atomic_update" in body, (
        "routes/admin_advanced.py atomic chapter writer must persist "
        "the `formatted_by` audit field alongside the polished content."
    )
