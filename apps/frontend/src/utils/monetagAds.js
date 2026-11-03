export const MONETAG_ZONES = Object.freeze({
  onclick: {
    zoneId: '12180636',
    scriptUrl: 'https://al5sm.com/tag.min.js',
  },
  inPagePush: {
    zoneId: '12180642',
    scriptUrl: 'https://nap5k.com/tag.min.js',
  },
});

const MONETAG_MANAGED_PATHS = Object.freeze([
  '/learn/:slug',
  '/pyq/:slug',
  '/:board/:classSlug/:subjectSlug',
  '/:board/:classSlug/:subjectSlug/:chapterSlug',
  '/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug',
  '/:board/:classSlug/:subjectSlug/:chapterSlug/topic/:topicSlug',
  '/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug/topic/:topicSlug',
  '/as/:board/:classSlug/:subjectSlug/:chapterSlug',
  '/as/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug',
  '/as/:board/:classSlug/:subjectSlug/:chapterSlug/topic/:topicSlug',
  '/as/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug/topic/:topicSlug',
]);

export const MONETAG_REPLACED_AD_PLACEMENTS = new Set([
  'pyq.topOfContent',
  'pyq.inContent',
  'pyq.betweenImages',
  'pyq.endOfContent',
  'learn.topOfContent',
  'learn.inContent',
  'learn.afterPyqs',
  'learn.afterFlashcards',
  'learn.endOfContent',
  'learn.sidebar',
  'learn.afterQuestion',
  'chapter.notes.top',
  'chapter.notes.inContent',
  'chapter.notes.end',
  'chapter.qa.inContent',
  'chapter.qa.end',
  'chapter.sidebar',
  'chapter.pyq.top',
  'chapter.pyq.inContent',
]);

export function isMonetagManagedPath(pathname) {
  if (typeof pathname !== 'string') return false;
  // Keep this aligned with the public learning routes in App.jsx. Exact
  // matching prevents Monetag from being treated as active on /chat,
  // /library, staff, account, or unrelated pages.
  return MONETAG_MANAGED_PATHS.some((path) => {
    const pattern = path
      .split('/')
      .filter(Boolean)
      .map((segment) => {
        if (segment.startsWith(':')) return '[^/]+';
        return segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      })
      .join('/');
    return new RegExp(`^/${pattern}/?$`).test(pathname);
  });
}

export function shouldForceMonetagDocumentNavigation(fromPath, toPath) {
  return isMonetagManagedPath(fromPath) && !isMonetagManagedPath(toPath);
}

export function injectMonetagZones({
  onclick = true,
  inPagePush = true,
  doc = typeof document === 'undefined' ? null : document,
} = {}) {
  if (!doc) return;
  const requestedZones = [
    ...(onclick ? [MONETAG_ZONES.onclick] : []),
    ...(inPagePush ? [MONETAG_ZONES.inPagePush] : []),
  ];

  for (const zone of requestedZones) {
    const exists = [...doc.scripts].some(
      (script) => script.dataset.zone === zone.zoneId,
    );
    if (exists) continue;

    const script = doc.createElement('script');
    script.dataset.zone = zone.zoneId;
    script.setAttribute('data-cfasync', 'false');
    script.addEventListener('load', () => {
      script.dataset.loaded = 'true';
    }, { once: true });
    script.addEventListener('error', () => {
      script.dataset.loadError = 'true';
    }, { once: true });
    script.async = true;
    script.src = zone.scriptUrl;
    doc.head.appendChild(script);
  }
}
