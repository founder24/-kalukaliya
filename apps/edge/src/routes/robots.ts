/**
 * Robots.txt handler for the edge worker.
 *
 * Returns a robots.txt that allows major search engines and AI crawlers
 * while blocking known scrapers that don't respect content licensing.
 */

const ROBOTS_TXT = `User-agent: *
Allow: /

User-agent: GPTBot
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: Applebot-Extended
Allow: /

User-agent: CCBot
Disallow: /

User-agent: Bytespider
Disallow: /

Sitemap: https://syrabit.ai/sitemap-index.xml
Sitemap: https://syrabit.ai/sitemap-subjects.xml
Sitemap: https://syrabit.ai/sitemap-chapters.xml
Sitemap: https://syrabit.ai/sitemap.xml
`;

export function handleRobots(env: Env): Response {
  return new Response(ROBOTS_TXT, {
    status: 200,
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'public, max-age=86400',
    },
  });
}
