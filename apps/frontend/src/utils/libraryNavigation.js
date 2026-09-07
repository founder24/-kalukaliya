/**
 * Whether a pathname represents a public chapter route.
 *
 * Chapter URLs have an optional language prefix, an optional stream segment,
 * and may include either a legacy page suffix or a topic suffix. Keeping this
 * route knowledge in one place prevents the public and signed-in mobile navs
 * from drifting apart.
 */
export function isChapterRoute(pathname) {
  if (typeof pathname !== 'string') return false;

  const segments = pathname.split('/').filter(Boolean);
  const routeSegments = segments[0] === 'as' ? segments.slice(1) : segments;

  // /:board/:class/:subject/:chapter
  if (routeSegments.length === 4) return true;

  // /:board/:class/:stream/:subject/:chapter
  // or /:board/:class/:subject/:chapter/:legacyPage
  if (routeSegments.length === 5) return true;

  // Chapter topic routes have one of the two supported chapter prefixes.
  return (
    (routeSegments.length === 6 && routeSegments[4] === 'topic') ||
    (routeSegments.length === 7 && routeSegments[5] === 'topic')
  );
}

export function isLibraryNavigationActive(pathname, libraryPath = '/library') {
  if (typeof pathname !== 'string') return false;

  return (
    pathname === libraryPath ||
    pathname.startsWith(`${libraryPath}/`) ||
    isChapterRoute(pathname)
  );
}