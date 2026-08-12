---
name: Sarvam enable_thinking mode strategy
description: When to use enable_thinking=True vs False in Sarvam API calls, and the planning-leak bug that resulted from using True in non-streaming mode.
---

## Rule

**Non-streaming `generate()` calls: always `enable_thinking=False`.**
**Streaming `chat_service` calls: `enable_thinking=True` for English, `False` for Assamese.**

## Why

`enable_thinking=True` in non-streaming mode does NOT reliably separate the model's chain-of-thought from the `content` field. Sarvam-30b inconsistently places its planning preamble ("Topic Selection:", "Drafting Content:", "Word Count Check:", "Constraints Analysis:") directly into `content`, polluting stored notes.

With `enable_thinking=False` in non-streaming:
- `content` field: sometimes holds the clean final answer, sometimes null/empty
- `reasoning_content`: always populated with full reasoning + embedded final answer
- Fallback: `_extract_english_answer(reasoning_content)` reliably extracts the answer

The streaming path uses `enable_thinking=True` for English correctly because the streaming handler buffers `reasoning_content` separately and only yields `content` chunks to the client.

## Planning preamble detection (for cleanup scripts)

Real-world patterns that appeared in `notes_en` due to this bug:
```
*   **Topic Selection:** ...          # bullet + bold
*   **Initial Plan:** ...             # bullet + bold  
1.  **Deconstruct the Request:** ...  # numbered + bold
## Constraints Analysis:              # ## planning heading
## Drafting the Notes ...:            # ## planning heading
## Topic Planning:                    # ## planning heading
## Key Themes and Elements...:        # ## planning heading
## Plan:                              # ## planning heading
## Structuring the Notes              # ## planning heading
```

Cleanup script: `apps/backend/scripts/clean_polluted_notes.py`

## `_clean_notes_output` fix (ahsec_ingest.py)

Step 1 now ONLY anchors on `##\s` headings to find the start of real content.
The old code also matched `**Topic N:` bold lines — which appear in planning mental-outline sections — causing it to anchor too early and leave the rest of the planning intact.

**How to apply:** Any time you add or modify a non-streaming Sarvam call, ensure `enable_thinking=False`. The streaming path in `chat_service.py` retains `enable_thinking=True` for English — do not change that.
