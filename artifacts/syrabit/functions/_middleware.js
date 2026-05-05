// Pages Functions middleware — gates SSR on `SSR_ENABLED` and proxies
// the SEO route families to the backend `/api/seo/html/...` handlers.
// Unmapped paths fall through to the SPA. See RUNBOOK §"Task #386:
// SSR route families" for the full mapping table.

const PASSTHROUGH_PREFIXES = [
  '/api/', '/admin/', '/auth/', '/assets/', '/icons/', '/fonts/',
  '/sitemap', '/feed', '/rss', '/llms', '/robots.txt', '/manifest.json',
  '/sw.js', '/.well-known/',
];

// Boards we know about — keeps the mapper from accidentally rewriting
// random three-segment paths (e.g. /chat/new/x) into a backend lookup.
const KNOWN_BOARDS = new Set([
  'seba', 'ahsec', 'cbse', 'icse', 'nios', 'gauhati-university',
  'dibrugarh-university', 'assam-university', 'cotton-university',
  'tezpur-university', 'degree',
]);

// Page-type slugs the backend recognises as the trailing segment of
// the typed-topic SSR route.
const KNOWN_PAGE_TYPES = new Set([
  'notes', 'mcqs', 'pyq', 'qa', 'summary', 'flashcards', 'glossary',
  'short-questions', 'long-questions',
]);

/**
 * Translate a frontend URL pathname into the matching backend SSR
 * endpoint, or return null if no SSR route applies. The returned
 * object also tracks the route family so we can attribute fallbacks
 * in the response header. Pure function — easy to unit test.
 */
export function mapSsrRoute(pathname) {
  if (!pathname || pathname === '/') {
    return { backend: '/api/seo/html/homepage', family: 'homepage' };
  }
  if (pathname === '/about') {
    return { backend: '/api/seo/html/about', family: 'about' };
  }

  // Strip Assamese prefix and remember it as a query param.
  let lang = '';
  let p = pathname;
  if (p.startsWith('/as/')) {
    lang = 'as';
    p = p.slice(3); // keep the leading slash from "/as/..."
  } else if (p === '/as') {
    lang = 'as';
    p = '/';
  }

  const segs = p.split('/').filter(Boolean);

  // /pyq/<board>/<class>/<subject> → subject family with page_type=pyq.
  if (segs[0] === 'pyq' && segs.length === 4 && KNOWN_BOARDS.has(segs[1])) {
    const [, board, cls, subject] = segs;
    const qs = new URLSearchParams({ page_type: 'pyq' });
    if (lang) qs.set('lang', lang);
    return {
      backend: `/api/seo/html/subject/${board}/${cls}/${subject}?${qs.toString()}`,
      family: 'pyq',
    };
  }

  // /pyq/<year>/<paper> → year+paper landing (e.g. /pyq/2024/major).
  // Year is 4-digit; paper is one of major/minor/model/supplementary.
  // The backend renders an index of every PYQ paper that matches.
  if (
    segs[0] === 'pyq' && segs.length === 3 &&
    /^[12]\d{3}$/.test(segs[1]) &&
    /^[a-z][a-z0-9-]{1,32}$/.test(segs[2])
  ) {
    const qs = lang ? `?lang=${lang}` : '';
    return {
      backend: `/api/seo/html/pyq/${segs[1]}/${segs[2]}${qs}`,
      family: 'pyq_year_paper',
    };
  }

  // /<board>/<class>/<subject>(/<topic>(/<page_type>)?)? — the canonical
  // SSR families. Reject anything that doesn't start with a known board.
  if (segs.length >= 3 && KNOWN_BOARDS.has(segs[0])) {
    const [board, cls, subject, topic, pageType] = segs;
    const qs = lang ? `?lang=${lang}` : '';
    if (segs.length === 3) {
      return {
        backend: `/api/seo/html/subject/${board}/${cls}/${subject}${qs}`,
        family: 'subject',
      };
    }
    if (segs.length === 4) {
      return {
        backend: `/api/seo/html/${board}/${cls}/${subject}/${topic}${qs}`,
        family: 'topic',
      };
    }
    // /<board>/<class>/<subject>/chapter/<chapter_slug> — board-scoped
    // chapter landing. The trailing two segments use the literal
    // "chapter" tag so it's unambiguous against the typed-topic shape.
    if (segs.length === 5 && segs[3] === 'chapter') {
      return {
        backend: `/api/seo/html/${board}/${cls}/${subject}/chapter/${segs[4]}${qs}`,
        family: 'chapter',
      };
    }
    if (segs.length === 5 && KNOWN_PAGE_TYPES.has(pageType)) {
      return {
        backend: `/api/seo/html/${board}/${cls}/${subject}/${topic}/${pageType}${qs}`,
        family: 'topic_typed',
      };
    }
  }

  // Slug-only families — backend resolves slug to the canonical chain.
  if ((segs[0] === 'topic' || segs[0] === 'chapter' || segs[0] === 'subject') && segs.length === 2) {
    const family = `${segs[0]}_slug`;
    const qs = lang ? `?lang=${lang}` : '';
    return {
      backend: `/api/seo/html/${segs[0]}/${segs[1]}${qs}`,
      family,
    };
  }

  return null;
}

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);

  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return next();
  }

  for (const p of PASSTHROUGH_PREFIXES) {
    if (url.pathname.startsWith(p)) {
      return next();
    }
  }

  const ssrEnabled = String(env.SSR_ENABLED || '0').toLowerCase();
  if (ssrEnabled !== '1' && ssrEnabled !== 'true' && ssrEnabled !== 'on' && ssrEnabled !== 'yes') {
    return next();
  }

  const route = mapSsrRoute(url.pathname);
  if (!route) {
    // Path didn't match any known SSR family — defer to SPA. Tag the
    // response so the SSR health panel can attribute the miss.
    const r = await next();
    try {
      r.headers.set('X-SSR-Fallback-Family', 'unmapped');
    } catch (_) { /* immutable Response from upstream — non-fatal */ }
    return r;
  }

  const backend = (env.BACKEND_BOT_URL || env.BACKEND_URL || 'https://api.syrabit.ai').replace(/\/+$/, '');
  // route.backend already begins with /api/seo/html/... and may carry
  // its own query string (e.g. ?page_type=pyq). Append the original
  // request's search string only when the backend URL doesn't already
  // contain one, so we don't double up on `?`.
  const sep = route.backend.includes('?') ? '&' : '?';
  const incomingQs = url.search.replace(/^\?/, '');
  const targetUrl = `${backend}${route.backend}${incomingQs ? sep + incomingQs : ''}`;

  let upstream;
  try {
    upstream = await fetch(targetUrl, {
      method: 'GET',
      headers: {
        'Accept': 'text/html,application/xhtml+xml',
        'User-Agent': request.headers.get('user-agent') || 'syrabit-pages-ssr/1.0',
        'X-SSR-Source': 'pages-functions',
        'X-SSR-Family': route.family,
        'X-Forwarded-For': request.headers.get('cf-connecting-ip') || '',
        'CF-IPCountry': request.headers.get('cf-ipcountry') || '',
      },
      redirect: 'follow',
    });
  } catch (err) {
    // SSR is best-effort — never break the page on backend hiccup.
    const r = await next();
    try {
      r.headers.set('X-SSR-Fallback-Family', `error:${route.family}`);
    } catch (_) { /* non-fatal */ }
    return r;
  }

  if (!upstream.ok) {
    const r = await next();
    try {
      r.headers.set('X-SSR-Fallback-Family', `${upstream.status}:${route.family}`);
    } catch (_) { /* non-fatal */ }
    return r;
  }

  const ct = upstream.headers.get('content-type') || '';
  if (!ct.includes('text/html')) {
    const r = await next();
    try {
      r.headers.set('X-SSR-Fallback-Family', `non-html:${route.family}`);
    } catch (_) { /* non-fatal */ }
    return r;
  }

  const headers = new Headers(upstream.headers);
  headers.set('X-SSR-Rendered', 'pages-functions');
  headers.set('X-SSR-Family', route.family);
  // Cache rendered HTML at the edge for 5 min. Cache-Tag lets the
  // backend purge by entity (subject / chapter) when content updates.
  if (!headers.has('Cache-Control')) {
    headers.set('Cache-Control', 'public, max-age=300, s-maxage=300');
  }
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}
