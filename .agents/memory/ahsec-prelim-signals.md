---
name: AHSEC prelim-page detection
description: _PRELIM_SIGNALS regex in ahsec_ingest.py; patterns that slipped through causing textbook publication-page text to be stored as chapter notes.
---

# AHSEC prelim-page detection

## The rule
`_PRELIM_SIGNALS` in `ahsec_ingest.py` must catch ALL textbook-metadata page patterns or they get passed to Sarvam and returned as structured "notes" with headings like "## Textbook Publication Details".

**Why:** 16 chapters were found with publication-page content in `notes_en` because the initial regex missed the specific heading formats used by ASTPPCL/NCERT textbooks.

## Patterns that slipped through (now added)
- `Textbook Publication Details` — formatted section heading on copyright page
- `Educational Philosophy` — standard NCERT section heading
- `Content Rationalization` — post-COVID NCERT section
- `National Curriculum Framework` — NCF reference on prelim pages
- `ASTPPCL` / `Assam State Textbook Production` — publisher abbreviation
- `About the Textbook` / `Textbook Overview` / `Textbook Information`
- `Purpose and Approach of the Textbook`
- `Adopted by Assam Higher Secondary`
- `printed on \d+gsm` / `published for the session` / `illustrations by ncert`

## How to apply
- If a new subject's chapters show boilerplate text (publisher info, philosophy, rationalization), add the heading text to `_PRELIM_SIGNALS` in `ahsec_ingest.py`
- Then clear the affected chapters' `notes_en`/`content_en`/`rag_sections_en` to null/[] so fill-gaps or `--force` regenerates them
- Detection regex checks the first 600 chars of `notes_en` to identify pollution
