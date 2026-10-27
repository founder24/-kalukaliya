export function enumerateSitemapSubjectRoutes(chapterPaths, bundleRoutes, bundleChapters = []) {
  const pathsBySubjectRoute = new Map();
  for (const chapterPath of chapterPaths) {
    const segments = chapterPath.split("/").filter(Boolean);
    if (segments.length !== 4) continue;
    const subjectUrl = `/${segments.slice(0, 3).join("/")}`;
    if (!pathsBySubjectRoute.has(subjectUrl)) pathsBySubjectRoute.set(subjectUrl, new Set());
    pathsBySubjectRoute.get(subjectUrl).add(segments[3]);
  }

  const chaptersBySubject = new Map();
  for (const chapter of bundleChapters) {
    if (!chapter?.subject_id || !chapter?.slug) continue;
    if (!chaptersBySubject.has(chapter.subject_id)) chaptersBySubject.set(chapter.subject_id, new Set());
    chaptersBySubject.get(chapter.subject_id).add(chapter.slug);
  }

  const routes = [];
  for (const [subjectUrl, publishedSlugs] of pathsBySubjectRoute) {
    const [board, classSlug, subjectSlug] = subjectUrl.split("/").filter(Boolean);
    const candidates = bundleRoutes.filter((route) =>
      `/${route.board}/${route.classSlug}/${route.subjectSlug}` === subjectUrl,
    );
    const ranked = candidates.map((route) => {
      const subjectId = route.subject?.id || route.subject?._id;
      const availableSlugs = chaptersBySubject.get(subjectId) || new Set();
      const matches = [...publishedSlugs].filter((slug) => availableSlugs.has(slug)).length;
      return { route, matches, subjectId };
    }).sort((a, b) => b.matches - a.matches);

    if (
      ranked.length > 1 &&
      ranked[0].matches > 0 &&
      ranked[0].matches === ranked[1].matches &&
      ranked[0].subjectId !== ranked[1].subjectId
    ) {
      throw new Error(
        `[prerender-routes] ambiguous stream for published subject ${subjectUrl}: ` +
        `${ranked.filter((candidate) => candidate.matches === ranked[0].matches)
          .map((candidate) => candidate.route.streamSlug || "(no stream)").join(", ")}`,
      );
    }

    const selected = ranked[0]?.route;
    routes.push({
      ...(selected || {}),
      board,
      classSlug,
      subjectSlug,
      // Preserve the bundle's stream-specific subject record when the
      // public URL omits its stream segment. This resolves duplicate
      // slugs from the published chapter inventory, not record order.
      subject: selected?.subject || {},
    });
  }
  return routes;
}

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
