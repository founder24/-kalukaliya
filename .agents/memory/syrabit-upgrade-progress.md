---
name: Syrabit god-level upgrade progress
description: Which phases of the God-Level upgrade plan are done; what's left
---

# Syrabit God-Level Upgrade — Phase Tracker

## Completed (Sprint 1 + 2)

### P1.1 — Non-streaming confidence gate (chat.py)
`_maybe_retrieve()` now returns `(chunks, match_score)` tuple. Web search blocked
at `match_score >= CONFIDENCE_MID` (0.65). Eliminates unnecessary DuckDuckGo
calls on strong topic matches in the `/chat/` POST endpoint.

### P1.2 — Remove web from MID in streaming (chat.py)
MID confidence block in `chat_stream()` no longer runs a parallel web search.
`web_chunks = []` explicitly set; saves 200-400ms for queries scoring 0.65-0.80.

### P1.3 — Bilingual system prompts
Already implemented before this sprint. Both en/as paths existed in build_system_prompt().

### P1.4 — Full source card chain in MessageBubble
- `source_card.py to_sse_dict()`: renamed `ctx_board_name/slug/class_name` to
  `rag_board_name/slug/rag_class_name`; added `rag_subject_slug`, `rag_class_slug`
  (derived from class_level: "Class 12" → "class-12").
- `build_source_card()`: derives `board_name = board_slug.upper()` for display.
- `MessageBubble.jsx`: added `topicLabel` variable + breadcrumb row showing
  `AHSEC › Class 12 › Physics › Chapter › Topic` above the chapter h4.

### P4.1 — Auto conversation titles (chat_service.py)
Added `_generate_title(user_message)` static method (first 8 words, 60 char cap,
preserves Assamese script). `save_chat()` checks `Chat.find_one(session_id)` —
sets title only when no prior doc exists for the session.

### P2.1 — New Chapter model fields (content.py)
Added: `rag_text_en`, `rag_text_as`, `notes_en`, `notes_as`, `pyq_pdf_url`,
`pyq_rag_text`. Schema-less MongoDB — old docs return None for new fields.

### P2.2 — RAG retrieval prefers rag_text (chat_service.py)
`retrieve_context_from_chapter()` now reads `rag_text_as → content_as → rag_text_en → content_en`
for Assamese, and `rag_text_en → content_en` for English.

## Remaining (Sprints 3 + 4)

- P2.3: Vectorize chunk ingestion to use rag_text
- P3.1: Admin two-tab editor (Reader Content / RAG Content)
- P5.1: Rich library subject cards (2-col grid, thumbnail, description, tags)
- P5.2: Textbook-style chapter page layout (720px column, 14px body)
- P6.1: Profile board/class change dispatches `syrabit:onboarding-updated` event
- P6.2: Board/class injected into chat system prompt from request body
- P7.1: Admin conversation message viewer (slide-in panel)
- P8.1: MongoDB fallback blacklist for token revocation on logout
- P9.1: CF Worker forwards CF-Connecting-IP to backend

**Why:** Architecture rules (content dual-purpose split, web search gating) are
now enforced. Remaining sprints are UX, admin tooling, and security hardening.
