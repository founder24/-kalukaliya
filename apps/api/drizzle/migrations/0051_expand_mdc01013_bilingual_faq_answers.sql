-- Expand the existing bilingual course FAQs using only the approved syllabus
-- outline. Preserve each question and its stable metadata, and mark RAG content
-- stale so notes and Q&A must be reindexed before readiness can pass.
UPDATE chapters
SET
  qa_en = json_set(
    qa_en,
    '$[0].answer', 'MDC-01013 examines Assam’s transition into modernity under colonial rule, tracing modern Assamese language, literature, music, culture, political institutions, tribal rights and women’s education.',
    '$[1].answer', 'The syllabus has three units. Unit I covers language, literature and culture. Unit II covers political foundations and tribal rights. Unit III examines women’s education, politics and literature.',
    '$[2].answer', 'Unit I highlights Anandaram Dhekiyal Phukan, Lakshminath Bezbaruah and Banikanta Kakati in language and literature, alongside Jyoti Prasad Agarwala, Bishnu Prasad Rabha and Bhupen Hazarika in music and culture.',
    '$[3].answer', 'Unit II traces political foundations through Asom Chatra Sanmilan and Assam Association, followed by the roles of Tarun Ram Phukan and Gopinath Bordoloi. It also covers Kalicharan Brahma and Bhimbar Deori on tribal consciousness and rights.',
    '$[4].answer', 'Unit III covers Baptist missionaries and women’s education, reformers Chandraprabha Saikiani, Amal Prabha Das and Rajabala Das, and the literary organization Asom Lekhika Samaroh.'
  ),
  qa_as = json_set(
    qa_as,
    '$[0].answer', 'MDC-01013-ত ঔপনিৱেশিক শাসনৰ সময়ত অসমৰ আধুনিক যুগলৈ ৰূপান্তৰ, আধুনিক অসমীয়া ভাষা-সাহিত্য, সংগীত-সংস্কৃতি, ৰাজনৈতিক প্ৰতিষ্ঠান, জনজাতীয় অধিকাৰ আৰু নাৰী শিক্ষাৰ বিষয়ে আলোচনা কৰা হয়।',
    '$[1].answer', 'এই পাঠ্যক্ৰম তিনিটা গোটত বিভক্ত। গোট ১-ত ভাষা, সাহিত্য আৰু সংস্কৃতি আলোচনা কৰা হৈছে। গোট ২-ত ৰাজনৈতিক ভিত্তি আৰু জনজাতীয় অধিকাৰ আছে। গোট ৩-ত নাৰী শিক্ষা, ৰাজনীতি আৰু সাহিত্যৰ বিষয়সমূহ অন্তৰ্ভুক্ত।',
    '$[2].answer', 'গোট ১-ত ভাষা-সাহিত্যৰ ক্ষেত্ৰত আনন্দৰাম ঢেকিয়াল ফুকন, লক্ষ্মীনাথ বেজবৰুৱা আৰু বাণীকান্ত কাকতি অন্তৰ্ভুক্ত। সংগীত-সংস্কৃতিত জ্যোতিপ্ৰসাদ আগৰৱালা, বিষ্ণুপ্ৰসাদ ৰাভা আৰু ভূপেন হাজৰিকাক আলোচনা কৰা হৈছে।',
    '$[3].answer', 'গোট ২-ত অসম ছাত্ৰ সন্মিলন আৰু অসম এছ’চিয়েশ্যনৰ লগতে তৰুণ ৰাম ফুকন আৰু গোপীনাথ বৰদলৈৰ ৰাজনৈতিক ভূমিকা আলোচনা কৰা হৈছে। জনজাতীয় অধিকাৰৰ ক্ষেত্ৰত কালীচৰণ ব্ৰহ্ম আৰু ভিমবৰ দেউৰী অন্তৰ্ভুক্ত।',
    '$[4].answer', 'গোট ৩-ত বেপ্তিষ্ট মিছনেৰীসকলৰ নাৰী শিক্ষাত ভূমিকা, চন্দ্ৰপ্ৰভা সাইকীয়ানী, অমল প্ৰভা দাস আৰু ৰাজবালা দাসৰ সংস্কাৰমূলক অৱদান, আৰু অসম লেখিকা সমাৰোহৰ সাহিত্যিক ভূমিকা আলোচনা কৰা হৈছে।'
  ),
  updated_at = unixepoch(),
  rag_updated_at = unixepoch()
WHERE id IN (
    '6a90409b-3488-47ef-b443-e4c2f1bdf469',
    '9bf76bd3-b7c5-4a51-b66c-10d3a0b69d8d',
    'fe2d8e57-7d3a-4066-8d8c-9f6986b5c623'
  )
  AND slug = 'course-syllabus-mdc-01013-makers-of-modern-assam-semester-1'
  AND status = 'published'
  AND json_array_length(qa_en) = 5
  AND json_array_length(qa_as) = 5;
