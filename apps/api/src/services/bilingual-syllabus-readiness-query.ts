/**
 * Shared D1 query for staff publishing readiness. Counts and timestamps come
 * from the persisted chunk-to-vector map, not just the chapter's index marker.
 */
export const BILINGUAL_SYLLABUS_READINESS_SELECT = `
  WITH rag_counts AS (
    SELECT chapter_id,
      SUM(CASE WHEN source_type = 'notes' AND medium = 'english' AND vector_id IS NOT NULL AND vector_id != '' THEN 1 ELSE 0 END) AS rag_notes_en_count,
      MAX(CASE WHEN source_type = 'notes' AND medium = 'english' AND vector_id IS NOT NULL AND vector_id != '' THEN created_at END) AS rag_notes_en_latest_at,
      SUM(CASE WHEN source_type = 'notes' AND medium = 'assamese' AND vector_id IS NOT NULL AND vector_id != '' THEN 1 ELSE 0 END) AS rag_notes_as_count,
      MAX(CASE WHEN source_type = 'notes' AND medium = 'assamese' AND vector_id IS NOT NULL AND vector_id != '' THEN created_at END) AS rag_notes_as_latest_at,
      SUM(CASE WHEN source_type = 'important_questions' AND medium = 'english' AND vector_id IS NOT NULL AND vector_id != '' THEN 1 ELSE 0 END) AS rag_qa_en_count,
      MAX(CASE WHEN source_type = 'important_questions' AND medium = 'english' AND vector_id IS NOT NULL AND vector_id != '' THEN created_at END) AS rag_qa_en_latest_at,
      SUM(CASE WHEN source_type = 'important_questions' AND medium = 'assamese' AND vector_id IS NOT NULL AND vector_id != '' THEN 1 ELSE 0 END) AS rag_qa_as_count,
      MAX(CASE WHEN source_type = 'important_questions' AND medium = 'assamese' AND vector_id IS NOT NULL AND vector_id != '' THEN created_at END) AS rag_qa_as_latest_at
    FROM chunks
    GROUP BY chapter_id
  )
  SELECT c.id, c.subject_id, c.title, c.title_as, c.slug, c.slug_as, c.status, c.content_type,
         c.meta_description, c.meta_description_as, c.keywords, c.keywords_as,
         c.notes_en, c.notes_as, c.rag_text, c.rag_text_as, c.rag_sections_en, c.rag_sections_as,
         c.qa_en, c.qa_as, c.published_topics, c.rag_updated_at, c.rag_indexed_at,
         COALESCE(r.rag_notes_en_count, 0) AS rag_notes_en_count,
         COALESCE(r.rag_notes_en_latest_at, 0) AS rag_notes_en_latest_at,
         COALESCE(r.rag_notes_as_count, 0) AS rag_notes_as_count,
         COALESCE(r.rag_notes_as_latest_at, 0) AS rag_notes_as_latest_at,
         COALESCE(r.rag_qa_en_count, 0) AS rag_qa_en_count,
         COALESCE(r.rag_qa_en_latest_at, 0) AS rag_qa_en_latest_at,
         COALESCE(r.rag_qa_as_count, 0) AS rag_qa_as_count,
         COALESCE(r.rag_qa_as_latest_at, 0) AS rag_qa_as_latest_at
  FROM chapters c
  LEFT JOIN rag_counts r ON r.chapter_id = c.id
`;
