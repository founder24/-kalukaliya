---
name: Content Editor browser fixture
description: Non-obvious persistence and endpoint contracts for authenticated Content Editor browser coverage
---

Stateful Content Editor browser fixtures must treat `notes_en` as the formatter's canonical write and mirror it into the legacy `content` field returned to the chapter list. Image-wise PYQ pages are separate chapter-scoped state and must be modeled through their GET, POST, and DELETE endpoints.

**Why:** The formatter PATCH writes `notes_en`, while the chapter-row preview still reads `content`; failing to normalize the fixture makes a reload assertion report a false persistence failure. PYQ page uploads do not travel through the chapter update route.

**How to apply:** Keep the fixture state outside the page instance, normalize formatter PATCH responses before subsequent chapter-list reads, and assert both the browser-visible result and the chapter-specific image-page request sequence.