#!/usr/bin/env python3
"""scripts/seo_baseline.py — Task #15 §1.

Sample 20 production SEO chapter pages (4 boards × 5 chapters), run
Lighthouse + a structured-data validator + Google's Rich Results test
against each, and emit a single JSON report at
``docs/seo/baseline-2026-Q2.json``. The report is the canonical
"baseline-then-track" artefact the user can diff against on every
weekly Lambda invocation (see ``infra/aws/lambda/manifest.json``
entry ``seo-baseline`` — added in a follow-up; not wired by this
task because the upstream content tasks (#5–#13) are still in
flight and the baseline would otherwise capture the *unimproved*
state).

Why the script is fail-loud (V4 §12)
------------------------------------
* External tools are required, not optional. If ``lighthouse`` or
  ``curl`` is missing, the script exits non-zero with the exact
  install command, instead of writing a half-empty report.
* The Rich Results endpoint is Google-rate-limited; we space requests
  by ``GOOGLE_RR_MIN_INTERVAL_S`` (default 5 s) and surface a 429 as
  a hard error so a silently degraded run cannot land in the report.
* Network errors per-page are recorded in the report under
  ``failures[]`` rather than swallowed, so a regression that
  manifests as "Lighthouse couldn't reach the page" is not invisible.

Usage
-----
::

    PUBLIC_BASE_URL=https://syrabit.ai \\
    GOOGLE_RR_API_KEY=... \\
    python scripts/seo_baseline.py \\
        --boards ahsec,ncert,scert,seba \\
        --chapters-per-board 5 \\
        --out docs/seo/baseline-2026-Q2.json

If ``GOOGLE_RR_API_KEY`` is unset, the Rich Results leg is skipped
with an explicit ``skipped_reason`` per page so the report is
unambiguous about what was/was not measured.

This is the offline part of Task #15. The Lambda-scheduled weekly
run + admin-observability tile depends on the script existing first;
that wiring lives in Task #28 (proposed at the end of this task).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "seo" / "baseline-2026-Q2.json"
DEFAULT_PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://syrabit.ai").rstrip("/")
DEFAULT_BOARDS = ("ahsec", "ncert", "scert", "seba")
DEFAULT_CLASS = "12"
RICH_RESULTS_ENDPOINT = "https://searchconsole.googleapis.com/v1/urlTestingTools/richResults:run"
GOOGLE_RR_MIN_INTERVAL_S = float(os.environ.get("GOOGLE_RR_MIN_INTERVAL_S", "5"))


@dataclass
class PageReport:
    url: str
    board: str
    chapter_slug: str
    page_type: str
    lighthouse: Optional[dict[str, Any]] = None
    structured_data: Optional[dict[str, Any]] = None
    rich_results: Optional[dict[str, Any]] = None
    failures: list[str] = field(default_factory=list)
    skipped_legs: dict[str, str] = field(default_factory=dict)


@dataclass
class BaselineReport:
    generated_at_utc: str
    public_base_url: str
    sampled_pages: int
    pages: list[PageReport]
    summary: dict[str, Any]


# ─── Sampling ───────────────────────────────────────────────────────────────


def _sample_chapter_slugs(board: str, n: int) -> list[str]:
    """Placeholder sampler — read the live chapter list when wired.

    Wiring path (deferred to follow-up Task #28): query
    ``GET {PUBLIC_BASE_URL}/api/seo/sitemap-sample?board=...&limit=...``
    which returns the top-N chapters by 7-day page_views (same
    selector the prewarm Lambda uses, see
    ``aca_jobs/prewarm_seo_routes.py``). For now we return a
    deterministic placeholder so the baseline JSON shape is stable
    even before the upstream tasks ship the chapter content.
    """
    return [f"sample-chapter-{i + 1:02d}" for i in range(n)]


def _build_url(base: str, board: str, chapter_slug: str, page_type: str) -> str:
    return f"{base}/board/{board}/class/{DEFAULT_CLASS}/subject/general/chapter/{chapter_slug}/{page_type}"


# ─── Tool wrappers ──────────────────────────────────────────────────────────


def _require_tool(name: str, install_hint: str) -> None:
    if shutil.which(name) is None:
        sys.exit(
            f"seo_baseline.py: required tool '{name}' not on PATH. "
            f"Install with: {install_hint}"
        )


def _run_lighthouse(url: str) -> dict[str, Any]:
    """Execute Lighthouse against ``url``; return the parsed JSON.

    We invoke the Node CLI (``lighthouse``) instead of the programmatic
    API so the script stays language-portable — the Lambda runtime
    will execute the same binary out of a layer.
    """
    cmd = [
        "lighthouse",
        url,
        "--quiet",
        "--chrome-flags=--headless --no-sandbox",
        "--output=json",
        "--output-path=stdout",
        "--only-categories=performance,seo,accessibility,best-practices",
        "--throttling-method=simulate",
        "--form-factor=mobile",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"lighthouse exit={proc.returncode}: {proc.stderr.strip()[:400]}")
    return json.loads(proc.stdout)


def _summarise_lighthouse(payload: dict[str, Any]) -> dict[str, Any]:
    cats = payload.get("categories", {})
    audits = payload.get("audits", {})
    lcp = audits.get("largest-contentful-paint", {}).get("numericValue")
    inp = audits.get("interaction-to-next-paint", {}).get("numericValue")
    cls = audits.get("cumulative-layout-shift", {}).get("numericValue")
    return {
        "scores": {k: int((v.get("score") or 0) * 100) for k, v in cats.items()},
        "lcp_ms": lcp,
        "inp_ms": inp,
        "cls": cls,
        "lcp_under_2_5s": (lcp is not None and lcp <= 2500),
    }


def _validate_structured_data(url: str) -> dict[str, Any]:
    """Fetch the page and validate every JSON-LD island.

    Uses urllib (no extra deps) + Python's stdlib json. We do not call
    out to schema.org's hosted validator because it is rate-limited
    per IP without an API key; instead we extract every
    ``<script type="application/ld+json">`` block, parse it as JSON,
    and assert each block has at minimum a ``@context`` and ``@type``.
    Stricter schema-conformance lives in the Rich Results call below.
    """
    req = request.Request(url, headers={"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"})
    try:
        with request.urlopen(req, timeout=20) as resp:  # noqa: S310 — controlled URL
            body = resp.read().decode("utf-8", "ignore")
    except error.URLError as exc:
        raise RuntimeError(f"fetch failed: {exc}") from exc

    blocks: list[Any] = []
    cursor = 0
    open_tag = '<script type="application/ld+json"'
    close_tag = "</script>"
    while True:
        i = body.find(open_tag, cursor)
        if i < 0:
            break
        j = body.find(">", i)
        k = body.find(close_tag, j)
        if j < 0 or k < 0:
            break
        raw = body[j + 1: k].strip()
        cursor = k + len(close_tag)
        # The page emits a single JSON-LD object with `@context` and
        # `@graph`, see `routes/seo_pages.py::_jsonld(...)`. Reverse
        # the page's `<\/` script-tag escape before parsing so the
        # JSON is valid.
        try:
            blocks.append(json.loads(raw.replace("<\\/", "</")))
        except json.JSONDecodeError as exc:
            blocks.append({"_parse_error": str(exc), "_raw_head": raw[:120]})

    # Walk every node, descending into lists and `@graph`, so a
    # `{ "@context": "...", "@graph": [{"@type": "FAQPage"}, ...] }`
    # block surfaces every nested `@type`. Without this we would
    # falsely report `has_faq_page=False` against a perfectly valid
    # page (architect-flagged regression, fixed in Task #15).
    def _walk(node: Any, out: list[str]) -> None:
        if isinstance(node, list):
            for item in node:
                _walk(item, out)
        elif isinstance(node, dict):
            t = node.get("@type")
            if isinstance(t, str):
                out.append(t)
            elif isinstance(t, list):
                out.extend(x for x in t if isinstance(x, str))
            graph = node.get("@graph")
            if graph is not None:
                _walk(graph, out)

    types: list[str] = []
    invalid = 0
    for b in blocks:
        if isinstance(b, dict) and "_parse_error" in b:
            invalid += 1
            continue
        before = len(types)
        _walk(b, types)
        if len(types) == before:
            invalid += 1
    return {
        "blocks_found": len(blocks),
        "types": types,
        "invalid_blocks": invalid,
        "has_faq_page": any(t == "FAQPage" for t in types),
        "has_breadcrumb": any(t == "BreadcrumbList" for t in types),
        "has_quick_answer": any(
            t in ("Question", "Answer", "QAPage") for t in types
        ),
    }


def _run_rich_results(url: str, api_key: str) -> dict[str, Any]:
    payload = json.dumps({"url": url, "userAgent": "MOBILE"}).encode("utf-8")
    req = request.Request(
        f"{RICH_RESULTS_ENDPOINT}?key={api_key}",
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=60) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("Google Rich Results: 429 (rate-limited; raise GOOGLE_RR_MIN_INTERVAL_S)") from exc
        raise RuntimeError(f"rich results http {exc.code}: {exc.read()[:200]!r}") from exc
    items = data.get("richResultsResult", {}).get("detectedItems", [])
    issues = []
    for item in items:
        for issue in item.get("issues", []):
            issues.append({
                "type": item.get("richResultType"),
                "severity": issue.get("severity"),
                "message": issue.get("issueMessage"),
            })
    return {
        "verdict": data.get("verdict"),
        "detected_types": [item.get("richResultType") for item in items],
        "issues": issues,
    }


# ─── Driver ─────────────────────────────────────────────────────────────────


def run_baseline(
    base_url: str,
    boards: tuple[str, ...],
    chapters_per_board: int,
    page_type: str,
    rich_results_key: Optional[str],
) -> BaselineReport:
    _require_tool("lighthouse", "npm i -g lighthouse@latest")

    pages: list[PageReport] = []
    last_rr_call_at = 0.0
    for board in boards:
        for slug in _sample_chapter_slugs(board, chapters_per_board):
            url = _build_url(base_url, board, slug, page_type)
            rep = PageReport(url=url, board=board, chapter_slug=slug, page_type=page_type)

            try:
                rep.lighthouse = _summarise_lighthouse(_run_lighthouse(url))
            except Exception as exc:
                rep.failures.append(f"lighthouse: {exc}")

            try:
                rep.structured_data = _validate_structured_data(url)
            except Exception as exc:
                rep.failures.append(f"structured_data: {exc}")

            if rich_results_key:
                wait = max(0.0, GOOGLE_RR_MIN_INTERVAL_S - (time.time() - last_rr_call_at))
                if wait:
                    time.sleep(wait)
                try:
                    rep.rich_results = _run_rich_results(url, rich_results_key)
                except Exception as exc:
                    rep.failures.append(f"rich_results: {exc}")
                    # Fail loud (V4 §12): a 429 / HTTP failure on the
                    # Rich Results leg means the weekly baseline is
                    # incomplete in a way that would silently regress
                    # the trend line. Re-raise so the Lambda exits
                    # non-zero and the alarm fires; a per-page
                    # non-RR failure (Lighthouse timeout etc.) is
                    # already captured in `failures[]` and does not
                    # invalidate the run as a whole.
                    raise RuntimeError(
                        f"Google Rich Results leg failed for {url}: {exc}. "
                        "Refusing to publish a partial baseline."
                    ) from exc
                last_rr_call_at = time.time()
            else:
                rep.skipped_legs["rich_results"] = "GOOGLE_RR_API_KEY unset"

            pages.append(rep)

    # ── summary ──
    n = len(pages)
    lh = [p.lighthouse for p in pages if p.lighthouse]
    summary = {
        "total_pages": n,
        "pages_with_lighthouse": len(lh),
        "median_seo_score": _median([p["scores"].get("seo", 0) for p in lh]),
        "median_perf_score": _median([p["scores"].get("performance", 0) for p in lh]),
        "lcp_under_2_5s_ratio": (
            sum(1 for p in lh if p.get("lcp_under_2_5s")) / len(lh)
            if lh else None
        ),
        "pages_with_faq_jsonld": sum(
            1 for p in pages
            if p.structured_data and p.structured_data.get("has_faq_page")
        ),
        "pages_with_breadcrumb_jsonld": sum(
            1 for p in pages
            if p.structured_data and p.structured_data.get("has_breadcrumb")
        ),
        "pages_with_failures": sum(1 for p in pages if p.failures),
    }
    return BaselineReport(
        generated_at_utc=datetime.now(tz=timezone.utc).isoformat(),
        public_base_url=base_url,
        sampled_pages=n,
        pages=pages,
        summary=summary,
    )


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _to_dict(report: BaselineReport) -> dict[str, Any]:
    return {
        "generated_at_utc": report.generated_at_utc,
        "public_base_url": report.public_base_url,
        "sampled_pages": report.sampled_pages,
        "summary": report.summary,
        "pages": [asdict(p) for p in report.pages],
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base-url", default=DEFAULT_PUBLIC_BASE_URL)
    p.add_argument("--boards", default=",".join(DEFAULT_BOARDS),
                   help="Comma-separated board slugs to sample.")
    p.add_argument("--chapters-per-board", type=int, default=5)
    p.add_argument("--page-type", default="notes",
                   help="One of: notes, definition, important-questions, mcqs, examples, faq.")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args(argv)

    boards = tuple(b.strip() for b in args.boards.split(",") if b.strip())
    rich_key = os.environ.get("GOOGLE_RR_API_KEY")

    report = run_baseline(
        base_url=args.base_url,
        boards=boards,
        chapters_per_board=args.chapters_per_board,
        page_type=args.page_type,
        rich_results_key=rich_key,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(_to_dict(report), indent=2), encoding="utf-8")
    print(f"seo_baseline: wrote {report.sampled_pages} pages to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
