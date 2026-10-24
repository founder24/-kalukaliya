-- Forward-only content corrections verified against official NCERT sources.
-- Physics: Class XI Physics Part I, Chapter 3, section 3.2, "Scalars and Vectors".
-- https://ncert.nic.in/textbook/pdf/keph103.pdf
-- Business Studies: Class XI Business Studies, Chapter 2, "Forms of Business Organisation".
-- https://ncert.nic.in/textbook/pdf/kebs102.pdf
-- Federalism: Class XI Indian Constitution at Work, Chapter 7, "Federalism",
-- section "Federalism in Nigeria". This material does not belong under Nationalism.
-- https://ncert.nic.in/textbook/pdf/keps207.pdf

UPDATE chapters AS chapter
SET published_topics = json_set(
  chapter.published_topics,
  '$[' || (
    SELECT topic.key
    FROM json_each(chapter.published_topics) AS topic
    WHERE json_extract(topic.value, '$.id') = 'topic-6a19e0d84d8e6ddb2deb7d07-en-250'
      AND json_extract(topic.value, '$.topic_slug') = 'vector-and-scalar-foundations-invariance-and-algebraic-rules'
      AND json_extract(topic.value, '$.language') = 'English'
    LIMIT 1
  ) || '].definition',
  'A scalar quantity has magnitude only and is specified by a number and its unit. It has no associated direction. Distance, mass, temperature, and time are examples. Scalars combine according to ordinary algebra. A vector quantity has both magnitude and direction and follows the triangle law (equivalently, the parallelogram law) of addition. Displacement, velocity, acceleration, and force are examples. A vector is written in bold, such as 𝐯, or with an arrow, such as v⃗. Its magnitude is written |𝐯|. Source: NCERT, Physics Part I, Class XI, Chapter 3, section 3.2, “Scalars and Vectors.”'
),
updated_at = unixepoch()
WHERE chapter.id = '6a19e0d84d8e6ddb2deb7d07'
  AND chapter.status = 'published'
  AND json_valid(chapter.published_topics)
  AND EXISTS (
    SELECT 1
    FROM json_each(chapter.published_topics) AS topic
    WHERE json_extract(topic.value, '$.id') = 'topic-6a19e0d84d8e6ddb2deb7d07-en-250'
      AND json_extract(topic.value, '$.topic_slug') = 'vector-and-scalar-foundations-invariance-and-algebraic-rules'
      AND json_extract(topic.value, '$.language') = 'English'
  );

UPDATE chapters AS chapter
SET published_topics = json_set(
  chapter.published_topics,
  '$[' || (
    SELECT topic.key
    FROM json_each(chapter.published_topics) AS topic
    WHERE json_extract(topic.value, '$.id') = 'topic-6a19e0f44d8e6ddb2deb7d6f-as-22'
      AND json_extract(topic.value, '$.topic_slug') = 'ব্যৱসায়-সংগঠনৰ-প্ৰকাৰসমূহ'
      AND json_extract(topic.value, '$.language') = 'Assamese'
    LIMIT 1
  ) || '].definition',
  'ব্যৱসায়ৰ আকাৰ, পুঁজিৰ প্ৰয়োজন আৰু মালিকীস্বত্বৰ ভিত্তিত বিভিন্ন ব্যৱসায়-সংগঠন বাছনি কৰা হয়। একক মালিকীস্বত্বত এজন ব্যক্তিয়ে ব্যৱসায়ৰ মালিকীস্বত্ব, পৰিচালনা আৰু নিয়ন্ত্ৰণ কৰে। অংশীদাৰী ব্যৱসায়ত দুই বা ততোধিক অংশীদাৰে চুক্তিৰে ব্যৱসায় চলায় আৰু লাভ-লোকচান ভাগ কৰে। যৌথ হিন্দু পৰিয়াল ব্যৱসায় পৰিয়ালৰ সদস্যসকলৰ দ্বাৰা গঠিত হয় আৰু কৰ্তাই ইয়াৰ পৰিচালনা কৰে। সমবায় সমিতিত সদস্যসকলে স্বেচ্ছাই যোগ দি উমৈহতীয়া সুবিধাৰ বাবে কাম কৰে। যৌথ মূলধনী কোম্পানীত শ্বেয়াৰধাৰীয়ে মূলধন যোগায় আৰু কোম্পানীটো নিজস্ব আইনগত সত্তাৰে পৰিচালিত হয়। কোনটো ৰূপ উপযুক্ত হ’ব সেয়া ব্যৱসায়ৰ প্ৰয়োজন আৰু প্ৰতিটো ৰূপৰ বৈশিষ্ট্য, সুবিধা-অসুবিধাৰ ওপৰত নিৰ্ভৰ কৰে। উৎস: NCERT, Business Studies, Class XI, Chapter 2, “Forms of Business Organisation.”'
),
updated_at = unixepoch()
WHERE chapter.id = '6a19e0f44d8e6ddb2deb7d6f'
  AND chapter.status = 'published'
  AND json_valid(chapter.published_topics)
  AND EXISTS (
    SELECT 1
    FROM json_each(chapter.published_topics) AS topic
    WHERE json_extract(topic.value, '$.id') = 'topic-6a19e0f44d8e6ddb2deb7d6f-as-22'
      AND json_extract(topic.value, '$.topic_slug') = 'ব্যৱসায়-সংগঠনৰ-প্ৰকাৰসমূহ'
      AND json_extract(topic.value, '$.language') = 'Assamese'
  );

-- Remove the Nigeria/federalism topic from the Nationalism chapter. The
-- source confirms that this example belongs in a Federalism chapter and this
-- migration does not invent or create an unverified replacement chapter.
UPDATE chapters AS chapter
SET published_topics = json_remove(
  chapter.published_topics,
  '$[' || (
    SELECT topic.key
    FROM json_each(chapter.published_topics) AS topic
    WHERE json_extract(topic.value, '$.id') = 'topic-6a1f19fbcee3667f3ecc3be9-en-297'
      AND json_extract(topic.value, '$.topic_slug') = 'challenges-to-federalism'
      AND json_extract(topic.value, '$.language') = 'English'
    LIMIT 1
  ) || ']'
),
updated_at = unixepoch()
WHERE chapter.id = '6a1f19fbcee3667f3ecc3be9'
  AND chapter.status = 'published'
  AND json_valid(chapter.published_topics)
  AND EXISTS (
    SELECT 1
    FROM json_each(chapter.published_topics) AS topic
    WHERE json_extract(topic.value, '$.id') = 'topic-6a1f19fbcee3667f3ecc3be9-en-297'
      AND json_extract(topic.value, '$.topic_slug') = 'challenges-to-federalism'
      AND json_extract(topic.value, '$.language') = 'English'
  );
