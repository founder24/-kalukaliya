/**
 * AskPanel — streams /api/edu/grounded-answer SSE for the page the user is reading.
 *
 * Event shapes (from grounded_answer.py):
 *   meta:        { event: "meta", message_id, citations, rag_source, from_cache }
 *   content:     { message_id, content }
 *   syrabit_done: { event: "syrabit_done", message_id, elapsed_ms, citations, ... }
 *   cancelled:   { event: "cancelled", message_id }
 *   error:       { event: "error", error, detail }
 *   safety_break:{ event: "safety_break" }
 *   finally:     data: [DONE]
 */
import { useCallback, useEffect, useMemo, useRef, useState, Fragment } from 'react';
import { Send, StopCircle, Sparkles, AlertTriangle, RotateCcw } from 'lucide-react';
import { API_BASE, getAnonId } from '@/utils/api';
import { useAuth } from '@/context/AuthContext';
import { CitationChip } from './CitationChip';
import { CitationTray } from './CitationTray';

function _genMessageId() {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return 'msg_' + Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join('');
}

function renderAnswerWithChips(text, citationsById) {
  // Split on [N] references so each token can render either as text or a chip.
  if (!text) return null;
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((part, i) => {
    const m = /^\[(\d+)\]$/.exec(part);
    if (m) {
      const id = Number(m[1]);
      const cite = citationsById.get(id);
      if (cite) {
        return (
          <Fragment key={i}>
            <CitationChip citation={cite} compact />
          </Fragment>
        );
      }
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

export function AskPanel({ article, subject, chapter, board, className }) {
  const { user } = useAuth();
  const [query, setQuery] = useState('');
  const [answer, setAnswer] = useState('');
  const [citations, setCitations] = useState([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState(null);
  const [fromCache, setFromCache] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const abortRef = useRef(null);
  const messageIdRef = useRef(null);
  const scrollRef = useRef(null);

  const citationsById = useMemo(() => {
    const m = new Map();
    for (const c of citations || []) {
      // Indexes arrive as numbers from the backend, but coerce defensively so
      // string/number mismatches don't silently drop chips.
      m.set(Number(c.index), c);
    }
    return m;
  }, [citations]);

  // Autoscroll answer area as it streams in.
  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [answer, streaming]);

  // When the user navigates to a new article, reset the last answer so it's
  // not confusing to see an answer about a different page.
  useEffect(() => {
    setAnswer('');
    setCitations([]);
    setError(null);
    setFromCache(false);
    setCancelled(false);
    if (abortRef.current) {
      try { abortRef.current.abort(); } catch { /* noop */ }
      abortRef.current = null;
    }
  }, [article?.url]);

  const handleCancel = useCallback(() => {
    if (abortRef.current) {
      try { abortRef.current.abort(); } catch { /* noop */ }
    }
    setCancelled(true);
    setStreaming(false);
  }, []);

  const sendQuery = useCallback(async (q) => {
    const text = (q ?? query).trim();
    if (!text || streaming) return;
    setAnswer('');
    setCitations([]);
    setError(null);
    setCancelled(false);
    setFromCache(false);
    setStreaming(true);

    // New AbortController for this run; cancel any in-flight.
    if (abortRef.current) {
      try { abortRef.current.abort(); } catch { /* noop */ }
    }
    const controller = new AbortController();
    abortRef.current = controller;
    const mid = _genMessageId();
    messageIdRef.current = mid;

    const payload = {
      query: text,
      page_url: article?.url || '',
      subject_id: subject?.id || '',
      subject_name: subject?.name || '',
      chapter_name: chapter || '',
      board_name: board || user?.board_name || '',
      class_name: user?.class_name || '',
      response_lang: 'en',
      max_tokens: 1024,
      message_id: mid,
    };

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (!user) headers['x-anon-id'] = getAnonId();

      const resp = await fetch(`${API_BASE}/edu/grounded-answer`, {
        method: 'POST',
        headers,
        credentials: 'include',
        body: JSON.stringify(payload),
        signal: controller.signal,
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        if (resp.status === 429) {
          setError({ title: 'Slow down a bit', body: err.detail || 'Rate limit exceeded — try again in a minute.' });
        } else {
          setError({ title: 'Something went wrong', body: err.detail || `Server returned ${resp.status}` });
        }
        setStreaming(false);
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let acc = '';
      let doneStreaming = false;
      while (!doneStreaming) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() || '';
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          if (raw === '[DONE]') { doneStreaming = true; break; }
          let ev;
          try { ev = JSON.parse(raw); } catch { continue; }

          if (ev.event === 'meta') {
            // Server-assigned message_id overrides the client-generated one so
            // cancel/retry paths can reference the authoritative server ID.
            if (ev.message_id) messageIdRef.current = ev.message_id;
            if (Array.isArray(ev.citations)) setCitations(ev.citations);
            if (ev.from_cache) setFromCache(true);
            continue;
          }
          // Harden cancel/retry races: if the server emits a message_id on an
          // event that doesn't match the one we're currently tracking, that
          // chunk belongs to a stale request we already aborted. Drop it.
          if (ev.message_id && messageIdRef.current && ev.message_id !== messageIdRef.current) {
            continue;
          }
          if (ev.event === 'cancelled') {
            setCancelled(true);
            doneStreaming = true;
            break;
          }
          if (ev.event === 'error') {
            setError({ title: 'Answer failed', body: ev.detail || ev.error || 'Try again.' });
            doneStreaming = true;
            break;
          }
          if (ev.event === 'safety_break') {
            setError({ title: 'Answer stopped for safety', body: 'Syra cut the response to stay kid-safe. Try rephrasing your question.' });
            doneStreaming = true;
            break;
          }
          if (ev.event === 'syrabit_done') {
            if (Array.isArray(ev.citations) && ev.citations.length) setCitations(ev.citations);
            continue;
          }
          if (typeof ev.content === 'string') {
            acc += ev.content;
            setAnswer(acc);
          }
        }
      }
      try { reader.cancel(); } catch { /* noop */ }
    } catch (err) {
      if (err?.name === 'AbortError') {
        setCancelled(true);
      } else {
        setError({ title: 'Network error', body: err?.message || 'Could not reach the server.' });
      }
    } finally {
      // Only clear shared state if THIS request is still the active one —
      // otherwise an aborted stale request would clobber a newer in-flight call.
      if (abortRef.current === controller) {
        abortRef.current = null;
        setStreaming(false);
      }
    }
  }, [query, streaming, article?.url, subject, chapter, board, user]);

  const onSubmit = (e) => {
    e.preventDefault();
    sendQuery();
  };

  const canAsk = !!article?.url;
  const showEmpty = !answer && !streaming && !error;
  const suggestions = useMemo(() => [
    'Summarise this page in 5 bullet points',
    'What are the 3 key concepts here?',
    'Make a short quiz from this page',
    'Explain this for an AHSEC student',
  ], []);

  return (
    <section className={`flex flex-col bg-card ${className || ''}`} aria-label="Ask Syra about this page">
      <header className="flex items-center gap-2 px-4 py-3 border-b border-border/60">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-fuchsia-500 flex items-center justify-center shadow-sm">
          <Sparkles className="w-4 h-4 text-white" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-foreground">Ask Syra about this page</div>
          <div className="text-xs text-muted-foreground truncate">
            {article?.title ? article.title : canAsk ? article.domain : 'Paste a URL first to enable questions'}
          </div>
        </div>
        {fromCache && (
          <span className="hidden sm:inline-flex items-center rounded-full bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200 px-2 py-0.5 text-[10px] font-semibold">
            cached
          </span>
        )}
      </header>

      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto min-h-[200px] max-h-[60vh] md:max-h-none"
        aria-live="polite"
        aria-atomic="false"
      >
        {showEmpty && (
          <div className="p-4 md:p-5">
            <div className="text-sm text-muted-foreground mb-3">
              {canAsk
                ? 'Ask anything — Syra will answer using this page and verified sources.'
                : 'Open a page on the left, then ask a question here.'}
            </div>
            {canAsk && (
              <div className="flex flex-wrap gap-2">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => { setQuery(s); sendQuery(s); }}
                    className="text-xs px-3 py-1.5 rounded-full border border-border bg-background hover:bg-muted text-foreground transition-colors"
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="m-4 flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-900" role="alert">
            <AlertTriangle className="w-4 h-4 flex-none mt-0.5" aria-hidden="true" />
            <div className="text-sm min-w-0">
              <div className="font-semibold">{error.title}</div>
              <div className="mt-0.5">{error.body}</div>
              <button
                type="button"
                onClick={() => { setError(null); sendQuery(); }}
                className="inline-flex items-center gap-1 mt-2 text-sm font-medium hover:underline"
              >
                <RotateCcw className="w-3.5 h-3.5" aria-hidden="true" /> Try again
              </button>
            </div>
          </div>
        )}

        {(answer || streaming) && (
          <div className="p-4 md:p-5">
            <div className="text-sm md:text-base text-foreground leading-relaxed whitespace-pre-wrap">
              {renderAnswerWithChips(answer, citationsById)}
              {streaming && (
                <span className="inline-block align-middle ml-1 w-2 h-4 bg-violet-500/70 animate-pulse rounded-sm" aria-hidden="true" />
              )}
            </div>
            {cancelled && (
              <div className="text-xs text-muted-foreground mt-3">Stopped.</div>
            )}
          </div>
        )}
      </div>

      <CitationTray citations={citations} />

      <form onSubmit={onSubmit} className="border-t border-border/60 p-3 md:p-4 bg-background">
        <div className="flex gap-2 items-end">
          <label htmlFor="ask-syra-input" className="sr-only">Ask a question</label>
          <textarea
            id="ask-syra-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                onSubmit(e);
              }
            }}
            placeholder={canAsk ? 'Ask about this page…' : 'Load a page first, then ask'}
            rows={1}
            disabled={!canAsk}
            className="flex-1 resize-none rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/60 disabled:opacity-60 disabled:cursor-not-allowed min-h-[44px] max-h-32"
            style={{ overflowY: 'auto' }}
          />
          {streaming ? (
            <button
              type="button"
              onClick={handleCancel}
              className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-muted text-foreground hover:bg-muted/70 focus:outline-none focus:ring-2 focus:ring-ring"
              aria-label="Stop streaming"
              title="Stop"
            >
              <StopCircle className="w-5 h-5" aria-hidden="true" />
            </button>
          ) : (
            <button
              type="submit"
              disabled={!canAsk || !query.trim()}
              className="inline-flex items-center justify-center w-11 h-11 rounded-xl bg-primary text-primary-foreground disabled:opacity-50 disabled:cursor-not-allowed hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-ring"
              aria-label="Ask"
              title="Ask"
            >
              <Send className="w-5 h-5" aria-hidden="true" />
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
