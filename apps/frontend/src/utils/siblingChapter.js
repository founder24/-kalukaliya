/**
 * Given a flat chapter list (from library bundle) belonging to one subject,
 * find the previous & next chapter for a given current chapter id/slug.
 *
 * Sorting heuristic: respect numeric `order` if present, else fall back to
 * the natural array order from the bundle (which is already chapter order).
 */
export function findSiblingChapters(chapters, currentChapterId, currentChapterSlug) {
  if (!Array.isArray(chapters) || chapters.length === 0) return { prev: null, next: null };
  const sorted = [...chapters].sort((a, b) => {
    const ao = typeof a.order === 'number' ? a.order : Number.MAX_SAFE_INTEGER;
    const bo = typeof b.order === 'number' ? b.order : Number.MAX_SAFE_INTEGER;
    if (ao !== bo) return ao - bo;
    return 0;
  });
  const idx = sorted.findIndex((ch) =>
    (currentChapterId && (ch.id === currentChapterId || ch._id === currentChapterId)) ||
    (currentChapterSlug && ch.slug === currentChapterSlug)
  );
  if (idx === -1) return { prev: null, next: null };
  return {
    prev: idx > 0 ? sorted[idx - 1] : null,
    next: idx < sorted.length - 1 ? sorted[idx + 1] : null,
  };
}

/**
 * Build sibling chapter links inside a subject for fallback "related" content
 * when the SEO related-by-chapter API returns nothing.
 */
export function siblingsAsRelated(chapters, currentChapterId, currentChapterSlug, basePath, limit = 6) {
  if (!Array.isArray(chapters) || chapters.length === 0 || !basePath) return [];
  const filtered = chapters.filter((ch) => {
    if (currentChapterId && (ch.id === currentChapterId || ch._id === currentChapterId)) return false;
    if (currentChapterSlug && ch.slug === currentChapterSlug) return false;
    return ch.slug && (ch.status === undefined || ch.status === 'published');
  });
  return filtered.slice(0, limit).map((ch) => ({
    id: ch.id || ch._id || ch.slug,
    title: ch.title || ch.slug,
    seo_path: `${basePath}/${ch.slug}`,
  }));
}
