from types import SimpleNamespace

from app.api.v1.public_content import _build_chapter_question_bank


def _chapter(**overrides):
    values = {
        "id": "chapter-1",
        "notes_en": "## Motion\n\nMotion is change in position.",
        "content_en": None,
        "notes_as": None,
        "content_as": None,
        "faq_jsonld": [
            {
                "question": "What is motion?",
                "answer": "Motion is the change in position of an object with time.",
                "marks": 2,
                "year": 2024,
                "source": "AHSEC 2024",
            },
            {
                "question": "State the meaning of displacement.",
                "answer": "Displacement is the shortest directed distance between two positions.",
                "marks": 3,
                "year": 2023,
                "source": "AHSEC 2023",
            },
        ],
        "qa_rag_sections_en": [
            {
                "section": "Short Answer",
                "question": "Explain motion using the chapter notes.",
                "answer": "Motion describes a change in the position of an object with respect to time.",
                "marks": 2,
            },
            {
                "section": "Textbook Exercise",
                "question": "Define displacement from the chapter.",
                "solution": "Displacement is the shortest directed distance between the initial and final positions.",
                "marks": 3,
            },
            {
                "section": "Short Answer",
                "question": "This incomplete record has no answer.",
            },
        ],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_question_bank_groups_solved_sources_by_marks_and_tracks_pyq_pattern():
    result = _build_chapter_question_bank(_chapter())

    assert result["counts"] == {"pyq": 2, "important": 1, "exercise": 1}
    assert set(result["mark_wise"]) == {"2", "3"}
    assert result["sources"] == {"notes": True, "chapter_qa": True, "pyq": True}
    assert result["pyq_years"] == ["2024", "2023"]

    important = next(
        item for item in result["items"] if item["kind"] == "important"
    )
    exercise = next(
        item for item in result["items"] if item["kind"] == "exercise"
    )
    assert important["solution"].startswith("Motion describes")
    assert exercise["solution"].startswith("Displacement is")
    assert important["pyq_frequency"] > 0
    assert exercise["pyq_frequency"] > 0


def test_question_bank_does_not_publish_unsolved_records_as_solutions():
    result = _build_chapter_question_bank(
        _chapter(
            faq_jsonld=[],
            qa_rag_sections_en=[
                {
                    "section": "Short Answer",
                    "question": "A question without a source answer.",
                }
            ],
        )
    )

    assert result["total"] == 0
    assert result["items"] == []