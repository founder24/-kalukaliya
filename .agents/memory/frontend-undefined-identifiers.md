---
name: Frontend undefined identifiers
description: Why successful Vite builds are insufficient to validate extracted React admin components.
---

Treat a successful Vite build as insufficient proof that an extracted JSX component is safe from unbound identifiers. Pair ESLint's core `no-undef` with React's `jsx-no-undef`: core catches variables and omitted destructured props, but not JSX tag names.

**Why:** A staff health tab passed production builds while several imported components and a callback prop were missing, causing sequential runtime `ReferenceError` failures that cascaded across unrelated health checks.

**How to apply:** Keep the check in normal frontend validation and retain regression snippets for a missing JSX component and handler prop. Register plugins named by inline directives even when their rules are disabled. Do not upgrade ESLint majors until the Babel parser and React plugins support them.