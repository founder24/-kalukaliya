/**
 * Custom robots.txt handler
 * Allows major search engines and AI crawlers, blocks known scrapers.
 */

export function handleRobots(env: Env): Response {
  const content = `User-agent: *
Allow: /
Disallow: /api/
Disallow: /admin/
Disallow: /profile

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

Sitemap: https://syrabit.ai/sitemap.xml
`;

  return new Response(content, {
    headers: { 'Content-Type': 'text/plain' },
  });
}
