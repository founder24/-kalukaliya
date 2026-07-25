---
name: Sarvam enable_thinking mode strategy
description: How enable_thinking affects where the final answer lands; extraction strategy for non-streaming calls
---

## Rule
- **Non-streaming generate() call**: always use `enable_thinking=False`.
  - `content` field: **inconsistently populated** — sometimes holds the clean final answer, sometimes is null even on 200 OK.
  - `reasoning_content` field: **always populated** — contains the full internal reasoning + embedded Assamese drafts.
  - Correct approach: read `content` first; if empty, extract from `reasoning_content`.

- **Streaming generate() call** (English responses): use `enable_thinking=True` — model's internal reasoning goes to `reasoning_content` (hidden), clean final answer comes in `content` field (streamed directly).

## Extraction from reasoning_content (translation tasks)
`_extract_assamese_translation(rc)` runs THREE strategies in parallel and returns the LONGEST result by word count:
- **Strategy C** (primary): line-by-line — collects all Assamese-script lines, deduplicates by first-40-char key keeping last occurrence. Most complete for multi-paragraph content.
- **Strategy A** (secondary): last double-quoted string > 30 chars that is >50% Assamese — captures the model's final assembled output in the "Final Output" section.
- **Strategy B** (tertiary): last paragraph > 60% Assamese — useful when model outputs without quoting.
- Winner = `max(candidates, key=lambda s: len(s.split()))`.

## Why
Taking the longest candidate is critical: Strategy A can return a short concluding sentence (12 words) while the full multi-paragraph translation is in Strategy C (1900+ words). Without this, some chapters are severely truncated.

## How to apply
- In `_do_generate()` closure: `content = (msg.get("content") or "").strip()`. If empty: use `_extract_assamese_translation(rc)` for `is_assamese=True`, `_extract_english_answer(rc)` otherwise.
- Follow with `_strip_think_block(result)` — removes `<think>` tags if model included them in content field.
- `max_tokens`: 2048 for Assamese (sarvam-30b), 1200 for English.

## Triggering retranslation from shell
The backend admin API is the reliable path (not standalone scripts — they die in Replit's ShellExec env):
1. Generate admin JWT: `jwt.encode({"sub": "...", "type": "admin", "role": "admin", "iat": ..., "exp": ...}, settings.ADMIN_JWT_SECRET, algorithm="HS256")`
2. `curl -X POST http://localhost:8000/api/v1/admin/content/seed-assamese -H "Authorization: Bearer $TOKEN" -d '{"force": true, "concurrency": 3}'`
3. StatReload will interrupt the job if you edit backend code files mid-run. Avoid edits while seeding.
