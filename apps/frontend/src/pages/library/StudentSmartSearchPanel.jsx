import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getStudentSearchStatus, searchStudentContent } from '@/utils/api';

const copy = {
  en: {
    toggle: 'Smart search',
    intro: 'Search study notes by concept, not only by title.',
    placeholder: 'Try “why does acceleration change velocity?”',
    search: 'Search notes',
    searching: 'Searching…',
    loading: 'Loading smart search…',
    empty: 'No matching published notes. Try another phrase or remove a filter.',
    error: 'Smart search is temporarily unavailable. Your regular library search still works.',
    rateLimited: 'Too many searches. Wait a minute and try again.',
    results: 'Results',
  },
  as: {
    toggle: 'স্মাৰ্ট সন্ধান',
    intro: 'কেৱল শিৰোনাম নহয়, ধাৰণা অনুসৰি পঢ়াৰ টোকা বিচাৰক।',
    placeholder: 'যেনে: ত্বৰণে বেগ কিয় সলনি কৰে?',
    search: 'টোকা বিচাৰক',
    searching: 'বিচৰা হৈছে…',
    loading: 'স্মাৰ্ট সন্ধান লোড হৈছে…',
    empty: 'মিল থকা প্ৰকাশিত টোকা পোৱা নগ’ল। বেলেগ শব্দ বা ফিল্টাৰ চেষ্টা কৰক।',
    error: 'স্মাৰ্ট সন্ধান এই মুহূৰ্তত উপলব্ধ নহয়। সাধাৰণ লাইব্ৰেৰী সন্ধান চলি থাকিব।',
    rateLimited: 'বহুত সন্ধান কৰা হৈছে। এক মিনিট অপেক্ষা কৰি পুনৰ চেষ্টা কৰক।',
    results: 'ফলাফল',
  },
};

export default function StudentSmartSearchPanel({
  language = 'en',
  scope = {},
  hidden = false,
}) {
  const t = copy[language] || copy.en;
  const clientFeatureEnabled = import.meta.env.VITE_STUDENT_SEARCH_UI_ENABLED === 'true';
  const [enabled, setEnabled] = useState(false);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [submittedQuery, setSubmittedQuery] = useState('');
  const [results, setResults] = useState([]);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!clientFeatureEnabled || hidden) return undefined;
    let active = true;
    getStudentSearchStatus()
      .then((status) => {
        if (active) setEnabled(status?.enabled === true);
      })
      .catch(() => {
        if (active) setEnabled(false);
      });
    return () => { active = false; };
  }, [clientFeatureEnabled, hidden]);

  useEffect(() => {
    setResults([]);
    setMessage('');
    setSubmittedQuery('');
  }, [scope.board_slug, scope.class_slug, scope.stream_slug, language]);

  if (!enabled || hidden) return null;

  async function handleSubmit(event) {
    event.preventDefault();
    const normalizedQuery = query.normalize('NFC').trim();
    if (normalizedQuery.length < 2) {
      setMessage(language === 'as' ? 'কমেও দুটা আখৰ লিখক।' : 'Enter at least two characters.');
      setResults([]);
      return;
    }
    setLoading(true);
    setMessage('');
    setSubmittedQuery(normalizedQuery);
    try {
      const payload = await searchStudentContent({
        query: normalizedQuery,
        language,
        ...scope,
      });
      setResults(Array.isArray(payload?.results) ? payload.results : []);
      if (!payload?.results?.length) setMessage(t.empty);
    } catch (error) {
      const status = error?.response?.status;
      setMessage(status === 429 ? t.rateLimited : t.error);
      setResults([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section
      className="mb-4 overflow-hidden rounded-xl border border-violet-200/70 bg-violet-50/40 dark:border-violet-900/60 dark:bg-violet-950/20"
      aria-label={t.toggle}
      data-testid="student-smart-search-panel"
    >
      <button
        type="button"
        onClick={() => setOpen(value => !value)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        aria-expanded={open}
        aria-controls="student-smart-search-content"
        data-testid="button-toggle-student-smart-search"
      >
        <span>
          <span className="block text-sm font-semibold text-foreground">{t.toggle}</span>
          <span className="mt-0.5 block text-xs text-muted-foreground">{t.intro}</span>
        </span>
        <span aria-hidden="true" className="shrink-0 text-violet-600">
          {open ? '−' : '+'}
        </span>
      </button>

      {open && (
        <div id="student-smart-search-content" className="border-t border-violet-200/70 px-4 py-3 dark:border-violet-900/60">
          <form onSubmit={handleSubmit} className="flex flex-col gap-2 sm:flex-row">
            <label className="sr-only" htmlFor="student-smart-search-query">{t.toggle}</label>
            <input
              id="student-smart-search-query"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              maxLength={200}
              placeholder={t.placeholder}
              disabled={loading}
              className="h-10 min-w-0 flex-1 rounded-lg border border-border bg-background px-3 text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/30"
              data-testid="input-student-smart-search"
            />
            <button
              type="submit"
              disabled={loading || query.trim().length < 2}
              className="h-10 rounded-lg bg-violet-600 px-4 text-sm font-semibold text-white transition-colors hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-55"
              data-testid="button-submit-student-smart-search"
            >
              {loading ? t.searching : t.search}
            </button>
          </form>

          {loading && <p className="mt-3 text-sm text-muted-foreground" role="status">{t.loading}</p>}
          {message && !loading && (
            <p className="mt-3 text-sm text-muted-foreground" role="status" data-testid="status-student-smart-search">
              {message}
            </p>
          )}

          {!loading && results.length > 0 && (
            <div className="mt-3" aria-live="polite">
              <p className="mb-2 text-xs font-medium text-muted-foreground">
                {t.results} {submittedQuery ? `— “${submittedQuery}”` : ''}
              </p>
              <ul className="space-y-2" data-testid="list-student-smart-search-results">
                {results.map((result) => (
                  <li
                    key={`${result.chapter_id}-${result.url}`}
                    className="rounded-lg border border-border bg-background/80 p-3"
                    data-testid={`result-student-search-${result.chapter_id}`}
                  >
                    <Link
                      to={result.url}
                      className="text-sm font-semibold text-primary hover:underline"
                      data-testid={`link-student-search-${result.chapter_id}`}
                    >
                      {result.title}
                    </Link>
                    <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">
                      {result.snippet}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
