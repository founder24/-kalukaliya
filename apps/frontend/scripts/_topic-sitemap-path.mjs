export function decodeSitemapPath(pathname) {
  try {
    return decodeURIComponent(pathname);
  } catch {
    return pathname;
  }
}

export function canonicalTopicSitemapPath(pathname) {
  const marker = "/topic/";
  const markerIndex = pathname.lastIndexOf(marker);
  if (markerIndex < 0) return pathname;

  const prefix = pathname.slice(0, markerIndex + marker.length);
  const slug = decodeSitemapPath(pathname.slice(markerIndex + marker.length));
  return `${prefix}${encodeURIComponent(slug)}`;
}
