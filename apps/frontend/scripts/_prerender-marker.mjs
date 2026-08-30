function normalizedPathname(pathname) {
  if (!pathname || pathname === "/") return "/";
  return pathname.replace(/\/+$/, "");
}

function escapeAttribute(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function injectPrerenderPath(html, routePath) {
  const marker =
    `<meta name="syrabit-prerender-path" content="` +
    `${escapeAttribute(normalizedPathname(routePath))}" />`;
  const withoutOldMarker = html.replace(
    /<meta\s+name=["']syrabit-prerender-path["'][^>]*>\s*/gi,
    "",
  );
  return withoutOldMarker.replace(/<\/head>/i, `    ${marker}\n  </head>`);
}