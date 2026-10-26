-- NCERT's official Class XII Introductory Macroeconomics, Chapter 5,
-- "Government Budget and the Economy", section "Changes in Government
-- Expenditure", gives this derivation in equations (5.4), (5.4a), (5.5),
-- and (5.6), p. 73 (Reprint 2026-27):
-- https://ncert.nic.in/textbook/pdf/leec105.pdf
--
-- Forward-only and narrowly scoped: repair only the published canonical
-- chapter/topic pair while both reviewed OCR-corrupted equations are present.
-- This is safe to rerun and does not change any other topic.
UPDATE chapters AS chapter
SET published_topics = json_set(
  chapter.published_topics,
  '$[' || (
    SELECT topic.key
    FROM json_each(chapter.published_topics) AS topic
    WHERE json_extract(topic.value, '$.id') = 'topic-6a1f87832c1a19d2142de410-en-430'
      AND json_extract(topic.value, '$.topic_slug') = 'government-spending-multiplier'
      AND json_extract(topic.value, '$.language') = 'English'
      AND json_extract(topic.value, '$.title') = 'Government Spending Multiplier'
    LIMIT 1
  ) || '].definition',
  'The government-spending multiplier measures the change in equilibrium income caused by a change in government purchases. NCERT equation (5.4) gives the original equilibrium: Y* = (1 ÷ (1 − c)) × (C̄ − cT + cTR̄ + I + G). When government purchases change from G to G + ΔG and equilibrium income changes from Y* to Y* + ΔY, equation (5.4a) gives: (Y* + ΔY) = (1 ÷ (1 − c)) × (C̄ − cT + cTR̄ + I + G + ΔG). Subtracting equation (5.4) from equation (5.4a) yields equation (5.5): ΔY = (1 ÷ (1 − c)) × ΔG. Therefore, equation (5.6) gives the government-spending multiplier: ΔY ÷ ΔG = 1 ÷ (1 − c). This derivation holds taxes constant, as stated by NCERT. Source: NCERT, Introductory Macroeconomics, Class XII, Chapter 5, “Government Budget and the Economy,” section “Changes in Government Expenditure,” equations (5.4), (5.4a), (5.5), and (5.6), p. 73 (Reprint 2026–27). https://ncert.nic.in/textbook/pdf/leec105.pdf'
  ),
  updated_at = unixepoch()
WHERE chapter.id = '6a1f87832c1a19d2142de410'
  AND chapter.title = 'Government Budget and the Economy'
  AND chapter.slug = 'government-budget-and-the-economy'
  AND chapter.status = 'published'
  AND json_valid(chapter.published_topics)
  AND EXISTS (
    SELECT 1
    FROM json_each(chapter.published_topics) AS topic
    WHERE json_extract(topic.value, '$.id') = 'topic-6a1f87832c1a19d2142de410-en-430'
      AND json_extract(topic.value, '$.topic_slug') = 'government-spending-multiplier'
      AND json_extract(topic.value, '$.language') = 'English'
      AND json_extract(topic.value, '$.title') = 'Government Spending Multiplier'
      AND instr(COALESCE(json_extract(topic.value, '$.definition'), ''), 'AY = 7/5G') > 0
      AND instr(COALESCE(json_extract(topic.value, '$.definition'), ''), 'AY = 1/2.6T%G') > 0
  );
