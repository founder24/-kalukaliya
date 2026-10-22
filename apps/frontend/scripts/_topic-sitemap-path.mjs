export function canonicalTopicSitemapPath(pathname) {
  const marker = "/topic/";
  const markerIndex = pathname.lastIndexOf(marker);
  if (markerIndex < 0) return pathname;

  const prefix = pathname.slice(0, markerIndex + marker.length);
  const encodedSlug = pathname.slice(markerIndex + marker.length);
  let slug = encodedSlug;
  try {
    slug = decodeURIComponent(encodedSlug);
  } catch {
    // Keep malformed percent sequences as literal slug text.
  }
  return `${prefix}${encodeURIComponent(slug)}`;
}
