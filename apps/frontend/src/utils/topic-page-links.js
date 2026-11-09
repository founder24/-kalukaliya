export function topicPageApiPath(chapterId, topicSlug, language = 'en') {
  if (chapterId == null || !topicSlug) return null;

  const path = `/content/chapters/${encodeURIComponent(String(chapterId))}/topics/${encodeURIComponent(String(topicSlug))}/page`;
  return language === 'as' ? `${path}?lang=as` : path;
}

export function topicPageUrl(topic, chapterPath, language = 'en') {
  if (!topic || topic[`topic_page_available_${language}`] !== true) return null;

  const slug = language === 'as'
    ? topic.topic_slug_as
    : (topic.topic_slug || topic.slug);
  const base = typeof chapterPath === 'string' ? chapterPath.replace(/\/+$/, '') : '';
  if (typeof slug !== 'string' || !slug.trim() || !base) return null;

  return `${base}/topic/${encodeURIComponent(slug.trim())}`;
}

export function topicPageMatchesRoute(response, topicSlug, language = 'en', chapterId = null) {
  const topic = response?.topic;
  if (!topic || !topicSlug) return false;
  if (
    chapterId != null &&
    String(response.chapter_id) !== String(chapterId)
  ) return false;

  const slugs = [topic.topic_slug, topic.topic_slug_as, topic.slug]
    .filter((value) => typeof value === 'string');
  return slugs.includes(topicSlug) && (!topic.language || topic.language === language);
}
