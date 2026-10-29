ALTER TABLE subjects
  ADD COLUMN publication_profile TEXT NOT NULL DEFAULT 'standard'
  CHECK (publication_profile IN ('standard', 'bilingual_syllabus'));