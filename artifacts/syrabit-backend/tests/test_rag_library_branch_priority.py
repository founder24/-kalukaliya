"""
Task #409 — pin the strengthened wording of the rag.py
``build_rag_system_prompt`` "library" branch.

When ChatPage forwards a non-empty ``card_context`` (because the
student arrived from a SubjectCard / ChapterPage / SubjectLandingPage
/ PersonalizedCmsPage Ask-AI button), the dispatcher routes through
the library source and the rendered system prompt MUST tell the LLM
to treat that card content as authoritative — not just as
background colour.

The previous wording ("Use this syllabus and chapter context to give
accurate, curriculum-aligned answers") was gentle enough that the
model still pivoted to generic curriculum coverage when the parent
page had a narrower focus, which made deep-link Ask-AI feel
ungrounded. These tests pin the new contract so a future copy edit
that softens the priority language fails loudly here instead of
silently regressing the on-page chat experience.
"""
import pytest

from rag import build_rag_system_prompt
from routes.ai_chat import _remap_card_context_source_to_library


def _build(card_context: str = "Subject: Political Science 2nd Sem NEP\n"
                              "Active chapter (priority context): Federalism") -> str:
    """Smallest possible call into the library branch.

    ``context``/``user_info`` mimic the shape ChatPage's payload
    populates after _resolve_subject_context resolves the subject and
    the user's board/class. The library branch only requires
    ``rag_context.source == 'library'`` and a non-empty
    ``document_text`` (which the ai_chat dispatcher copies from
    ``card_context``); everything else is incidental for this test.
    """
    return build_rag_system_prompt(
        context={"subject_name": "Political Science 2nd Sem NEP",
                 "board_name": "AHSEC", "class_name": "Class 12"},
        rag_context={"source": "library", "document_text": card_context},
        user_info={"id": "u-test"},
        query="Explain federalism in the Indian constitution",
        resolved_intent="notes",
    )


def test_library_prompt_renders_priority_header():
    """The new header must say 'PRIMARY CONTEXT — answer from this first'.

    The on-call grep in chat-debug logs keys off this phrase to
    confirm a turn was grounded in the parent page; renaming it
    silently would break that runbook.
    """
    out = _build()
    assert "PRIMARY CONTEXT" in out
    assert "answer from this first" in out
    assert "parent page / content card" in out


def test_library_prompt_marks_card_content_as_authoritative():
    """Must explicitly call the card content the AUTHORITATIVE source.

    Anything weaker (e.g. "use this for accurate answers") lets the
    model treat the card as flavour and pivot to generic curriculum
    knowledge — which is exactly the regression Task #409 fixed.
    """
    out = _build()
    assert "AUTHORITATIVE" in out
    assert "DIRECTLY from this context" in out
    assert "do not pivot to a different subject" in out


def test_library_prompt_honours_inner_priority_markers():
    """The two inner markers ChatPage / PersonalizedCmsPage emit must
    be named so the LLM knows they outrank the rest of the syllabus.

    ChatPage's `cardContext` builder writes
    ``Active chapter (priority context):`` when a chapter is active,
    and ``buildPlanSeedContext`` writes
    ``PERSONALIZED STUDY PLAN (priority context):`` for plan deep-links.
    Both must be referenced verbatim in the system prompt so the LLM
    actually follows the priority hint.
    """
    out = _build()
    assert "Active chapter (priority context):" in out
    assert "PERSONALIZED STUDY PLAN (priority context):" in out


def test_library_prompt_requires_explicit_fallback_disclosure():
    """When the card lacks the answer, the LLM must say so out loud.

    Silent fallback to general knowledge is what made the old
    behaviour feel ungrounded — students couldn't tell whether the
    answer came from the subject they were viewing or from the
    model's training data. The prompt now mandates disclosure.
    """
    out = _build()
    assert "fall back to general curriculum knowledge" in out
    assert "say so explicitly" in out


def test_library_prompt_embeds_the_card_text_under_the_priority_header():
    """The rendered card content must appear under
    'Page content (priority context):' so the LLM associates the
    body with the priority instruction immediately above it.
    """
    out = _build("Subject: Political Science 2nd Sem NEP\n"
                 "Active chapter (priority context): Federalism\n"
                 "Federalism is the division of powers...")
    header_idx = out.find("**Page content (priority context):**")
    body_idx   = out.find("Federalism is the division of powers...")
    assert header_idx != -1, "priority body header missing"
    assert body_idx > header_idx, (
        "card content must be rendered AFTER the 'Page content "
        "(priority context):' header so the LLM links the two"
    )


def test_library_branch_returns_early_so_priority_language_is_not_diluted():
    """The library branch must return before the generic 'CONTENT RULE'
    block at the bottom of build_rag_system_prompt is appended;
    otherwise the model sees both the strong priority instruction
    AND a generic 'answer from your training knowledge' rule and
    the latter wins.
    """
    out = _build()
    # The generic non-library tail block adds this exact phrase via
    # the "_stage1_subject" branch; if it leaks into a library-branch
    # render, the early-return contract is broken.
    assert "Answer from your general knowledge about this subject" not in out


def test_library_branch_is_inert_without_card_text():
    """No card_context → no library priority header.

    Defensive: a future caller that mis-tags ``source='library'``
    but forgets ``document_text`` should NOT get the priority
    header (which would lie to the LLM about having authoritative
    page content).
    """
    out = build_rag_system_prompt(
        context={"subject_name": "Political Science 2nd Sem NEP"},
        rag_context={"source": "library", "document_text": ""},
        user_info={"id": "u-test"},
        query="hello",
        resolved_intent="notes",
    )
    assert "PRIMARY CONTEXT" not in out


# ── Task #409 — dispatcher remap that gets the library branch to fire ──
#
# The above tests pin the prompt language WHEN source=="library", but the
# real production bug was that card_context payloads enter
# resolve_rag_context() and come back tagged source=="document" (because
# the dispatcher passes the scraped card text through the same
# document_text slot uploaded study PDFs use). The fix lives in
# routes/ai_chat.py: a small remap helper relabels the source from
# "document" to "library" BEFORE build_rag_system_prompt runs.
#
# These tests pin the helper itself plus the end-to-end "remap → prompt
# contains PRIMARY CONTEXT" assertion the architect explicitly asked for.

def test_remap_relabels_document_source_when_card_context_is_present():
    """The hot path — card_context present, resolve_rag_context returned
    source='document', the helper must flip it to 'library'."""
    rag_ctx = {"source": "document", "document_text": "Subject: ...\nActive chapter ..."}
    _remap_card_context_source_to_library(rag_ctx, is_card_context=True)
    assert rag_ctx["source"] == "library"


def test_remap_is_a_noop_when_card_context_is_absent():
    """No card_context → never touch the source. Uploaded study PDFs
    must stay on the 'document' branch (which has the correct
    "quote directly when possible" wording for a PDF the student
    is reading from)."""
    rag_ctx = {"source": "document", "document_text": "Page 1 of student-uploaded.pdf..."}
    _remap_card_context_source_to_library(rag_ctx, is_card_context=False)
    assert rag_ctx["source"] == "document"


def test_remap_is_a_noop_for_internal_rag_hits():
    """Defensive: when the internal RAG path resolved real chapter
    chunks (source='internal'), the card_context flag is irrelevant —
    we must not silently downgrade an internal-RAG hit to the
    library branch which would hide the chapter-chunk grounding the
    'internal' branch renders."""
    rag_ctx = {"source": "internal", "chapters": [{"title": "X", "content": "..."}]}
    _remap_card_context_source_to_library(rag_ctx, is_card_context=True)
    assert rag_ctx["source"] == "internal"


def test_remap_is_idempotent_when_source_is_already_library():
    rag_ctx = {"source": "library", "document_text": "..."}
    _remap_card_context_source_to_library(rag_ctx, is_card_context=True)
    assert rag_ctx["source"] == "library"


def test_end_to_end_card_context_payload_produces_primary_context_prompt():
    """Architect-requested integration assertion: simulate the
    dispatcher's hand-off — a rag_ctx that resolve_rag_context would
    have returned for a card_context payload (source='document',
    document_text=<scraped card text>) — apply the remap, then build
    the prompt. The result MUST contain the new 'PRIMARY CONTEXT'
    header. If a future refactor ever moves the remap back to AFTER
    prompt construction (the original bug), this test fails loudly.
    """
    card_text = (
        "Subject: Political Science 2nd Sem NEP\n"
        "Active chapter (priority context): Federalism\n"
        "Federalism is the constitutional division of powers..."
    )
    rag_ctx = {"source": "document", "document_text": card_text}
    _remap_card_context_source_to_library(rag_ctx, is_card_context=True)
    out = build_rag_system_prompt(
        context={"subject_name": "Political Science 2nd Sem NEP",
                 "board_name": "AHSEC", "class_name": "Class 12"},
        rag_context=rag_ctx,
        user_info={"id": "u-test"},
        query="Explain federalism",
        resolved_intent="notes",
    )
    assert "PRIMARY CONTEXT" in out
    assert "AUTHORITATIVE" in out
    assert "Federalism is the constitutional division of powers" in out
    assert "GROUNDING CONTEXT (Uploaded Study Document)" not in out, (
        "card_context turn must NOT render the uploaded-PDF branch's wording — "
        "the remap is what stops that from happening"
    )
