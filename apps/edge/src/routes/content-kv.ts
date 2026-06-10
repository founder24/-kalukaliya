/**
 * CONTENT_KV handler — serves pre-rendered SEO HTML to bot crawlers.
 *
 * The backend content pipeline pushes fully-rendered HTML for 5 page types
 * (notes, mcqs, summary, definitions, important-questions) to CONTENT_KV
 * with keys: {board}/{class_level}/{subject}/{chapter}/{page_type}
 *
 * URL patterns supported:
 *   /{board}/{classSlug}/{subjectSlug}/{chapterSlug}          (4 segments)
 *   /{board}/{classSlug}/{streamSlug}/{subjectSlug}/{chapterSlug} (5 segments — stream ignored in key)
 *   /as/{board}/{classSlug}/{subjectSlug}/{chapterSlug}        (Assamese mirror)
 *
 * Only responds for known bot user agents. Returns null for human traffic.
 */

const BOT_UA_RE =
  /googlebot|bingbot|gptbot|claudebot|perplexitybot|applebot|yandex|baidu|facebookexternalhit|twitterbot|linkedinbot|whatsapp|slackbot|discordbot|telegrambot|pinterestbot/i;

const VALID_SLUG_RE = /^[a-z0-9][a-z0-9-]*$/;

const VALID_PAGE_TYPES = new Set([
  'notes',
  'mcqs',
  'summary',
  'definitions',
  'important-questions',
]);

/**
 * Try to resolve a CONTENT_KV key from the request URL and return cached HTML.
 * Returns null if the request is not a bot, not a chapter path, or KV misses.
 */
export async function handleContentKV(
  request: Request,
  env: Env,
): Promise<Response | null> {
  if (!env.CONTENT_KV) return null;

  const ua = request.headers.get('User-Agent') || '';
  if (!BOT_UA_RE.test(ua)) return null;

  const url = new URL(request.url);
  let pathname = url.pathname;

  // Strip trailing slash
  if (pathname.endsWith('/') && pathname.length > 1) {
    pathname = pathname.slice(0, -1);
  }

  // Strip /as prefix (Assamese mirror routes)
  const isAs = pathname.startsWith('/as/');
  if (isAs) {
    pathname = pathname.slice(3); // '/as/...' → '/...'
  }

  // Split into segments
  const segments = pathname.slice(1).split('/'); // remove leading '/'

  // Determine page type from ?page= query param (default: 'notes')
  let pageType = url.searchParams.get('page') || 'notes';
  if (!VALID_PAGE_TYPES.has(pageType)) pageType = 'notes';

  let board: string, classSlug: string, subjectSlug: string, chapterSlug: string;

  if (segments.length === 4) {
    [board, classSlug, subjectSlug, chapterSlug] = segments;
  } else if (segments.length === 5) {
    // 5th segment: board/class/stream/subject/chapter — drop stream (index 2)
    [board, classSlug, , subjectSlug, chapterSlug] = segments;
  } else {
    return null;
  }

  // Validate all slugs look like slugs (not file extensions, numbers-only, etc.)
  if (
    !VALID_SLUG_RE.test(board) ||
    !VALID_SLUG_RE.test(classSlug) ||
    !VALID_SLUG_RE.test(subjectSlug) ||
    !VALID_SLUG_RE.test(chapterSlug)
  ) {
    return null;
  }

  const kvKey = `${board}/${classSlug}/${subjectSlug}/${chapterSlug}/${pageType}`;

  try {
    const cached = await env.CONTENT_KV.get(kvKey);
    if (!cached) return null;

    return new Response(cached, {
      status: 200,
      headers: {
        'Content-Type': 'text/html; charset=utf-8',
        'X-Content-KV': 'HIT',
        'X-Content-KV-Key': kvKey,
        'Cache-Control': 'public, max-age=3600, stale-while-revalidate=86400',
      },
    });
  } catch {
    return null;
  }
}
