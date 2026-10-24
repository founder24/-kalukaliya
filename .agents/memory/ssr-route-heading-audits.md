---
name: SSR route heading audits
description: Avoid scoring shared shell headings as route-specific content in prerendered HTML.
---

When auditing prerendered pages, do not assume the first `<h1>` belongs to the route. The shared shell can emit its own heading before the page-specific heading; use the route heading and its visible content when checking metadata or JSON-LD.

**Why:** A full topic-page audit initially compared valid topic schema against the shared shell heading, producing false failures for every page.

**How to apply:** For sitemap-wide SSR checks, identify the route-specific heading (or use the last heading when the template guarantees shell-first order) and compare it with the page title, visible definition, and structured data.
