import { useState, useMemo, useCallback, useEffect, useRef, memo, lazy, Suspense } from 'react';
import { useNavigate } from 'react-router-dom';
import { RefreshCw, Copy, Check, FileText, Globe, BookOpen, ThumbsUp, ThumbsDown, MessageSquare, Share2, Send, HelpCircle, ShieldAlert, ChevronDown, ChevronUp, Loader2 } from 'lucide-react';
import { ReadAloudButton } from '@/components/study/ReadAloudButton';
import { QuizModal } from '@/components/study/QuizModal';
import { useShare } from '@/hooks/useShare';
import { postChatFeedback, eduRequestSite, API_BASE } from '@/utils/api';
import { log } from '@/utils/logger';
import { toast } from 'sonner';
import { ThinkingIndicator } from './ThinkingIndicator';

const MarkdownContent = lazy(() => import('./MarkdownContent').then(m => ({ default: m.MarkdownContent })));

export const MessageBubble = memo(function MessageBubble({ msg, onCopy, onRegenerate, onRetry, onSwitchToEnglish, isLast, messageIndex, conversationId, responseLang, subject, scopedChapters }) {
  const [copied, setCopied] = useState(false);
  const [reaction, setReaction] = useState(null);
  const [showComment, setShowComment] = useState(false);
  const [comment, setComment] = useState('');
  const [commentSent, setCommentSent] = useState(false);
  const [quizOpen, setQuizOpen] = useState(false);
  // Strict-Mode "review hidden links" surface — populated by
  // MarkdownContent's onHiddenLinks callback after each render.
  const [hiddenLinks, setHiddenLinks] = useState([]);
  const [hiddenOpen, setHiddenOpen] = useState(false);
  const [requestState, setRequestState] = useState({}); // host -> 'pending'|'sent'|'failed'
  // Task #42 — dev-only "router log" expander on the QA badge.
  // ``routerLogState`` is one of 'idle' | 'loading' | 'ok' | 'error';
  // ``routerLogs`` holds the matching backend log lines.
  const [routerPanelOpen, setRouterPanelOpen] = useState(false);
  const [routerLogState, setRouterLogState] = useState('idle');
  const [routerLogs, setRouterLogs] = useState([]);
  const [routerLogError, setRouterLogError] = useState(null);
  // Countdown for the auto-retry that fires 8 s after an AI unavailable error.
  const [retryCountdown, setRetryCountdown] = useState(null);
  const retryTimerRef = useRef(null);

  // Start an 8-second countdown display when the message becomes an error card.
  // The actual retry is triggered by ChatPage; this is just the visual countdown.
  // Task #370 — skip the countdown for the Assamese-mode error card. The
  // strict 2-leg chain has no further fallback, so ChatPage disables the
  // auto-retry timer and the countdown would mislead the user.
  useEffect(() => {
    if (!msg.isAiUnavailable || msg.isAssameseUnavailable) {
      setRetryCountdown(null);
      if (retryTimerRef.current) clearInterval(retryTimerRef.current);
      return;
    }
    setRetryCountdown(8);
    let remaining = 8;
    retryTimerRef.current = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        clearInterval(retryTimerRef.current);
        setRetryCountdown(null);
      } else {
        setRetryCountdown(remaining);
      }
    }, 1000);
    return () => {
      if (retryTimerRef.current) clearInterval(retryTimerRef.current);
    };
  }, [msg.isAiUnavailable, msg.isAssameseUnavailable]);

  const handleHiddenLinks = useCallback((items) => {
    setHiddenLinks(items || []);
  }, []);

  const requestHiddenSite = useCallback(async (host) => {
    if (!host || requestState[host] === 'pending' || requestState[host] === 'sent') return;
    setRequestState((p) => ({ ...p, [host]: 'pending' }));
    try {
      await eduRequestSite(host, 'Requested from chat hidden-link review');
      setRequestState((p) => ({ ...p, [host]: 'sent' }));
      toast.success(`Requested ${host} for review`);
    } catch (e) {
      const status = e?.response?.status;
      // 429 = rate-limited; treat as informational, not a hard fail.
      if (status === 429) {
        setRequestState((p) => ({ ...p, [host]: 'failed' }));
        toast.error('Too many requests. Try again in a few minutes.');
      } else {
        setRequestState((p) => ({ ...p, [host]: 'failed' }));
        toast.error('Could not send request. Try again.');
      }
      log('hidden-link-request-error', e);
    }
  }, [requestState]);
  const { share } = useShare();
  const navigate = useNavigate();
  const isUser = msg.role === 'user';



  const sendFeedback = async (type, value) => {
    try {
      await postChatFeedback({
        conversation_id: conversationId || null,
        message_index: messageIndex ?? null,
        message_preview: (msg.content || '').slice(0, 300),
        reaction: type === 'reaction' ? value : (reaction || undefined),
        comment: type === 'comment' ? value : undefined,
      });
    } catch (e) {
      log('feedback-error', e);
    }
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(msg.content || '');
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      if (onCopy) onCopy(msg.id);
    } catch (err) {
      // Fallback for clipboard restrictions
      const textArea = document.createElement('textarea');
      textArea.value = msg.content || '';
      textArea.style.position = 'fixed';
      textArea.style.opacity = '0';
      document.body.appendChild(textArea);
      textArea.select();
      try {
        document.execCommand('copy');
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
        if (onCopy) onCopy(msg.id);
      } catch (e) {
        log.error('Clipboard copy failed', { error: e.message });
      }
      document.body.removeChild(textArea);
    }
  };

  const timeStr = msg.timestamp
    ? new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : '';

  const { cleanContent, sourceLine } = useMemo(() => {
    if (!msg.content) return { cleanContent: msg.content, sourceLine: '' };
    let extracted = '';
    const cleaned = msg.content
      .replace(/\n*\n?Sources?:\s*((\[(PAGE|CHAPTER):[^\]]+\][,\s]*)+\.?\s*)$/gi, '')
      .replace(/\n*\n?SOURCE\s*:\s*(.+)$/i, (_, match) => { extracted = match.trim(); return ''; })
      .trim();
    return { cleanContent: cleaned, sourceLine: extracted };
  }, [msg.content]);

  return (
    <div
      // Stable id `m{messageIndex}` lets AI-notes citation chips deep-link
      // back to the originating chat message via `/chat?id=…#m<idx>`.
      id={typeof messageIndex === 'number' ? `m${messageIndex}` : undefined}
      className={`group scroll-mt-20 ${isUser ? 'flex flex-col items-end mb-2' : 'mb-3'}`}
      data-testid="chat-message-bubble"
    >
      {isUser && (
        <>
          <div
            className="whitespace-pre-wrap"
            style={{
              padding: '10px 16px',
              background: '#7c3aed',
              borderRadius: '18px 18px 4px 18px',
              fontSize: '15px',
              lineHeight: '1.6',
              color: '#fff',
              maxWidth: 'min(70%, calc(100vw - 5rem))',
              wordWrap: 'break-word',
            }}
          >
            {msg.content}
          </div>
          {!msg.streaming && (
            <div className="flex items-center gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity">
              {timeStr && <span className="text-[11px] text-muted-foreground">{timeStr}</span>}
              <button
                onClick={handleCopy}
                className="w-11 h-11 rounded-lg flex items-center justify-center hover:bg-primary/10 text-muted-foreground hover:text-primary transition-colors"
                title="Copy"
                aria-label={copied ? 'Copied' : 'Copy'}
              >
                {copied ? <Check size={14} style={{ color: '#047857' }} /> : <Copy size={14} />}
              </button>
            </div>
          )}
        </>
      )}

      {!isUser && (
        <div className="w-full">
          <div className="flex items-center gap-2 mb-1">
            <div className="w-5 h-5 rounded-full overflow-hidden flex-shrink-0">
              <img src="/logo-56.webp" alt="Syrabit.ai" width="20" height="20" className="w-full h-full object-cover" />
            </div>
            <span className="text-xs font-semibold text-foreground/70">Syrabit AI</span>
          </div>
          <div className="w-full" style={msg.streaming ? { willChange: 'contents', contain: 'layout style' } : undefined}>
            {msg.streaming && !msg.content && !msg.translating && (
              <ThinkingIndicator subject={subject} scopedChapters={scopedChapters} chapterMatch={msg.wai_chapter_match || null} discoveryEvents={msg.discovery_events || []} />
            )}
            {msg.translating && !msg.content && (
              <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground animate-pulse">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m5 8 6 6"/><path d="m4 14 6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><path d="m22 22-5-10-5 10"/><path d="M14 18h6"/></svg>
                Translating to Assamese…
              </div>
            )}

            {msg.isAiUnavailable && (
              /*
                Task #370 — Assamese-mode strict-chain exhaustion gets a
                localized card (অসমীয়া title/body) plus a "Switch to
                English mode" escape hatch that re-routes the same query
                through english_rag_chat. The generic English variant is
                still used for every other failure (English chain
                exhaustion, generic 5xx, etc).
              */
              <div
                role="alert"
                data-testid={msg.isAssameseUnavailable ? 'assamese-unavailable-card' : 'ai-unavailable-card'}
                className="flex flex-col gap-3 rounded-2xl px-4 py-3.5 mt-1"
                style={{
                  background: 'rgba(124,58,237,0.06)',
                  border: '1px solid rgba(124,58,237,0.18)',
                  maxWidth: '26rem',
                }}
              >
                <div className="flex items-start gap-3">
                  <div
                    className="mt-0.5 flex-shrink-0 flex items-center justify-center rounded-full"
                    style={{ width: 32, height: 32, background: 'rgba(124,58,237,0.12)' }}
                    aria-hidden="true"
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#7c3aed" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
                    </svg>
                  </div>
                  <div lang={msg.isAssameseUnavailable ? 'as' : undefined}>
                    {msg.isAssameseUnavailable ? (
                      <>
                        <p className="text-sm font-semibold text-foreground" style={{ lineHeight: '1.55' }}>
                          অসমীয়া চেট সেৱা সাময়িকভাৱে অনুপলব্ধ
                        </p>
                        <p className="text-[12.5px] text-muted-foreground mt-0.5" style={{ lineHeight: '1.55' }}>
                          অনুগ্ৰহ কৰি কিছু সময়ৰ পিছত আকৌ চেষ্টা কৰক, বা ইংৰাজী মোডলৈ সলনি কৰক।
                        </p>
                      </>
                    ) : (
                      <>
                        <p className="text-sm font-semibold text-foreground" style={{ lineHeight: '1.45' }}>
                          Syra is resting — please try again in a moment
                        </p>
                        <p className="text-[12.5px] text-muted-foreground mt-0.5">
                          All AI services are temporarily busy. Your question is saved.
                        </p>
                      </>
                    )}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => { if (onRetry) onRetry(); }}
                    className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-colors"
                    style={{ background: '#7c3aed', color: '#fff' }}
                    aria-label={msg.isAssameseUnavailable ? 'আকৌ চেষ্টা কৰক' : 'Retry now'}
                    data-testid="ai-unavailable-retry"
                  >
                    <RefreshCw size={13} />
                    {msg.isAssameseUnavailable ? 'আকৌ চেষ্টা কৰক' : 'Retry now'}
                  </button>
                  {msg.isAssameseUnavailable && onSwitchToEnglish && (
                    <button
                      type="button"
                      onClick={onSwitchToEnglish}
                      className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-colors border"
                      style={{ background: 'transparent', borderColor: 'rgba(124,58,237,0.35)', color: '#7c3aed' }}
                      aria-label="Switch to English mode and retry"
                      data-testid="assamese-switch-english"
                    >
                      <Globe size={13} />
                      Switch to English mode
                    </button>
                  )}
                  {retryCountdown !== null && (
                    <span className="text-[12px] text-muted-foreground">
                      Auto-retry in {retryCountdown}s…
                    </span>
                  )}
                </div>
              </div>
            )}

            {!msg.isAiUnavailable && msg.content && (
              <Suspense fallback={<div className="md-content-light" style={{ fontSize: '0.9375rem' }}>{cleanContent}</div>}>
                <MarkdownContent
                  content={cleanContent}
                  streaming={!!msg.streaming}
                  sources={msg.sources}
                  onHiddenLinks={handleHiddenLinks}
                />
              </Suspense>
            )}

            {!msg.streaming && hiddenLinks.length > 0 && (
              <div
                className="mt-2 rounded-lg border border-amber-300/40 bg-amber-50/60 dark:bg-amber-900/10"
                data-testid="strict-mode-hidden-links"
              >
                <button
                  type="button"
                  onClick={() => setHiddenOpen((v) => !v)}
                  className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left"
                  aria-expanded={hiddenOpen}
                  aria-controls={`hidden-links-${msg.id || messageIndex}`}
                >
                  <span className="flex items-center gap-1.5 text-[12.5px] font-medium text-amber-800 dark:text-amber-200">
                    <ShieldAlert size={14} />
                    {hiddenLinks.length === 1
                      ? '1 link hidden by Strict Mode'
                      : `${hiddenLinks.length} links hidden by Strict Mode`}
                  </span>
                  <span className="flex items-center gap-1 text-[11.5px] text-amber-700/80 dark:text-amber-300/80">
                    Review
                    {hiddenOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                  </span>
                </button>
                {hiddenOpen && (
                  <ul
                    id={`hidden-links-${msg.id || messageIndex}`}
                    className="px-3 pb-2 space-y-1.5"
                  >
                    {hiddenLinks.map((it) => {
                      const st = requestState[it.host];
                      return (
                        <li
                          key={`${it.host}|${it.href}`}
                          className="flex items-center justify-between gap-2 text-[12px]"
                        >
                          <span className="min-w-0 flex-1 truncate">
                            <span className="font-mono text-foreground/90">{it.host || 'external site'}</span>
                            {it.text && (
                              <span className="ml-1 text-muted-foreground">— {it.text}</span>
                            )}
                          </span>
                          <button
                            type="button"
                            onClick={() => requestHiddenSite(it.host)}
                            disabled={st === 'pending' || st === 'sent' || !it.host}
                            className={`shrink-0 inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11.5px] font-medium transition-colors ${
                              st === 'sent'
                                ? 'bg-emerald-600/15 text-emerald-700 dark:text-emerald-300 cursor-default'
                                : 'bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-60'
                            }`}
                            aria-label={st === 'sent' ? `Already requested ${it.host}` : `Request review for ${it.host}`}
                          >
                            {st === 'pending' && <Loader2 size={11} className="animate-spin" />}
                            {st === 'sent'
                              ? 'Requested'
                              : st === 'pending'
                                ? 'Sending…'
                                : 'Request site'}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            )}

            {/* Task #37 — dev-only QA badge surfacing the per-turn
                router decision (decision / lang / namespace / embed
                provider / chain head). Mounted only when Vite is in
                dev mode AND the backend emitted a route_trace, so it
                never ships to production users. */}
            {!msg.streaming && msg.route_trace && import.meta.env.DEV && (
              <button
                type="button"
                onClick={async () => {
                  const next = !routerPanelOpen;
                  setRouterPanelOpen(next);
                  // Lazy-fetch the matching backend log lines the first
                  // time the panel opens (or on every open if the prior
                  // attempt failed). conversation_id may be missing on
                  // the very first turn before the backend assigns one
                  // — fall back to no filter so the most-recent log
                  // line still surfaces in dev.
                  if (!next) return;
                  if (routerLogState === 'loading') return;
                  if (routerLogState === 'ok' && routerLogs.length > 0) return;
                  setRouterLogState('loading');
                  setRouterLogError(null);
                  try {
                    const params = new URLSearchParams();
                    if (conversationId) params.set('conversation_id', conversationId);
                    params.set('limit', '20');
                    const res = await fetch(
                      `${API_BASE}/dev/router-logs/recent?${params.toString()}`,
                      { credentials: 'omit' },
                    );
                    if (!res.ok) {
                      throw new Error(`HTTP ${res.status}`);
                    }
                    const data = await res.json();
                    setRouterLogs(Array.isArray(data?.logs) ? data.logs : []);
                    setRouterLogState('ok');
                  } catch (e) {
                    setRouterLogError(e?.message || 'fetch failed');
                    setRouterLogState('error');
                  }
                }}
                className="mt-2 inline-flex flex-wrap items-center gap-1.5 rounded-md border border-dashed border-amber-400/60 bg-amber-50/70 px-2 py-1 text-[10px] font-mono text-amber-900 dark:border-amber-300/40 dark:bg-amber-900/20 dark:text-amber-200 cursor-pointer text-left hover:bg-amber-100/70 dark:hover:bg-amber-900/30 transition-colors"
                title="Click to expand router trace + matching backend log lines (dev only)"
                aria-expanded={routerPanelOpen}
                aria-controls={`router-log-panel-${msg.id || messageIndex}`}
                data-testid="chat-router-qa-badge"
              >
                <span className="font-semibold uppercase">QA</span>
                <span>route=<b>{msg.route_trace.decision}</b></span>
                <span>lang={msg.route_trace.lang}</span>
                {msg.route_trace.pinecone_namespace
                  ? <span>ns={msg.route_trace.pinecone_namespace}</span>
                  : <span className="opacity-60">ns=∅</span>}
                {msg.route_trace.embed_provider
                  ? <span>embed={msg.route_trace.embed_provider}</span>
                  : <span className="opacity-60">embed=∅</span>}
                {Array.isArray(msg.route_trace.provider_chain) && msg.route_trace.provider_chain.length > 0 && (
                  <span>head={msg.route_trace.provider_chain[0]}</span>
                )}
                {/* Task #39 — surface the real numeric centroid
                    similarity + the configured threshold so the
                    0.55 default can be eyeballed against actual
                    traffic. ``topic_score`` is the value the
                    dispatcher actually fed into the route() gate;
                    ``topic_threshold`` is what it was compared
                    against (CHAT_ROUTER_TOPIC_THRESHOLD env or
                    0.55 default). Renders only when a probe ran
                    (direct / casual paths leave it null). */}
                {typeof msg.route_trace.topic_score === 'number' && (
                  <span title="centroid similarity from chat_router.probe_topic_score">
                    score=<b>{msg.route_trace.topic_score.toFixed(3)}</b>
                  </span>
                )}
                {typeof msg.route_trace.topic_threshold === 'number' && (
                  <span className="opacity-70" title="CHAT_ROUTER_TOPIC_THRESHOLD env / 0.55 default">
                    th={msg.route_trace.topic_threshold.toFixed(2)}
                  </span>
                )}
                {msg.route_trace.reason && (
                  <span className="opacity-70" title={msg.route_trace.reason}>
                    · {String(msg.route_trace.reason).slice(0, 48)}
                  </span>
                )}
                <span className="opacity-60 ml-1" aria-hidden="true">
                  {routerPanelOpen ? '▾' : '▸'}
                </span>
              </button>
            )}

            {/* Task #42 — dev-only "router log" expander. Pretty-prints
                the full route_trace JSON and renders the matching
                backend [STREAM][ROUTER=...] log lines tailed from the
                in-process ring buffer at /api/dev/router-logs/recent.
                Gated on import.meta.env.DEV so it never ships to
                production users. */}
            {!msg.streaming && msg.route_trace && import.meta.env.DEV && routerPanelOpen && (
              <div
                id={`router-log-panel-${msg.id || messageIndex}`}
                data-testid="chat-router-log-panel"
                className="mt-1 rounded-md border border-amber-300/50 bg-amber-50/60 p-2 text-[11px] font-mono text-amber-900 dark:border-amber-300/30 dark:bg-amber-900/15 dark:text-amber-100"
                style={{ maxWidth: 'min(100%, 42rem)' }}
              >
                <div className="text-[10px] uppercase font-semibold opacity-70 mb-1">
                  route_trace
                </div>
                <pre
                  className="whitespace-pre-wrap break-all bg-amber-100/40 dark:bg-amber-900/25 rounded p-1.5 m-0"
                  style={{ maxHeight: '12rem', overflow: 'auto' }}
                  data-testid="chat-router-log-trace-json"
                >
                  {JSON.stringify(msg.route_trace, null, 2)}
                </pre>
                <div className="text-[10px] uppercase font-semibold opacity-70 mt-2 mb-1">
                  backend log lines
                  {conversationId
                    ? <span className="ml-1 normal-case opacity-70">(cid={String(conversationId).slice(0, 8)}…)</span>
                    : <span className="ml-1 normal-case opacity-70">(no cid — showing recent)</span>}
                </div>
                {routerLogState === 'loading' && (
                  <div className="flex items-center gap-1.5 opacity-80">
                    <Loader2 size={11} className="animate-spin" />
                    Fetching…
                  </div>
                )}
                {routerLogState === 'error' && (
                  <div
                    className="opacity-80"
                    data-testid="chat-router-log-error"
                  >
                    Could not load router logs: {routerLogError || 'unknown error'}.
                    The dev tail endpoint is gated on a non-production
                    backend env (ENV/ENVIRONMENT ≠ production).
                  </div>
                )}
                {routerLogState === 'ok' && routerLogs.length === 0 && (
                  <div className="opacity-80">
                    No matching <code>[STREAM][ROUTER=…]</code> log lines
                    in the last {500} entries of the in-process buffer.
                  </div>
                )}
                {routerLogState === 'ok' && routerLogs.length > 0 && (
                  <ul
                    className="space-y-1 m-0 p-0 list-none"
                    style={{ maxHeight: '14rem', overflow: 'auto' }}
                    data-testid="chat-router-log-lines"
                  >
                    {routerLogs.map((line, idx) => (
                      <li
                        key={`${line.ts}-${idx}`}
                        className="bg-amber-100/40 dark:bg-amber-900/25 rounded p-1.5"
                      >
                        <div className="opacity-60 text-[10px]">
                          {line.ts} · {line.level}
                        </div>
                        <div className="whitespace-pre-wrap break-all">
                          {line.message}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {!msg.streaming && msg.content && (() => {
              const subjectLabel = msg.rag_subject_name || msg.ctx_subject_name || null;
              const boardLabel = msg.rag_board_name || null;
              const classLabel = msg.rag_class_name || null;
              const chapterLabel = msg.rag_chapter_name || null;
              const chapterSlug = msg.rag_chapter_slug || null;
              const basePath = (msg.rag_board_slug && msg.rag_class_slug && msg.rag_subject_slug)
                ? `/${msg.rag_board_slug}/${msg.rag_class_slug}/${msg.rag_subject_slug}` : null;
              const chapterUrl = (basePath && chapterSlug) ? `${basePath}/${chapterSlug}` : null;
              const subjectUrl = chapterUrl || basePath || (msg.rag_subject_id ? `/subject/${msg.rag_subject_id}` : null);
              const isDocument = msg.rag_source === 'document';
              const isWeb = msg.rag_source === 'web';
              // Library, cache, and any other RAG-grounded source (anything
              // that isn't an uploaded user document, an external web hit,
              // or 'none') should render the same clickable card so the
              // student can deep-link from the answer back to the source
              // chapter — previously this only fired for ``rag_source ===
              // 'web'``, which meant the most common case (library /
              // cached library answers) showed no clickable badge at all.
              const isLibrary = msg.rag_source && !isDocument && !isWeb && msg.rag_source !== 'none';
              const hasContext = boardLabel || subjectLabel || (msg.rag_source && msg.rag_source !== 'none');

              const hasAnything = hasContext || sourceLine;
              if (!hasAnything) return null;

              const sourceMeta = isWeb
                ? { Icon: Globe, kindLabel: 'Web Search' }
                : isDocument
                  ? { Icon: FileText, kindLabel: 'Uploaded Document' }
                  : { Icon: BookOpen, kindLabel: 'Syrabit Library' };
              const showClickableCard = (isWeb || isLibrary) && subjectUrl && subjectLabel;
              const showStaticBadge = !showClickableCard && (isWeb || isDocument || isLibrary);
              const handleSourceCardClick = () => {
                if (chapterUrl) {
                  const topicText = msg.rag_topic_name || chapterLabel || '';
                  const params = new URLSearchParams();
                  params.set('from', 'chat');
                  if (topicText) params.set('topic', topicText);
                  const rawContent = (msg.content || '').replace(/[#*_`>\[\]()]/g, '').replace(/\s+/g, ' ').trim();
                  const sentences = rawContent.split(/(?<=[.!?])\s+/).filter(s => s.length > 20);
                  const coreSnippet = sentences.length > 1 ? sentences.slice(1, 4).join(' ') : rawContent;
                  const responseSnippet = coreSnippet.slice(0, 300);
                  if (responseSnippet) params.set('rchunk', responseSnippet);
                  navigate(`${chapterUrl}?${params.toString()}`);
                } else if (subjectUrl) {
                  navigate(subjectUrl);
                }
              };

              return (
                <>
                  {showClickableCard && (
                    <div
                      onClick={handleSourceCardClick}
                      className="source-card-container mt-3 rounded-xl overflow-hidden cursor-pointer active:scale-[0.98]"
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          handleSourceCardClick();
                        }
                      }}
                      aria-label={`Open ${chapterLabel || subjectLabel} in Syrabit Browser`}
                    >
                      <div className="px-3 py-2.5">
                        <div className="flex items-center gap-1.5 mb-1">
                          <sourceMeta.Icon size={11} className="source-card-icon" />
                          <span className="source-card-label text-[10px] font-semibold uppercase tracking-wider">Source</span>
                          <span className="text-[10px] text-muted-foreground" aria-hidden="true">·</span>
                          <span className="source-card-browser text-[10.5px] font-medium">{sourceMeta.kindLabel}</span>
                        </div>
                        {chapterLabel && (
                          <h4 className="source-card-title font-semibold leading-tight truncate" style={{ fontSize: '0.85rem', letterSpacing: '0.01em' }}>
                            {chapterLabel}
                          </h4>
                        )}
                        <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 mt-1.5">
                          {boardLabel && (
                            <>
                              <span className="source-card-badge text-[11px] font-medium px-1.5 py-0.5 rounded">{boardLabel}</span>
                              <span className="text-[11px] text-muted-foreground" aria-hidden="true">·</span>
                            </>
                          )}
                          {classLabel && (
                            <>
                              <span className="source-card-badge text-[11px] font-medium px-1.5 py-0.5 rounded">{classLabel}</span>
                              <span className="text-[11px] text-muted-foreground" aria-hidden="true">·</span>
                            </>
                          )}
                          <span className="source-card-badge text-[11px] font-medium px-1.5 py-0.5 rounded">{subjectLabel}</span>
                        </div>
                      </div>
                    </div>
                  )}
                  {showStaticBadge && (
                    <div className="flex items-center gap-2.5 mt-3 px-3 py-2 rounded-xl" style={{
                      background: isWeb ? 'rgba(56,189,248,0.08)' : isDocument ? 'rgba(139,92,246,0.08)' : 'rgba(34,197,94,0.08)',
                      border: isWeb ? '1px solid rgba(56,189,248,0.18)' : isDocument ? '1px solid rgba(139,92,246,0.18)' : '1px solid rgba(34,197,94,0.18)',
                      maxWidth: 'fit-content',
                    }}>
                      <div className="flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center" style={{
                        background: isWeb ? 'rgba(56,189,248,0.15)' : isDocument ? 'rgba(167,139,250,0.15)' : 'rgba(34,197,94,0.15)',
                      }}>
                        <sourceMeta.Icon size={16} style={{ color: isWeb ? '#38bdf8' : isDocument ? '#a78bfa' : '#22c55e' }} />
                      </div>
                      <span className="text-[13px] font-bold text-foreground" style={{ textTransform: 'uppercase', letterSpacing: '0.03em' }}>{sourceMeta.kindLabel}</span>
                    </div>
                  )}
                  <div className={`flex items-center gap-1.5 mt-1 transition-opacity ${responseLang && responseLang !== 'en' ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}>
                    {timeStr && (
                      <span className="text-[11px] text-muted-foreground">{timeStr}</span>
                    )}
                    <button
                      onClick={handleCopy}
                      className="w-11 h-11 rounded-lg flex items-center justify-center hover:bg-primary/10 text-muted-foreground hover:text-primary transition-colors"
                      title="Copy"
                      aria-label={copied ? 'Copied' : 'Copy'}
                    >
                      {copied ? <Check size={16} style={{ color: '#047857' }} /> : <Copy size={16} />}
                    </button>
                    <ReadAloudButton
                      id={`msg-${msg.id || messageIndex}`}
                      text={cleanContent || msg.content || ''}
                      className="!w-11 !h-11 justify-center !p-0"
                      label=""
                    />
                    <button
                      onClick={() => setQuizOpen(true)}
                      className="w-11 h-11 rounded-lg flex items-center justify-center hover:bg-primary/10 text-muted-foreground hover:text-primary transition-colors"
                      title="Quiz me on this answer"
                      aria-label="Quiz me"
                    >
                      <HelpCircle size={16} />
                    </button>
                    {isLast && onRegenerate && (
                      <button
                        onClick={onRegenerate}
                        className="w-11 h-11 rounded-lg flex items-center justify-center hover:bg-primary/10 text-muted-foreground hover:text-primary transition-colors"
                        title="Regenerate"
                        aria-label="Regenerate"
                      >
                        <RefreshCw size={16} />
                      </button>
                    )}
                    <button
                      onClick={() => {
                        const next = reaction === 'like' ? null : 'like';
                        setReaction(next);
                        if (next) sendFeedback('reaction', next);
                      }}
                      className={`w-11 h-11 rounded-lg flex items-center justify-center transition-colors ${reaction === 'like' ? 'bg-green-500/15 text-green-700' : 'hover:bg-primary/10 text-muted-foreground hover:text-primary'}`}
                      title="Like"
                      aria-label="Like"
                    >
                      <ThumbsUp size={15} fill={reaction === 'like' ? 'currentColor' : 'none'} />
                    </button>
                    <button
                      onClick={() => {
                        const next = reaction === 'dislike' ? null : 'dislike';
                        setReaction(next);
                        if (next) sendFeedback('reaction', next);
                      }}
                      className={`w-11 h-11 rounded-lg flex items-center justify-center transition-colors ${reaction === 'dislike' ? 'bg-red-500/15 text-red-600' : 'hover:bg-primary/10 text-muted-foreground hover:text-primary'}`}
                      title="Dislike"
                      aria-label="Dislike"
                    >
                      <ThumbsDown size={15} fill={reaction === 'dislike' ? 'currentColor' : 'none'} />
                    </button>
                    <button
                      onClick={() => setShowComment(v => !v)}
                      className={`w-11 h-11 rounded-lg flex items-center justify-center transition-colors ${showComment ? 'bg-primary/15 text-primary' : 'hover:bg-primary/10 text-muted-foreground hover:text-primary'}`}
                      title="Comment"
                      aria-label="Comment"
                    >
                      <MessageSquare size={15} />
                    </button>
                    <button
                      onClick={() => {
                        const title = (msg.content || '').slice(0, 80) + ((msg.content || '').length > 80 ? '…' : '');
                        share(title || 'Syrabit Chat', window.location.pathname);
                      }}
                      className="w-11 h-11 rounded-lg flex items-center justify-center hover:bg-primary/10 text-muted-foreground hover:text-primary transition-colors"
                      title="Share"
                      aria-label="Share"
                    >
                      <Share2 size={15} />
                    </button>
                  </div>
                  {showComment && (
                    <div className="mt-1.5 flex items-center gap-2">
                      <input
                        type="text"
                        value={comment}
                        onChange={e => setComment(e.target.value)}
                        placeholder={commentSent ? 'Feedback sent!' : 'Add feedback...'}
                        disabled={commentSent}
                        className="flex-1 text-[12px] px-3 py-1.5 rounded-lg bg-muted/50 border border-border/50 text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:border-primary/40 disabled:opacity-50"
                        onKeyDown={e => {
                          if (e.key === 'Enter' && comment.trim() && !commentSent) {
                            sendFeedback('comment', comment.trim());
                            setCommentSent(true);
                            setComment('');
                            toast.success('Feedback sent!');
                          }
                        }}
                      />
                      {!commentSent && (
                        <button
                          onClick={() => {
                            if (!comment.trim()) return;
                            sendFeedback('comment', comment.trim());
                            setCommentSent(true);
                            setComment('');
                            toast.success('Feedback sent!');
                          }}
                          disabled={!comment.trim()}
                          className="w-8 h-8 rounded-lg flex items-center justify-center bg-primary/15 text-primary hover:bg-primary/25 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                          title="Send feedback"
                        >
                          <Send size={14} />
                        </button>
                      )}
                      {commentSent && (
                        <Check size={16} style={{ color: '#047857' }} />
                      )}
                    </div>
                  )}
                </>
              );
            })()}
          </div>
        </div>
      )}
      <QuizModal
        open={quizOpen} onClose={() => setQuizOpen(false)}
        context={cleanContent || msg.content || ''}
        topic="this answer"
        response_lang={responseLang || 'en'}
        count={5}
      />
    </div>
  );
});
