---
name: Bilingual subject card metadata
description: How the public library localizes subject cards without adding a subject-description column.
---

For bilingual subject cards, use the existing subject `name_as` value for the Assamese title and derive the localized card description from the published syllabus chapter’s `meta_description_as`. This avoids duplicating card-summary content or adding a subject-table migration.

**Why:** The subjects table has an Assamese name but no Assamese description field; the syllabus chapter already owns language-specific SEO metadata and is the content source for the card.

**How to apply:** Keep the published syllabus slug under `course-syllabus-`. Set its Assamese meta description and expose the derived `description_as` value in library-bundle and subject-resolution responses. In Assamese mode, use both localized fields with English fallback.
