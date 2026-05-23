import { useMemo, useCallback, memo, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useStrictMode, useEduAllowlist } from '@/hooks/useStrictMode';

const remarkPlugins = [remarkGfm];

function _hostOf(href) {
  try { return new URL(href, window.location.origin).hostname; } catch { return ''; }
}

export const MarkdownContent = memo(function MarkdownContent({ content, streaming, sources, onHiddenLinks }) {
  const navigate = useNavigate();
  const { strict } = useStrictMode();
  const isHostAllowed = useEduAllowlist(strict);

  const handleInternalClick = useCallback((href) => {
    navigate(href);
  }, [navigate]);

  const components = useMemo(() => ({
    a: ({ href, children }) => {
      if (!href) return <span>{children}</span>;
      const isExternal = /^(https?:)?\/\/|^mailto:|^tel:/i.test(href);
      if (isExternal) {
        if (strict && /^(https?:)?\/\//i.test(href)) {
          const host = _hostOf(href);
          if (!isHostAllowed(host)) {
            return (
              <span
                className="inline-source-link inline-source-link--blocked"
                title={`Strict Mode hid this link to ${host || 'an external site'} because it isn't on the curated allowlist.`}
                aria-label={`Link hidden by Strict Mode (${host || 'external site'})`}
              >
                <span aria-hidden="true" style={{ marginRight: 3 }}>🔒</span>
                <span>{children}</span>
                <span style={{ marginLeft: 4, opacity: 0.75, fontSize: '0.85em' }}>
                  (hidden · Strict Mode)
                </span>
              </span>
            );
          }
        }
        return <a href={href} target="_blank" rel="noopener noreferrer" className="inline-source-link">{children}</a>;
      }
      return (
        <button onClick={() => handleInternalClick(href)} className="inline-source-link">
          {children}
        </button>
      );
    },
  }), [handleInternalClick, strict, isHostAllowed]);

  const processed = useMemo(() => {
    if (!content) return content;
    if (streaming) return content;
    // Unicode-aware normalization so non-Latin scripts (Assamese,
    // Bengali, Hindi, etc.) match correctly. The previous implementation
    // stripped everything outside `[a-z0-9 ]`, which collapsed any
    // Assamese title (e.g. "প্ৰাণী জগত") to a single space — meaning
    // every non-Latin source collided on the same key and inline
    // `[PAGE: ...]` citations silently degraded to bold text in
    // Assamese answers. We keep Unicode letters (`\p{L}`) and numbers
    // (`\p{N}`) with the `u` flag, and use `.normalize('NFC')` so the
    // same visual character composed two different ways still matches.
    const normalize = (s) => {
      try {
        return (s || '')
          .normalize('NFC')
          .trim()
          .toLowerCase()
          .replace(/[\s\-_]+/g, ' ')
          .replace(/[^\p{L}\p{M}\p{N} ]/gu, '');
      } catch {
        return (s || '').trim().toLowerCase().replace(/[\s\-_]+/g, ' ').replace(/[^a-z0-9 ]/g, '');
      }
    };
    const toSlug = (s) => normalize(s).replace(/\s+/g, '-');
    const urlMap = new Map();
    for (const s of (sources || [])) {
      const url = s.url || '';
      if (s.title) {
        urlMap.set(normalize(s.title), url);
        urlMap.set(toSlug(s.title), url);
      }
      if (s.slug) {
        urlMap.set(normalize(s.slug), url || `/learn/${s.slug}`);
        urlMap.set(toSlug(s.slug), url || `/learn/${s.slug}`);
      }
    }
    const findUrl = (raw) => {
      const norm = normalize(raw);
      const slug = toSlug(raw);
      if (urlMap.has(norm)) return urlMap.get(norm);
      if (urlMap.has(slug)) return urlMap.get(slug);
      if (norm.length >= 8) {
        for (const [k, v] of urlMap) {
          if (k.length >= 8 && (k.includes(norm) || norm.includes(k))) return v;
        }
      }
      return '';
    };
    // Append `?topic=<title>` to inline-citation links so the
    // ChapterPage's existing topic-highlight pipeline (200ms scroll +
    // 5s green flash, see ChapterPage.jsx ~line 637) fires when the
    // student clicks an inline citation.  Skip the param for content
    // cards (`/learn/...`) — they don't honour `?topic=`.
    return content.replace(/\[(PAGE|CHAPTER|TOPIC|LESSON|SECTION):\s*([^\]]+)\]/gi, (_, _type, rawTitle) => {
      const title = rawTitle.trim();
      const url = findUrl(title);
      if (!url) return `**${title}**`;
      const isLearn = url.startsWith('/learn/');
      const sep = url.includes('?') ? '&' : '?';
      const finalUrl = isLearn ? url : `${url}${sep}topic=${encodeURIComponent(title)}`;
      return `[${title}](${finalUrl})`;
    });
  }, [content, sources, streaming]);

  // Hidden-link extraction for the guardian "review hidden links"
  // affordance. We derive the list deterministically from `processed`
  // (instead of mutating a ref during ReactMarkdown render) so the
  // value is commit-stable under React 19 concurrent rendering and
  // does not depend on ReactMarkdown's renderer firing in any
  // particular order.
  //
  // Grouping is by host because the downstream "Request site"
  // action is host-level — one row per host is what the user
  // actually has to act on. We keep the first href/text seen so
  // the row can show a representative link label.
  const hiddenLinks = useMemo(() => {
    if (!strict || !processed || streaming) return [];
    const out = new Map(); // host -> {host, href, text}
    const consider = (href, text) => {
      if (!href) return;
      if (!/^https?:\/\//i.test(href)) return;
      const host = (_hostOf(href) || '').toLowerCase();
      if (!host || isHostAllowed(host)) return;
      if (!out.has(host)) out.set(host, { host, href, text: text || href });
    };
    // [text](url) — the dominant case for AI-generated answers.
    const md = /\[([^\]]+?)\]\((https?:\/\/[^\s)]+)\)/g;
    let m;
    while ((m = md.exec(processed)) !== null) consider(m[2], m[1]);
    // Bare https URLs (remark-gfm autolinks them too).
    const bare = /(?<![("\w])(https?:\/\/[^\s)<>"']+)/g;
    while ((m = bare.exec(processed)) !== null) consider(m[1], m[1]);
    return Array.from(out.values());
  }, [processed, streaming, strict, isHostAllowed]);

  useEffect(() => {
    if (!onHiddenLinks) return;
    onHiddenLinks(hiddenLinks);
  }, [hiddenLinks, onHiddenLinks]);

  return (
    <div className="md-content-light" style={{ fontSize: '0.9375rem' }}>
      <ReactMarkdown remarkPlugins={remarkPlugins} components={components}>
        {processed}
      </ReactMarkdown>
      {streaming && (
        <span
          className="inline-block rounded-full align-middle"
          style={{ width: 2, height: '1em', marginLeft: 2, background: 'hsl(var(--primary))' }}
        />
      )}
    </div>
  );
});
