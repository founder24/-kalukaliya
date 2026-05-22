import { useEffect } from 'react';
import { globalSiteSchema } from '@/lib/jsonld';

/**
 * Task #336: site-wide structured data injected once at the App root.
 * Emits Organization + LocalBusiness (Guwahati) + a WebPage stub so AI
 * crawlers (ChatGPT, Gemini, Perplexity, Bingbot/Copilot) can resolve
 * the publisher entity on every route — including pages that don't pass
 * a `pageType` to PageMeta. Per-page PageMeta blocks add more specific
 * graphs on top; nothing here gets duplicated because each node uses
 * a stable `@id`.
 *
 * Renders nothing into the React tree — injects directly into
 * document.head via useEffect. Returning real <script> tags from a
 * component triggers React 19 hydration mismatch (#418) on every
 * prerendered page; managing the head DOM imperatively avoids that.
 */
export default function GlobalSeo() {
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const schema = globalSiteSchema('https://syrabit.ai/');
    // De-dupe across re-renders / route changes via a stable marker attr.
    document.head
      .querySelectorAll('script[type="application/ld+json"][data-globalseo]')
      .forEach((el) => el.remove());
    const s = document.createElement('script');
    s.type = 'application/ld+json';
    s.setAttribute('data-globalseo', '1');
    s.textContent = JSON.stringify(schema);
    document.head.appendChild(s);
  }, []);

  return null;
}
