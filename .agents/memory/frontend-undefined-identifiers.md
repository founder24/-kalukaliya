---
name: Frontend undefined identifiers
description: Why successful Vite builds are insufficient to validate extracted React admin components.
---

Treat a successful Vite build as insufficient proof that an extracted JSX component is safe from unbound identifiers. Shared UI helpers and parent handlers can remain unresolved until the affected render branch executes.

**Why:** A staff health tab passed production builds while several imported components and a callback prop were missing, causing sequential runtime `ReferenceError` failures that cascaded across unrelated health checks.

**How to apply:** After splitting or moving admin JSX, mount the real parent and active tab in a unit/integration test. Prefer adding a static undefined-variable check so missing JSX imports and handler props fail before runtime.