-- Migration 0004: add PYQ columns to chapters
--
-- Mirrors the MongoDB chapter fields used by the Python backend for chapter-level
-- PYQ uploads (staff_content.py: pyq_pdf_url + pyq_papers).
--
--   pyq_pdf_url  — single PDF/image uploaded via POST /chapter/:id/upload-pyq
--   pyq_papers   — JSON array of page-image objects appended via POST /chapter/:id/pyq-papers

ALTER TABLE chapters ADD COLUMN pyq_pdf_url TEXT;
ALTER TABLE chapters ADD COLUMN pyq_papers  TEXT NOT NULL DEFAULT '[]';
