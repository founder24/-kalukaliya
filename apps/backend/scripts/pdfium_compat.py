"""Small PDFium helpers shared by the ingestion scripts.

PDFium is used instead of PyMuPDF so the ingestion toolchain stays on
permissive licenses while retaining text extraction and page rendering for
OCR fallbacks.
"""

from __future__ import annotations

from typing import Any

import pypdfium2 as pdfium


def open_pdf(data: bytes) -> Any:
    """Open PDF bytes as a PDFium document."""
    return pdfium.PdfDocument(data)


def page_text(page: Any) -> str:
    """Extract all text from one PDFium page."""
    text_page = page.get_textpage()
    try:
        return text_page.get_text_range()
    finally:
        text_page.close()


def page_image(page: Any, scale: float) -> Any:
    """Render a page as a Pillow image at the requested scale."""
    bitmap = page.render(scale=scale, rev_byteorder=True)
    # Detach from PDFium's native bitmap before the bitmap is released.
    return bitmap.to_pil().copy()


def close_pdf(document: Any) -> None:
    """Release PDFium resources explicitly after an ingestion pass."""
    document.close()