import { memo, useMemo, useState } from 'react';
import { ExternalLink, Loader2, AlertTriangle, Globe2, HelpCircle } from 'lucide-react';
import { ReadAloudButton } from '@/components/study/ReadAloudButton';
import { QuizModal } from '@/components/study/QuizModal';

const ERROR_HINTS = {
  not_allowlisted: {
    title: 'This site isn\'t on the approved list yet',
    body: 'Syrabit\'s reader only opens trusted educational sites. Try an NCERT, Wikipedia, Khan Academy, or AHSEC page — or ask an admin to approve this domain.',
  },
  not_allowed: {
    title: 'This site isn\'t on the approved list yet',
    body: 'Syrabit\'s reader only opens trusted educational sites. Try an NCERT, Wikipedia, Khan Academy, or AHSEC page.',
  },
  redirect_not_allowed: {
    title: 'This link redirected to a site that isn\'t approved',
    body: 'The page tried to bounce us to a domain outside the educational allowlist.',
  },
  robots_disallow: {
    title: 'This site asks not to be crawled',
    body: 'Its robots.txt disallows automated fetches for this path. Open the original link in a new tab instead.',
  },
  timeout: {
    title: 'The page took too long to load',
    body: 'Try again in a moment, or pick a different URL.',
  },
  private_ip: {
    title: 'That URL points to a private address',
    body: 'The reader only fetches public educational pages.',
  },
  too_large: {
    title: 'This page is too large to read here',
    body: 'The reader caps fetches at 2 MB. Pick a more focused article.',
  },
  empty_content: {
    title: 'We couldn\'t extract readable content',
    body: 'The page may be behind a login or rendered entirely by JavaScript. Try a different source.',
  },
};

function _formatText(text) {
  if (!text) return [];
  // Split on blank lines to preserve paragraph structure from the reader.
  return text.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
}

export const ReaderPane = memo(function ReaderPane({ article, loading, error }) {
  const [quizOpen, setQuizOpen] = useState(false);
  const paragraphs = useMemo(() => _formatText(article?.text), [article?.text]);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[320px] p-8 text-center">
        <Loader2 className="w-6 h-6 text-primary animate-spin mb-3" aria-hidden="true" />
        <div className="text-sm text-muted-foreground">Fetching and cleaning the page…</div>
      </div>
    );
  }

  if (error) {
    // Try multiple fields — backend shapes vary: some return {error:"..."}, some
    // put the code in {detail:"..."}, and some gateways strip the body on 4xx.
    // As a last resort, fall back on HTTP status so 403 still shows allowlist copy.
    const code =
      error.error ||
      error.code ||
      error.detail ||
      (error.status === 403 ? 'not_allowed' : null) ||
      'fetch_failed';
    const hint = ERROR_HINTS[code] || {
      title: 'Couldn\'t load this page',
      body: error.detail || error.message || 'Something went wrong fetching this URL. Please try another.',
    };
    return (
      <div className="p-6 md:p-8">
        <div
          className="flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-amber-900"
          role="alert"
        >
          <AlertTriangle className="w-5 h-5 flex-none mt-0.5" aria-hidden="true" />
          <div className="min-w-0">
            <div className="font-semibold text-sm">{hint.title}</div>
            <div className="text-sm mt-1">{hint.body}</div>
            {error.url && (
              <a
                href={error.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-sm font-medium mt-2 hover:underline"
              >
                Open original <ExternalLink className="w-3.5 h-3.5" aria-hidden="true" />
              </a>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (!article) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[320px] p-8 text-center text-muted-foreground">
        <Globe2 className="w-10 h-10 mb-3 text-muted-foreground/50" aria-hidden="true" />
        <div className="text-base font-medium text-foreground mb-1">Paste an educational URL to read it here</div>
        <div className="text-sm max-w-md">
          We clean the clutter, keep the text, and let you ask Syra about the page — all without leaving Syrabit.
        </div>
      </div>
    );
  }

  return (
    <article className="p-4 md:p-6 lg:p-8 max-w-3xl mx-auto">
      <header className="mb-5 pb-4 border-b border-border/60">
        <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
          {article.domain && (
            <span className="inline-flex items-center gap-1">
              <Globe2 className="w-3.5 h-3.5" aria-hidden="true" />
              {article.domain}
            </span>
          )}
          {article.language && article.language !== 'en' && (
            <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide">
              {article.language}
            </span>
          )}
          {article.word_count ? <span>· {article.word_count.toLocaleString()} words</span> : null}
        </div>
        <h1 className="text-xl md:text-2xl font-bold text-foreground leading-tight">
          {article.title || 'Untitled page'}
        </h1>
        {article.byline && (
          <div className="text-sm text-muted-foreground mt-1">{article.byline}</div>
        )}
        {article.url && (
          <a
            href={article.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline mt-2"
          >
            View original <ExternalLink className="w-3 h-3" aria-hidden="true" />
          </a>
        )}
      </header>

      {article.lead_image && (
        <img
          src={article.lead_image}
          alt=""
          className="w-full rounded-lg mb-5 max-h-80 object-cover"
          loading="lazy"
          referrerPolicy="no-referrer"
        />
      )}

      <div className="flex items-center gap-2 mb-3">
        <ReadAloudButton
          id={`reader-${article.url || 'page'}`}
          text={paragraphs.join('\n\n')}
          label="Read aloud"
        />
        <button
          onClick={() => setQuizOpen(true)}
          className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Quiz me on this article"
        >
          <HelpCircle className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">Quiz me</span>
        </button>
      </div>

      <div data-savable="true" className="prose prose-sm md:prose-base max-w-none text-foreground/90 leading-relaxed">
        {paragraphs.length > 0 ? (
          paragraphs.map((p, i) => (
            <p key={i} className="mb-4 whitespace-pre-wrap">{p}</p>
          ))
        ) : (
          <p className="text-muted-foreground italic">No readable text extracted.</p>
        )}
      </div>

      <QuizModal
        open={quizOpen} onClose={() => setQuizOpen(false)}
        context={paragraphs.slice(0, 12).join('\n\n')}
        topic={article.title || article.domain || 'this article'}
        count={6}
      />
    </article>
  );
});
