from scripts.syllabus_catalog_ingest import (
    _degree_course,
    _extract_semesters,
    _ocr_languages,
    _slug,
)


def test_extracts_numeric_and_roman_semesters():
    text = "Semester I Course Structure\nSemester-II\n3rd Semester\nSEM 8"
    assert _extract_semesters(text, "Other Programme") == [1, 2, 3, 8]


def test_uses_programme_duration_only_when_pdf_has_no_semesters():
    assert _extract_semesters("Course structure", "FYUGP (4-Year UG)") == list(
        range(1, 9)
    )
    assert _extract_semesters("Course structure", "FYIMP") == list(range(1, 11))
    assert _extract_semesters("Course structure", "PG (2-Year Postgraduate)") == [
        1,
        2,
        3,
        4,
    ]


def test_programme_duration_rejects_incidental_out_of_range_mentions():
    text = "Semester I through Semester VIII. Reference: Semester X regulations."
    assert _extract_semesters(text, "FYUGP (4-Year UG)") == list(range(1, 9))


def test_maps_programme_and_faculty_to_existing_course_hierarchy():
    assert _degree_course("FYUGP (4-Year UG)", "Arts", "English") == "B.A."
    assert _degree_course("FYUGP (4-Year UG)", "Science", "Physics") == "B.Sc"
    assert _degree_course("FYUGP (4-Year UG)", "Commerce & Management", "Commerce") == "B.Com"
    assert _degree_course("PG", "Science", "Physics") == "M.Sc."


def test_slug_is_stable_for_subject_names():
    assert _slug("English Language Teaching (ELT)") == "english-language-teaching-elt"


def test_ocr_language_matches_official_subject_language():
    from scripts.syllabus_catalog_ingest import CatalogItem

    base = {
        "institution": "AHSEC",
        "source_page_url": "https://ahsec.assam.gov.in/",
        "source_url": "https://ahsec.assam.gov.in/file.pdf",
        "source_title": "Syllabus",
        "programme": "HS 1st Year",
        "faculty": "",
        "session": "2026-27",
    }
    assert _ocr_languages(CatalogItem(subject_name="MIL (Assamese)", **base)) == "asm+eng"
    assert _ocr_languages(CatalogItem(subject_name="MIL (Bengali)", **base)) == "ben+eng"