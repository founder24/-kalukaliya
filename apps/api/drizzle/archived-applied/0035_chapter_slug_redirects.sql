CREATE TABLE IF NOT EXISTS chapter_slug_redirects (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  chapter_id TEXT NOT NULL,
  locale TEXT NOT NULL DEFAULT 'en' CHECK (locale IN ('en', 'as')),
  slug TEXT NOT NULL,
  created_at INTEGER DEFAULT (unixepoch())
);

CREATE UNIQUE INDEX IF NOT EXISTS chapter_slug_redirects_subject_locale_slug_idx
  ON chapter_slug_redirects(subject_id, locale, slug);

CREATE INDEX IF NOT EXISTS chapter_slug_redirects_chapter_idx
  ON chapter_slug_redirects(chapter_id);