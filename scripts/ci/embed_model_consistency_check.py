#!/usr/bin/env python3
"""
CI Gate — embed-model consistency check (Task #363, §4.4).

The live-RAG Pinecone index and the async-batch Pinecone index MUST
both populate vectors using the SAME embedding model — the
`embed_hotpath` pin from #359 (`@cf/baai/bge-m3`).

If they ever diverge, retrieval quality degrades silently and
unobservably (different vector spaces; cosine distance becomes
meaningless across them).

This CI check:
  1. Parses `infra/provider-priority-map.md` to extract the
     `embed_hotpath` primary `model_id` (the canonical source of
     truth).
  2. Asserts both `vector_db_live` and `vector_db_batch` sections
     exist and each declare a `pinecone` primary row (the live + batch
     index slugs).
  3. Greps `artifacts/syrabit-backend/` AND `artifacts/syrabit-backend/scripts/`
     for every Pinecone upsert call site and asserts the embed model
     used at that site matches the canonical pin. Supabase
     `.table(...).upsert(...)` calls are excluded (they're SQL, not
     vector writes).

Exit codes:
  0  — gate passed
  2  — gate failed (one or more upserts use a non-canonical or
       unresolved embed model, OR vector_db_live/batch sections are
       malformed in the priority map)
  3  — could not parse provider-priority-map.md

Usage:
  python3 scripts/ci/embed_model_consistency_check.py
  python3 scripts/ci/embed_model_consistency_check.py \
      --map infra/provider-priority-map.md \
      --backend artifacts/syrabit-backend
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Match Pinecone upsert call sites. Covers:
#   retriever.upsert(...)   r.upsert(...)   _pc.upsert(...)
#   index.upsert(...)       pinecone.upsert(...)   client.upsert(...)
#   "/vectors/upsert" HTTP path
# Excludes Supabase: `.table(...).upsert(...)` is filtered post-match.
UPSERT_RE = re.compile(
    r"\b(?:[a-zA-Z_][a-zA-Z0-9_]*)\.upsert\s*\(|/vectors/upsert"
)
SUPABASE_TABLE_RE = re.compile(r"\.table\s*\([^)]*\)\s*\.upsert\b")
EMBED_HINT_RE = re.compile(
    r"@cf/[A-Za-z0-9._/-]+|embed-multilingual-v\d+\.\d+|"
    r"voyage-\d+(?:-large)?|text-embedding-\d+"
)
# Operators annotate verified call sites with `# embed-model: <slug>`
# (or `# embed-model: legacy-migration-script-not-in-prod-chain`) to
# suppress findings on sites that have been audited.
ANNOTATION_RE = re.compile(
    r"#\s*embed-model:\s*([A-Za-z0-9@/._-]+)", re.IGNORECASE
)


def parse_canonical_embed_model(map_path: Path) -> str | None:
    if not map_path.exists():
        return None
    in_section = False
    for line in map_path.read_text().splitlines():
        s = line.strip()
        if s.startswith("##"):
            in_section = (s == "## embed_hotpath")
            continue
        if in_section and s.startswith("| primary"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) >= 3:
                return cells[2]
    return None


def assert_vector_db_sections(map_path: Path) -> tuple[bool, list[str]]:
    """Returns (ok, messages). Asserts vector_db_live and vector_db_batch
    each have a `pinecone` primary row."""
    msgs: list[str] = []
    text = map_path.read_text()
    ok_all = True
    for section in ("vector_db_live", "vector_db_batch"):
        m = re.search(
            rf"^## {re.escape(section)}\s*$(.*?)(?=^## |\Z)",
            text, re.MULTILINE | re.DOTALL,
        )
        if not m:
            msgs.append(f"  FAIL: section `## {section}` missing")
            ok_all = False
            continue
        body = m.group(1)
        primary_pinecone = False
        for line in body.splitlines():
            s = line.strip()
            if not s.startswith("| primary"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) >= 2 and cells[1] == "pinecone":
                primary_pinecone = True
                break
        if primary_pinecone:
            msgs.append(f"  OK:   `## {section}` has a `pinecone` primary row")
        else:
            msgs.append(
                f"  FAIL: `## {section}` has no `pinecone` primary row"
            )
            ok_all = False
    return ok_all, msgs


def scan_backend_for_upserts(
    root: Path, canonical: str
) -> list[tuple[Path, int, str, str]]:
    """Returns (file, line_no, line, found_model_or_'unknown').

    Excludes test files and Supabase `.table().upsert()` calls.
    """
    hits: list[tuple[Path, int, str, str]] = []
    py_files = [
        p for p in root.rglob("*.py")
        if p.name != "embed_model_consistency_check.py"
        and "/tests/" not in str(p) and not p.name.startswith("test_")
        and ".venv" not in p.parts and "node_modules" not in p.parts
    ]
    for f in py_files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            if not UPSERT_RE.search(line):
                continue
            if SUPABASE_TABLE_RE.search(line):
                continue
            window_start = max(0, i - 30)
            window_end = min(len(lines), i + 5)
            window = "\n".join(lines[window_start:window_end])
            ann = ANNOTATION_RE.search(window)
            if ann:
                annotated_slug = ann.group(1)
                if annotated_slug == canonical or annotated_slug.startswith(
                    "legacy-"
                ):
                    hits.append((f, i + 1, line.strip(),
                                 f"annotated:{annotated_slug}"))
                    continue
                hits.append((f, i + 1, line.strip(),
                             f"annotated-mismatch:{annotated_slug}"))
                continue
            models_in_window = set(EMBED_HINT_RE.findall(window))
            if canonical in models_in_window:
                hits.append((f, i + 1, line.strip(), canonical))
            elif models_in_window:
                hits.append(
                    (f, i + 1, line.strip(), ", ".join(sorted(models_in_window)))
                )
            else:
                hits.append((f, i + 1, line.strip(), "unknown"))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="infra/provider-priority-map.md")
    ap.add_argument("--backend", default="artifacts/syrabit-backend")
    args = ap.parse_args()

    map_path = Path(args.map)
    canonical = parse_canonical_embed_model(map_path)
    if not canonical:
        print(f"ERROR: could not parse `embed_hotpath` primary model_id "
              f"from {map_path}", file=sys.stderr)
        return 3

    print(f"Canonical embed_hotpath model: {canonical}\n")

    print("Section presence checks (vector_db_live, vector_db_batch):")
    sections_ok, sec_msgs = assert_vector_db_sections(map_path)
    for m in sec_msgs:
        print(m)

    root = Path(args.backend)
    upsert_failed = False
    if not root.exists():
        print(f"\nWARN: backend dir not found: {root}; skipping upsert scan.")
    else:
        hits = scan_backend_for_upserts(root, canonical)
        print(f"\nPinecone upsert sites scanned: {len(hits)}")
        bad: list[tuple[Path, int, str]] = []
        unresolved: list[tuple[Path, int, str]] = []
        for f, line_no, line, found in hits:
            if found == canonical:
                tag = "OK"
            elif found.startswith("annotated:"):
                tag = f"OK ({found})"
            elif found.startswith("annotated-mismatch:"):
                tag = f"FAIL ({found})"
                bad.append((f, line_no, found))
            elif found == "unknown":
                tag = "FAIL (unresolved embed model in window)"
                unresolved.append((f, line_no, line))
            else:
                tag = f"FAIL (uses {found})"
                bad.append((f, line_no, found))
            print(f"  {tag:55s}  {f}:{line_no}  {line[:100]}")

        if bad or unresolved:
            upsert_failed = True
            print()
            if bad:
                print(f"FAIL: {len(bad)} upsert site(s) use a non-canonical "
                      f"embed model. The live and batch indexes MUST share "
                      f"the embed model `{canonical}` (#363 §4.4).")
            if unresolved:
                print(f"FAIL: {len(unresolved)} upsert site(s) have no "
                      f"detectable embed model in the surrounding window. "
                      f"Either annotate the call site (add a comment "
                      f"naming the model) or refactor so the embed model "
                      f"is co-located with the upsert.")

    if not sections_ok or upsert_failed:
        return 2

    print(f"\nPASS: provider-priority-map sections OK; all upsert sites "
          f"consistent with `{canonical}`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
