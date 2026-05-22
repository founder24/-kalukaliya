/**
 * BrowsePage — /read
 *
 * In-app educational browser shell. Students paste an allowlisted URL, the
 * backend (routes/edu_browser.py) fetches and cleans it, and a side panel
 * streams a grounded answer with numbered [N] citation chips.
 *
 * Endpoints:
 *   POST /api/edu/reader/fetch     → cleaned article payload
 *   POST /api/edu/grounded-answer  → SSE stream (see AskPanel.jsx)
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Globe2, ArrowRight, Loader2 } from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import { API_BASE, getAnonId } from '@/utils/api';
import { useAuth } from '@/context/AuthContext';
import { ReaderPane } from '@/components/edubrowser/ReaderPane';
import { AskPanel } from '@/components/edubrowser/AskPanel';

const SUGGESTED_URLS = [
  { label: 'Wikipedia: Photosynthesis', url: 'https://en.wikipedia.org/wiki/Photosynthesis' },
  { label: 'Khan Academy: Newton\'s Laws', url: 'https://www.khanacademy.org/science/physics/forces-newtons-laws' },
  { label: 'NCERT Physics Class 12', url: 'https://ncert.nic.in/textbook.php?leph1=0-8' },
  { label: 'Britannica: Mitosis', url: 'https://www.britannica.com/science/mitosis' },
];

function normaliseUrl(raw) {
  const s = (raw || '').trim();
  if (!s) return '';
  if (!/^https?:\/\//i.test(s)) return `https://${s}`;
  return s;
}

export default function BrowsePage() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialUrl = searchParams.get('url') || '';

  const [urlInput, setUrlInput] = useState(initialUrl);
  const [article, setArticle] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  const loadUrl = useCallback(async (rawUrl) => {
    const url = normaliseUrl(rawUrl);
    if (!url) return;
    setLoading(true);
    setError(null);
    setArticle(null);

    if (abortRef.current) {
      try { abortRef.current.abort(); } catch { /* noop */ }
    }
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (!user) headers['x-anon-id'] = getAnonId();
      const resp = await fetch(`${API_BASE}/edu/reader/fetch`, {
        method: 'POST',
        headers,
        credentials: 'include',
        body: JSON.stringify({ url, bypass_cache: false }),
        signal: controller.signal,
      });
      const data = await resp.json().catch(() => ({}));
      // Guard against stale responses: a newer loadUrl may have aborted us.
      if (abortRef.current !== controller) return;
      if (!resp.ok || !data?.ok) {
        setError({ ...data, status: resp.status });
      } else {
        setArticle(data);
        // Sync URL to ?url= so the page is shareable and reload-safe.
        setSearchParams({ url }, { replace: true });
      }
    } catch (err) {
      if (abortRef.current !== controller) return;
      if (err?.name !== 'AbortError') {
        setError({ error: 'network_error', detail: err?.message || 'Network error' });
      }
    } finally {
      // Only clear loading if this controller is still the active one.
      if (abortRef.current === controller) {
        abortRef.current = null;
        setLoading(false);
      }
    }
  }, [user, setSearchParams]);

  // Auto-load on first mount if ?url= is present.
  useEffect(() => {
    if (initialUrl) loadUrl(initialUrl);
    return () => {
      if (abortRef.current) {
        try { abortRef.current.abort(); } catch { /* noop */ }
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSubmit = (e) => {
    e.preventDefault();
    loadUrl(urlInput);
  };

  return (
    <AppLayout pageTitle="Read">
      <title>Read & Ask — Syrabit.ai</title>
      <meta name="description" content="Open a trusted educational page and ask Syra to explain, summarise, or quiz you on it — grounded in real sources with numbered citations." />

      <div className="max-w-6xl mx-auto px-3 md:px-6 py-4 md:py-6">
        {/* ── URL bar ───────────────────────────────────────────────── */}
        <form onSubmit={onSubmit} className="flex items-center gap-2 mb-4">
          <div className="relative flex-1">
            <Globe2 className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" aria-hidden="true" />
            <input
              type="url"
              inputMode="url"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              placeholder="Paste an educational URL (e.g. wikipedia.org/…)"
              aria-label="Educational page URL"
              className="w-full rounded-xl border border-border bg-background pl-9 pr-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/60 min-h-[44px]"
              autoComplete="url"
              spellCheck={false}
            />
          </div>
          <button
            type="submit"
            disabled={loading || !urlInput.trim()}
            className="inline-flex items-center gap-1.5 px-4 py-2.5 rounded-xl bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:opacity-90 min-h-[44px]"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowRight className="w-4 h-4" />}
            <span className="hidden sm:inline">Open</span>
          </button>
        </form>

        {/* ── Suggested quick-starts (only when nothing loaded yet) ─── */}
        {!article && !loading && !error && (
          <div className="mb-4">
            <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
              Try a verified page
            </div>
            <div className="flex flex-wrap gap-2">
              {SUGGESTED_URLS.map((s) => (
                <button
                  key={s.url}
                  type="button"
                  onClick={() => { setUrlInput(s.url); loadUrl(s.url); }}
                  className="text-xs px-3 py-1.5 rounded-full border border-border bg-background hover:bg-muted text-foreground transition-colors"
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ── Split layout: reader (left) + ask (right) ─────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_420px] xl:grid-cols-[1fr_460px] gap-4">
          <div className="rounded-xl border border-border/60 bg-card overflow-hidden min-h-[400px]">
            <ReaderPane article={article} loading={loading} error={error} />
          </div>
          <div className="rounded-xl border border-border/60 overflow-hidden flex flex-col min-h-[400px] lg:sticky lg:top-4 lg:self-start lg:max-h-[calc(100vh-2rem)]">
            <AskPanel
              article={article}
              board={user?.board_name}
              className="flex-1 min-h-0"
            />
          </div>
        </div>
      </div>
    </AppLayout>
  );
}
