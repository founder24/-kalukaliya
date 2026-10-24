-- Correct only the published English topic carrying the reviewed OCR corruption.
-- This is intentionally a forward-only, guarded update. Do not edit the applied
-- topic-publication migration that originally introduced the record.
UPDATE chapters AS chapter
SET published_topics = json_set(
  chapter.published_topics,
  '$[' || (
    SELECT topic.key
    FROM json_each(chapter.published_topics) AS topic
    WHERE json_extract(topic.value, '$.id') = 'topic-6a1f87832c1a19d2142de410-en-430'
      AND json_extract(topic.value, '$.topic_slug') = 'government-spending-multiplier'
      AND json_extract(topic.value, '$.language') = 'English'
    LIMIT 1
  ) || '].definition',
  'The government-spending multiplier measures the change in equilibrium income caused by a change in government purchases. With lump-sum taxes and transfers held constant, the equilibrium-income equation is Y* = (1 ÷ (1 − c)) × (C̄ − cT + cTR̄ + I + G). If government purchases rise by ΔG, the new equilibrium is Y* + ΔY = (1 ÷ (1 − c)) × (C̄ − cT + cTR̄ + I + G + ΔG). Subtracting the original equation gives ΔY = (1 ÷ (1 − c)) × ΔG, so the government-spending multiplier is ΔY ÷ ΔG = 1 ÷ (1 − c). Here c is the marginal propensity to consume. For c = 0.8, the multiplier is 5. A 100-unit increase in G raises equilibrium income by 500 units, with other factors unchanged. Source: NCERT, Introductory Macroeconomics, Chapter 5, “Changes in Government Expenditure,” equations 5.4–5.6, page 73 (Reprint 2026–27).'
),
updated_at = unixepoch()
WHERE chapter.id = '6a1f87832c1a19d2142de410'
  AND chapter.status = 'published'
  AND json_valid(chapter.published_topics)
  AND EXISTS (
    SELECT 1
    FROM json_each(chapter.published_topics) AS topic
    WHERE json_extract(topic.value, '$.id') = 'topic-6a1f87832c1a19d2142de410-en-430'
      AND json_extract(topic.value, '$.topic_slug') = 'government-spending-multiplier'
      AND json_extract(topic.value, '$.language') = 'English'
      AND (
        instr(COALESCE(json_extract(topic.value, '$.definition'), ''), 'AY = 7/5G') > 0
        OR instr(COALESCE(json_extract(topic.value, '$.definition'), ''), 'AY = 1/2.6T%G') > 0
      )
  );
