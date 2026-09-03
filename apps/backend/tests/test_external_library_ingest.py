from scripts.external_library_ingest import (
    SOURCES,
    Candidate,
    _cap_text,
    _item_title,
    canonical_url,
    crawl_source,
    is_dspace_url,
    is_educational_url,
    is_pdf_response,
    extract_pdf,
    parse_hierarchy,
    stable_key,
    record_for,
)
from bs4 import BeautifulSoup


def test_canonical_url_and_stable_key_ignore_tracking_and_fragments():
    dirty = "https://devlibrary.in/hs/notes?utm_source=x&a=1#heading"
    clean = "https://devlibrary.in/hs/notes?a=1"
    assert canonical_url(dirty) == clean
    assert stable_key("dev_library", dirty, dirty) == stable_key("dev_library", clean, clean)


def test_web_boundary_requires_same_approved_host_and_education_signal():
    source = next(s for s in SOURCES if s.name == "dev_library")
    assert is_educational_url(source, "https://devlibrary.in/hs/class-12-physics-notes")
    assert not is_educational_url(source, "https://example.com/hs/class-12-notes")
    assert not is_educational_url(source, "https://devlibrary.in/privacy")


def test_dspace_boundary_rejects_other_origins_and_non_repository_routes():
    source = next(s for s in SOURCES if s.name == "goalpara")
    assert source.seed == "http://goalparacollege.bsmlib.com/handle/123456789/11"
    assert is_dspace_url(source, "http://goalparacollege.bsmlib.com/handle/123456789/99?offset=20")
    assert not is_dspace_url(source, "http://goalparacollege.bsmlib.com/handle/123456789/99?mode=full")
    assert is_dspace_url(source, "http://goalparacollege.bsmlib.com/bitstream/123456789/99/1/paper.pdf")
    assert not is_dspace_url(source, "https://goalparacollege.bsmlib.com/handle/123456789/99")
    assert not is_dspace_url(source, "http://goalparacollege.bsmlib.com/simple-search?query=physics")
    assert not is_dspace_url(source, "http://goalparacollege.bsmlib.com/browse?type=dateissued")


def test_conservative_hierarchy_parser():
    values = parse_hierarchy("AHSEC Class XII Physics Question Paper 2024")
    assert values == {"class_name": "HS 2nd Year", "board": "AHSEC", "subject": "Physics", "year": 2024}
    assert parse_hierarchy("A wonderful publication") == {}
    assert parse_hierarchy("FYUGP Semester IV Economics notes") == {
        "semester": 4, "course": "FYUGP", "subject": "Economics"
    }
    assert parse_hierarchy("H.S 2ND YEAR English Question Paper 2026") == {
        "class_name": "HS 2nd Year", "subject": "English", "year": 2026
    }
    assert parse_hierarchy("Physics (Major) 10th semester 2026") == {
        "semester": 10, "subject": "Physics", "year": 2026
    }


def test_text_cap_and_pdf_signature_validation():
    assert _cap_text("abcdef", 4) == ("abcd", True)
    assert _cap_text("abc", 4) == ("abc", False)
    assert is_pdf_response("text/html", b" \n%PDF-1.7 body")
    assert not is_pdf_response("application/pdf", b"<html>blocked</html>")


def test_image_only_pdf_can_defer_ocr():
    import fitz

    document = fitz.open()
    document.new_page()
    data = document.tobytes()
    document.close()
    text, pages, truncated, method = extract_pdf(
        data, "Scanned question paper", max_ocr_pages=0
    )
    assert text == ""
    assert pages == 1
    assert truncated is True
    assert method == "pymupdf+image-pages-deferred"


def test_exact_other_seed_urls_and_dspace_title_cleanup():
    assert next(s for s in SOURCES if s.name == "ngc").seed == "http://ngc.digitallibrary.co.in/handle/123456789/1"
    assert next(s for s in SOURCES if s.name == "bikali").seed == "http://bikalicollege.digitallibrary.co.in/handle/123456789/3"
    assert next(s for s in SOURCES if s.name == "dynamic_tutorials").seed == "https://www.dynamictutorialsandservices.org/p/class-1112.html"
    source = next(s for s in SOURCES if s.name == "ngc")
    assert _item_title(source, BeautifulSoup("<title>NGC Digital Library: Physics 2024</title>", "html.parser"), "x") == "Physics 2024"


def test_dev_library_is_reference_excerpt_only():
    class Client:
        def get(self, *_args, **_kwargs):
            raise AssertionError("reference-only records must not fetch page bodies")
    source = next(s for s in SOURCES if s.name == "dev_library")
    candidate = Candidate(source, source.seed, source.seed, "HS notes")
    record = record_for(candidate, Client(), False)
    assert source.reference_excerpt_chars == 500
    assert record["status"] == "metadata_only"
    assert "extracted_text" not in record
    assert record["metadata"]["content_policy"] == "reference-only"


def test_crawl_deduplicates_links_before_queueing(monkeypatch):
    from scripts.external_library_ingest import PoliteClient

    source = next(s for s in SOURCES if s.name == "dynamic_tutorials")
    seed_page = b"""
      <html><title>AHSEC Class 12</title><body>
        <a href="/class-12-physics-notes">Physics notes</a>
        <a href="/class-12-physics-notes">Physics notes duplicate</a>
      </body></html>
    """
    child_page = (
        b"<html><title>Class 12 Physics notes</title>"
        b"<article>Study material</article></html>"
    )
    calls = []

    def fake_get(self, url, **_kwargs):
        calls.append(url)
        response = type("Response", (), {})()
        response.content = seed_page if len(calls) == 1 else child_page
        return response

    monkeypatch.setattr(PoliteClient, "get", fake_get)
    candidates, error = crawl_source(source, PoliteClient(1), 10, 10)
    assert error is None
    assert len(calls) == 2
    assert len(candidates) == 1