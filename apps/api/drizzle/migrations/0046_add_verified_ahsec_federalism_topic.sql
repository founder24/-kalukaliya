-- AHSEC's official Class XI textbook list includes Political Science,
-- "Indian Constitution at Work" (English):
-- https://ahsec.assam.gov.in/Class_XI_textbook
-- AHSEC-hosted English textbook:
-- https://ahsec.assam.gov.in/wp-content/uploads/2025/06/Indian-constitution-at-work_XI-Freebooks.pdf
-- The matching NCERT Class XI book places "Federalism" at Chapter Seven and
-- includes the Nigeria case under "Federalism in Nigeria":
-- https://ncert.nic.in/textbook/pdf/keps2ps.pdf
-- https://ncert.nic.in/textbook/pdf/keps207.pdf
--
-- The existing HS 1st Year Political Science subject has no Federalism
-- chapter record. Create the verified route without altering Nationalism.
-- This migration is not applied here; release publication remains separate.

INSERT INTO chapters (
  id, subject_id, title, slug, chapter_number, status, content_type, published_topics
)
SELECT
  'bb6e8973ef5d3ba462932b3c',
  '6a19e0eb4d8e6ddb2deb7d4f',
  'Federalism',
  'federalism',
  7,
  'published',
  'standard',
  '[]'
WHERE NOT EXISTS (
  SELECT 1
  FROM chapters
  WHERE subject_id = '6a19e0eb4d8e6ddb2deb7d4f'
    AND slug = 'federalism'
);

UPDATE chapters AS chapter
SET published_topics = json_insert(
  COALESCE(NULLIF(chapter.published_topics, ''), '[]'),
  '$[#]',
  json('{"id":"topic-bb6e8973ef5d3ba462932b3c-en-001","language":"English","title":"Challenges to Federalism","topic_slug":"challenges-to-federalism","definition":"NCERT’s Nigeria case shows how federalism can be strained when regions and communities do not trust one another. The chapter describes tensions among the Yoruba, Ibo, and Hausa-Fulani groups and notes that, after democracy was restored in 1999, religious differences and disputes over control of oil revenues continued. It presents Nigeria as an example of overlapping religious, ethnic, and economic differences among the units. Source: NCERT, Indian Constitution at Work, Class XI, Chapter 7, “Federalism,” section “Federalism in Nigeria,” pp. 156–157 (Reprint 2026–27). https://ncert.nic.in/textbook/pdf/keps207.pdf","definition_status":"ok","status":"published"}')
),
updated_at = unixepoch()
WHERE chapter.subject_id = '6a19e0eb4d8e6ddb2deb7d4f'
  AND chapter.slug = 'federalism'
  AND chapter.status = 'published'
  AND json_valid(COALESCE(NULLIF(chapter.published_topics, ''), '[]'))
  AND NOT EXISTS (
    SELECT 1
    FROM json_each(COALESCE(NULLIF(chapter.published_topics, ''), '[]')) AS existing
    WHERE json_extract(existing.value, '$.topic_slug') = 'challenges-to-federalism'
      AND COALESCE(json_extract(existing.value, '$.language'), 'English') = 'English'
  );
