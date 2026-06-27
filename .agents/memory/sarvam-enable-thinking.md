---
name: Sarvam enable_thinking mode strategy
description: How enable_thinking affects where the final answer lands in the SSE stream for sarvam-30b
---

## Rule
- **English mode**: `enable_thinking=True` — model's internal reasoning goes to `reasoning_content` (hidden), clean final answer comes in `content` field (streamed directly).  No extraction logic needed.
- **Assamese mode**: `enable_thinking=False` — model puts everything (English reasoning + embedded Assamese draft lines) in `reasoning_content`; `_extract_assamese_answer()` collects Assamese-script lines at end-of-stream. `content` is always empty in this mode.

## Why
With `enable_thinking=False` for English, sarvam-30b outputs its full reasoning chain (numbered sections 1-N) AND the final answer all together in `reasoning_content`. Regex-based extraction to separate them was fragile — the model's section structure varies per call (nondeterministic). Switching to `enable_thinking=True` gives a clean API-guaranteed separation.

## How to apply
- In `stream_generate` payload: `"enable_thinking": not is_assamese`
- Track `content_was_yielded` flag; set it `True` in the content-field path.
- At end-of-stream: only call `_extract_english_answer(edu_buf)` if `not content_was_yielded` (fallback for edge cases).
- `_extract_english_answer()` and its `_INLINE_STOPS` / `_META_PHRASES` machinery is kept as a fallback but is rarely needed.
- `max_tokens`: 4000 for Assamese, 2000 for English (full reasoning chain needs the budget).
