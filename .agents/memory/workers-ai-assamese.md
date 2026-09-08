---
name: Workers AI Assamese generation
description: Durable model-selection and validation constraints for reliable Assamese chat on Cloudflare Workers AI.
---

Use `@cf/aisingapore/gemma-sea-lion-v4-27b-it` through non-streaming Workers AI for Assamese answers. Keep the low-latency Llama streaming path for English.

**Why:** SEA-LION produced materially cleaner Assamese than the available Llama and Qwen options. Its binding returns an OpenAI-style non-streaming response even when a streaming request is attempted, so a generic streaming wrapper silently falls back to another model. Assamese answers are already buffered for whole-answer language validation.

**How to apply:** Route Assamese generation directly through the SEA-LION non-streaming call, parse `choices[0].message.content`, cap output, and use a bounded timeout. Do not reintroduce lexical Bengali-to-Assamese rewriting because it can corrupt formulas, quotations, names, and source text.

Assamese and Bengali share the U+0980–U+09FF block, while the danda `।` is U+0964 in the Devanagari range but is valid Assamese punctuation.

**Why:** A whole-range Devanagari rejection treated every normal Assamese sentence ending in `।` as Hindi leakage. Single Bengali-looking words are also not sufficient language evidence because vocabulary overlaps.

**How to apply:** Exclude danda punctuation from Devanagari-letter checks, use multiple lexical signals before declaring Bengali leakage, allow technical Latin terms proportionally, and verify with real Assamese-script plus romanized-Assamese production probes.

Strict dialect validation is advisory, not a reason to strand students behind an error card.

**Why:** Assamese and Bengali cannot be perfectly separated with deterministic Unicode and word lists. A readable script-heavy answer is more useful than a terminal “Assamese unavailable” response when the strict heuristic remains uncertain after one quality retry.

**How to apply:** Retry once through SEA-LION, then deliver the repaired or initial answer when it is script-heavy and neither Devanagari nor predominantly English. Reserve terminal language errors for genuinely unusable output.