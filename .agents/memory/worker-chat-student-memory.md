---
name: Worker chat student memory
description: Durable rules for reading and writing authenticated student memory in the Cloudflare chat pipeline.
---

Authenticated chat should load a small, recent, bounded set of student memories alongside retrieval and conversation history, then use those memories only when relevant. Successful authenticated exchanges should refresh a deterministic per-question memory; anonymous chats must never write long-term memory.

**Why:** The D1 memory table and profile UI existed, but the active Cloudflare chat pipeline neither read nor wrote it, so personalization was nonfunctional after the Cloudflare-only cutover.

**How to apply:** Keep memory retrieval parallel with RAG, cap prompt size, distinguish personalization from authoritative curriculum evidence, avoid announcing stored memories, and preserve user deletion controls.