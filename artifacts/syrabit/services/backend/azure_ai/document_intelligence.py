"""Azure AI Document Intelligence wrapper — layout-aware OCR.

Used as the layout-aware branch of the past-paper / marks-sheet OCR
chain alongside Textract (AWS) and Vision (GCP). The chain in
``artifacts/syrabit-backend/ocr/router.py`` picks the branch based on
the upload's classification:

* Past papers + marks sheets (multi-column, tables, handwritten
  Bengali/Assamese marginalia) → Document Intelligence prebuilt
  ``layout`` model.
* General photos + screenshots → AI Vision (see ``vision.py``).
* Bulk PDFs → Textract on the AWS side.

S3 is the only object store. Pages are uploaded to S3 by the API;
the OCR job mints a presigned GET URL and passes it to Document
Intelligence's ``urlSource`` so the service never reads from Azure
Blob.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from . import _resolver

API_VERSION = "2024-11-30"


@dataclass
class OCRPage:
    page_number: int
    text: str
    handwritten_confidence: float = 0.0


@dataclass
class OCRTable:
    page_number: int
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class LayoutResult:
    pages: list[OCRPage]
    tables: list[OCRTable]
    raw_content: str
    latency_ms: int


def _token() -> str:
    return _resolver.get_credential().get_token(
        "https://cognitiveservices.azure.com/.default"
    ).token


def analyze_layout(presigned_url: str, *, model: str = "prebuilt-layout") -> LayoutResult:
    """Run a presigned S3 URL through Document Intelligence.

    Polls the operation-location until ``succeeded``; the prebuilt
    layout model usually settles within 5–8 s for a single page and
    20–30 s for a 10-page past paper. The cron job calls this from a
    Container Apps Job, NOT the synchronous request path, so the
    poll timeout is generous.
    """
    import requests

    endpoint = _resolver.endpoint_for("document_intel").rstrip("/")
    submit = requests.post(
        f"{endpoint}/documentintelligence/documentModels/{model}:analyze"
        f"?api-version={API_VERSION}",
        json={"urlSource": presigned_url},
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    if submit.status_code == 429:
        raise RuntimeError("azure-doc-intel: submit throttled (429)")
    submit.raise_for_status()
    op_url = submit.headers["operation-location"]

    started = time.monotonic()
    deadline = started + 120
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError(f"azure-doc-intel: poll timed out after 120s ({op_url})")
        poll = requests.get(
            op_url,
            headers={"Authorization": f"Bearer {_token()}"},
            timeout=15,
        )
        poll.raise_for_status()
        body = poll.json()
        status = body.get("status")
        if status == "succeeded":
            break
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"azure-doc-intel: analysis {status}: {body.get('error')}")
        time.sleep(1.5)

    latency_ms = int((time.monotonic() - started) * 1000)
    analyze = body.get("analyzeResult", {})

    pages: list[OCRPage] = []
    for page in analyze.get("pages", []):
        words = page.get("words", []) or []
        handwritten = [
            w for w in words if (w.get("style") or {}).get("isHandwritten")
        ]
        confidence = (
            sum(w.get("confidence", 0.0) for w in handwritten) / len(handwritten)
            if handwritten
            else 0.0
        )
        text = " ".join(w.get("content", "") for w in words)
        pages.append(OCRPage(page_number=page.get("pageNumber", 0), text=text, handwritten_confidence=confidence))

    tables: list[OCRTable] = []
    for tbl in analyze.get("tables", []):
        cells = tbl.get("cells", [])
        if not cells:
            continue
        max_row = max(c.get("rowIndex", 0) for c in cells)
        max_col = max(c.get("columnIndex", 0) for c in cells)
        grid = [["" for _ in range(max_col + 1)] for _ in range(max_row + 1)]
        for c in cells:
            grid[c.get("rowIndex", 0)][c.get("columnIndex", 0)] = c.get("content", "")
        # Tables span pages; pick the first page in the bounding regions.
        page_no = (tbl.get("boundingRegions") or [{}])[0].get("pageNumber", 0)
        tables.append(OCRTable(page_number=page_no, rows=grid))

    return LayoutResult(
        pages=pages,
        tables=tables,
        raw_content=analyze.get("content", ""),
        latency_ms=latency_ms,
    )
