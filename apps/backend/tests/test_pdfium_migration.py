from __future__ import annotations

import asyncio
import sys
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from scripts import ahsec_ingest, external_library_ingest, syllabus_catalog_ingest
from scripts.syllabus_catalog_ingest import CatalogItem


def _pdf_bytes(text: str | None = None) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=220, height=220)
    if text:
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
        })
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 20 120 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _catalog_item() -> CatalogItem:
    return CatalogItem(
        institution="AHSEC Assam",
        source_page_url="https://ahsec.assam.gov.in/index.php/syllabus-2",
        source_url="https://ahsec.assam.gov.in/files/test-syllabus.pdf",
        source_title="Test syllabus",
        programme="HS 2nd Year",
        faculty="Science",
        subject_name="Physics",
        session="2026",
    )


def _fake_pdf_response(data: bytes) -> SimpleNamespace:
    return SimpleNamespace(content=data, raise_for_status=lambda: None)


def _assert_png_argument(args: list[str]) -> None:
    assert Path(args[1]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_external_library_text_extraction_uses_pdfium():
    text, page_count, truncated, method = external_library_ingest.extract_pdf(
        _pdf_bytes("Syrabit PDFium extraction test"),
        "Text question paper",
        max_ocr_pages=0,
    )

    assert "Syrabit PDFium extraction test" in text
    assert page_count == 1
    assert truncated is False
    assert method == "pypdfium2"


def test_external_library_ocr_renders_pdfium_page(monkeypatch):
    monkeypatch.setattr(external_library_ingest.shutil, "which", lambda _name: "/usr/bin/tesseract")

    def fake_run(args, **_kwargs):
        _assert_png_argument(args)
        return SimpleNamespace(returncode=0, stdout="Recognized scanned text", stderr="")

    monkeypatch.setattr(external_library_ingest.subprocess, "run", fake_run)
    text, page_count, truncated, method = external_library_ingest.extract_pdf(
        _pdf_bytes(),
        "Scanned question paper",
        max_ocr_pages=1,
    )

    assert text == "Recognized scanned text"
    assert page_count == 1
    assert truncated is False
    assert method == "pypdfium2+ocr"


def test_ahsec_text_extraction_uses_pdfium(monkeypatch):
    data = _pdf_bytes("AHSEC importer PDFium text")
    monkeypatch.setattr(ahsec_ingest, "_download_pdf", lambda _url: data)

    pages = asyncio.run(ahsec_ingest.extract_pdf_text(
        "https://ahsec.assam.gov.in/test.pdf",
        medium="en",
    ))

    assert pages == [{"page_num": 1, "text": "AHSEC importer PDFium text"}]


def test_ahsec_ocr_renders_pdfium_page(monkeypatch):
    data = _pdf_bytes()
    monkeypatch.setattr(ahsec_ingest, "_download_pdf", lambda _url: data)
    fake_tesseract = ModuleType("pytesseract")

    def fake_image_to_string(image, **_kwargs):
        assert image.mode == "RGB"
        assert image.size == (330, 330)
        return "Recognized AHSEC scanned page content"

    fake_tesseract.image_to_string = fake_image_to_string
    monkeypatch.setitem(sys.modules, "pytesseract", fake_tesseract)

    pages = asyncio.run(ahsec_ingest.extract_pdf_text(
        "https://ahsec.assam.gov.in/scanned.pdf",
        medium="en",
    ))

    assert pages == [{"page_num": 1, "text": "Recognized AHSEC scanned page content"}]


def test_syllabus_catalog_text_extraction_uses_pdfium(monkeypatch):
    data = _pdf_bytes("Official syllabus PDFium text")
    response = _fake_pdf_response(data)
    monkeypatch.setattr(
        syllabus_catalog_ingest,
        "_session",
        lambda: SimpleNamespace(get=lambda *_args, **_kwargs: response),
    )

    parsed = syllabus_catalog_ingest._parse_pdf(
        _catalog_item(),
        timeout=10,
        max_text_chars=10_000,
    )

    assert "Official syllabus PDFium text" in parsed.text
    assert parsed.page_count == 1


def test_syllabus_catalog_ocr_renders_pdfium_page(monkeypatch):
    data = _pdf_bytes()
    response = _fake_pdf_response(data)
    monkeypatch.setattr(
        syllabus_catalog_ingest,
        "_session",
        lambda: SimpleNamespace(get=lambda *_args, **_kwargs: response),
    )
    monkeypatch.setattr(syllabus_catalog_ingest.shutil, "which", lambda _name: "/usr/bin/tesseract")

    def fake_run(args, **_kwargs):
        _assert_png_argument(args)
        return SimpleNamespace(returncode=0, stdout="Recognized official scan", stderr="")

    monkeypatch.setattr(syllabus_catalog_ingest.subprocess, "run", fake_run)
    parsed = syllabus_catalog_ingest._parse_pdf(
        _catalog_item(),
        timeout=10,
        max_text_chars=10_000,
    )

    assert parsed.text == "Recognized official scan"
    assert parsed.page_count == 1