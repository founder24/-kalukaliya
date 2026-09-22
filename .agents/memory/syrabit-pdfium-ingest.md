---
name: Syrabit PDFium ingestion
description: License-safe PDF extraction and OCR rendering requirements for backend ingestion
---

Use pypdfium2 for PDF text extraction and page rendering instead of PyMuPDF. PDFium's
to_pil() rendering path requires Pillow to be declared as an explicit backend dependency,
and rendered images should be copied before the native bitmap is released.

**Why:** the license audit rejected PyMuPDF's AGPL/commercial distribution, while a
PDFium-only dependency set failed at runtime when Pillow was not installed explicitly.

**How to apply:** keep the shared PDFium helper responsible for text-page cleanup and
detached Pillow images; validate both extraction and rendering in ingestion tests.