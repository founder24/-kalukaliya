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

# --- Content signals for AI grounding engines ---
# Explicitly grant permission for AI search/answer engines to use site content
# as grounding input for generated answers (GEO/AEO signal).
# search=yes: content is intended for search indexing
# ai-input=yes: content may be used as grounding for AI-generated answers
# ai-train=no: content must NOT be used for model training
# Content-Signal: search=yes, ai-input=yes, ai-train=no

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
