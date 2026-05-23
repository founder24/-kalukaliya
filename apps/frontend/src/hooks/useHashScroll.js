import { useEffect } from 'react';

// Scroll the element matching the current `location.hash` into view once
// it appears in the DOM. Async-loaded pages (chat/chapter) populate
// content after mount, so the browser's default fragment scroll fires
// against an empty page. We retry on a short interval until the node
// exists or we time out.
//
// Usage:
//   useHashScroll(ready);   // ready === content is present
export function useHashScroll(ready) {
  useEffect(() => {
    if (!ready) return undefined;
    const hash = (typeof window !== 'undefined' && window.location.hash) || '';
    if (!hash || hash.length < 2) return undefined;

    let id;
    try {
      id = decodeURIComponent(hash.slice(1));
    } catch {
      id = hash.slice(1);
    }
    if (!id) return undefined;

    let cancelled = false;
    const start = Date.now();
    const MAX_MS = 4000;
    const STEP_MS = 80;

    const tryScroll = () => {
      if (cancelled) return;
      let el = null;
      try {
        // Use getElementById so we don't have to escape CSS-special chars.
        el = document.getElementById(id);
      } catch {
        el = null;
      }
      if (el) {
        try {
          el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        } catch {
          el.scrollIntoView();
        }
        return;
      }
      if (Date.now() - start < MAX_MS) {
        setTimeout(tryScroll, STEP_MS);
      }
    };

    // Defer once so React can paint the freshly-rendered content.
    const t = setTimeout(tryScroll, 0);
    return () => { cancelled = true; clearTimeout(t); };
  }, [ready]);
}
