export function selectChapterCandidates({
  subjectUrl,
  rankedChapters,
  publishedChapterPaths,
  strict,
  limit,
}) {
  // A current sitemap is the public/indexable chapter contract in every
  // build mode. Keep the broader bundle-order fallback only when a local
  // development build has no sitemap to consult.
  const restrictToSitemap = strict || publishedChapterPaths.size > 0;
  const unique = new Map();
  for (const chapter of rankedChapters) {
    if (!chapter?.slug) continue;
    const path = `${subjectUrl}/${chapter.slug}`;
    if (restrictToSitemap && !publishedChapterPaths.has(path)) continue;
    if (!unique.has(chapter.slug)) unique.set(chapter.slug, chapter);
  }
  return [...unique.values()].slice(0, limit);
}

export function chapterLookupRecovery(
  chapterPath,
  publishedChapterPaths,
  hasStaticFallback,
  allowUnlistedStaticFallback = false,
) {
  if (publishedChapterPaths.has(chapterPath)) {
    if (hasStaticFallback) return 'use-static-fallback';
    throw new Error(
      `[prerender-routes] published sitemap chapter ${chapterPath} could not be fetched ` +
        'and has no static fallback; refusing to emit a shell instead of a chapter snapshot',
    );
  }
  if (hasStaticFallback && allowUnlistedStaticFallback) return 'use-static-fallback';
  return 'skip-unpublished';
}
