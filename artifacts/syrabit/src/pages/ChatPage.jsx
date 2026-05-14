/**
 * ChatPage — /chat
 * Full spec rebuild: 5-element animated empty state, typed bubbles,
 * actions bar (copy / regenerate / timestamp / credit badge),
 * credit progress bar, sync indicator, source badge.
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useNavigate, useSearchParams, useLocation } from 'react-router-dom';
import { buildCardContext } from '@/utils/cardContext';
import { AlertTriangle } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { useContentLang } from '@/context/LanguageContext';
import { getConversation, getAnonConversation, getSubject, getChapters, API_BASE, apiClient, getAnonId } from '@/utils/api';
import { AppLayout } from '@/components/layout/AppLayout';
import { toast } from 'sonner';

import { MessageBubble } from './chat/MessageBubble';
import { InputBar } from './chat/InputBar';
import { ModelSelector, MODELS } from './chat/ModelSelector';
import { Analytics } from '@/utils/analytics';
import { startTrace, makeTraceparent } from '@/utils/firebasePerf';
// React 19 hoists <title>/<meta>/<link> to <head> from anywhere in the
// tree without the SSR/client mismatch react-helmet-async causes. Use
// native tags directly. (Removes React error #418 on prerendered /chat.)

// EmptyState is imported eagerly so its h2 ("Hi! I'm Syra…") — the LCP
// element on /chat — renders in the SSR snapshot and on the very first
// client paint, instead of waiting for an async chunk. (Task #387)
import { EmptyState } from './chat/EmptyState';
import { useHashScroll } from '@/hooks/useHashScroll';
import { requestReviewPrompt } from '@/components/ReviewPrompt';

// ─────────────────────────────────────────────────────────────────────────────
// AD POLICY: /chat is intentionally AD-FREE. Do NOT import <AdSlot /> or any
// ad-network script here. The ad stack (Task #526) only runs on PYQ and Learn
// pages. Chat must stay distraction-free for the AI tutor experience.
// ─────────────────────────────────────────────────────────────────────────────

// ── ChatPage ──────────────────────────────────────────────────────────────────
export default function ChatPage() {
  const { user, authChecked } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const convId     = searchParams.get('id');
  const subjectId  = searchParams.get('subject');
  const documentId = searchParams.get('document_id');
  const chapterId  = searchParams.get('chapter');

  // Seed card_context from the originating page's react-router
  // Link state (Task #409). Currently PersonalizedCmsPage uses this
  // to send the plan summary; any future page with rich card content
  // can do the same. Captured once on mount so navigating to a
  // different conversation via ?id=… inside this same ChatPage
  // mount doesn't keep re-injecting the originating page's seed
  // into every subsequent message.
  const [seedCardContext] = useState(
    () => (typeof location.state?.seedCardContext === 'string'
      ? location.state.seedCardContext
      : '')
  );

  const [messages, setMessages]           = useState([]);
  const [input, setInput]                 = useState('');
  const [isLoading, setIsLoading]         = useState(false);
  const [conversationId, setConversationId] = useState(convId || null);
  const [model, setModel]                 = useState('openai/gpt-oss-20b');
  const [subject, setSubject]             = useState(null);
  const [scopedChapters, setScopedChapters] = useState([]);
  const [credits, setCredits]             = useState({ used: user?.credits_used || 0, limit: user?.credits_limit ?? null });
  const [syncState, setSyncState]         = useState('idle');
  // Once a conversation has loaded its messages, scroll to a `#m<index>`
  // hash if the URL carries one (set by AI-notes citation deep-links).
  useHashScroll(messages.length > 0 && syncState !== 'syncing');
  const [showModelMenu, setShowModelMenu] = useState(false);
  const [copiedMsgId, setCopiedMsgId]     = useState(null);
  // IMPORTANT: initialize to a deterministic constant ('en') and rehydrate
  // from localStorage in useEffect. Reading localStorage during render makes
  // the SSR snapshot (always 'en') drift from the client first render
  // (potentially 'as'), breaking hydration on the language toggle.
  // (Task #387 — architect review.)
  const [responseLang, setResponseLang] = useState('en');
  useEffect(() => {
    try {
      const stored = localStorage.getItem('syrabit_response_lang');
      if (stored && stored !== 'en') setResponseLang(stored);
    } catch {}
  }, []);
  const handleCopy = useCallback((msgId) => setCopiedMsgId(msgId), []);


  const messagesEndRef    = useRef(null);
  const lastUserMsgRef    = useRef(null);
  const textareaRef       = useRef(null);
  const abortControllerRef = useRef(null);
  const modelMenuRef      = useRef(null);
  const scrollTimeoutRef  = useRef(null);
  const autoRetryTimerRef = useRef(null);
  // Always points to the latest sendMsg closure so timers can call it safely.
  const sendMsgRef        = useRef(null);
  const pendingSendScroll = useRef(false);
  // Conversation IDs created locally during this session — we already
  // have their messages in state, so the URL→DB loader effect must
  // skip them (otherwise it overwrites the in-flight streaming AI
  // message with the not-yet-persisted DB snapshot, leaving the chat
  // visually empty until refresh).
  const ownedConvIds = useRef(new Set());

  useEffect(() => {
    return () => {
      if (abortControllerRef.current) abortControllerRef.current.abort();
      if (autoRetryTimerRef.current) clearTimeout(autoRetryTimerRef.current);
    };
  }, []);

  const lastMsgLenRef = useRef(0);
  useEffect(() => {
    const lastMsg = messages[messages.length - 1];
    const isStreaming = lastMsg?.streaming;
    const contentLen = (lastMsg?.content || '').length;
    // Throttle: while an answer is streaming, only re-scroll once we've
    // accumulated ≥80 new characters since the last scroll. BUT if a
    // brand-new user message just got sent (``pendingSendScroll`` is
    // true) we MUST always run the effect this tick, otherwise the
    // pin-to-top scroll is starved when the previous answer was long
    // (lastMsgLenRef still holds e.g. 2000 from the prior reply, while
    // the new streaming bubble starts at 0 — the delta is negative and
    // the early-return swallows the very scroll the user came here for).
    if (
      !pendingSendScroll.current &&
      isStreaming &&
      contentLen - lastMsgLenRef.current < 80 &&
      lastMsgLenRef.current > 0
    ) return;
    lastMsgLenRef.current = contentLen;
    if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
    scrollTimeoutRef.current = setTimeout(() => {
      if (pendingSendScroll.current && lastUserMsgRef.current) {
        pendingSendScroll.current = false;
        lastUserMsgRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
      }
      const container = messagesEndRef.current?.closest('.overflow-y-auto');
      if (container) {
        const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 150;
        if (atBottom) {
          messagesEndRef.current?.scrollIntoView({ behavior: isStreaming ? 'auto' : 'smooth', block: 'end' });
        }
      }
    }, isStreaming ? 120 : 40);
    return () => { if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current); };
  }, [messages]);

  // Task #796 — also fetch credits for anonymous students so the
  // composer can render "X / 30 free messages left today" against the
  // device-keyed daily counter that rate_limit_chat_optional charges.
  // The /user/credits endpoint peeks the same Redis key without
  // incrementing it, so polling here on every mount / send is safe and
  // never burns a free message. Bumped by ``creditsRefreshKey`` after
  // each anon send so the badge stays in sync (the SSE stream only
  // emits credits_used_total / remaining_credits for logged-in users —
  // anon users would otherwise need a hard refresh to see the count
  // tick down).
  const [creditsRefreshKey, setCreditsRefreshKey] = useState(0);
  useEffect(() => {
    // Wait for the /me round-trip so logged-in students don't fire a
    // throwaway anonymous request first; on the very first paint
    // ``user`` is null even for them.
    if (!authChecked) return;
    apiClient().get('/user/credits')
      .then((res) => {
        const c = res.data;
        setCredits({ used: c.used ?? 0, limit: c.limit ?? null });
      })
      .catch(() => {});
  }, [authChecked, user, creditsRefreshKey]);

  useEffect(() => {
    if (!subjectId) return;
    setSyncState('syncing');
    Promise.all([getSubject(subjectId), getChapters(subjectId)])
      .then(([subRes, chRes]) => { setSubject(subRes.data); setScopedChapters(chRes.data || []); setSyncState('idle'); })
      .catch(() => setSyncState('idle'));
  }, [subjectId]);

  useEffect(() => {
    if (!convId) return;
    // Skip server reload for conversations we just created locally —
    // their messages are already in state and the DB copy may be
    // missing the in-flight assistant message.
    if (ownedConvIds.current.has(convId)) return;
    setSyncState('syncing');
    const fetcher = user ? getConversation(convId) : getAnonConversation(convId);
    fetcher
      .then((r) => { const conv = r.data; setConversationId(conv.id); setMessages(conv.messages || []); setSyncState('idle'); })
      .catch(() => setSyncState('offline'));
  }, [convId, user]);

  useEffect(() => {
    const check = () => {
      if (document.visibilityState === 'visible') {
        fetch(`${API_BASE}/health`).then(() => setSyncState('idle')).catch(() => setSyncState('offline'));
      }
    };
    const goOffline = () => setSyncState('offline');
    const goOnline = () => {
      fetch(`${API_BASE}/health`).then(() => setSyncState('idle')).catch(() => setSyncState('offline'));
    };
    document.addEventListener('visibilitychange', check);
    window.addEventListener('offline', goOffline);
    window.addEventListener('online', goOnline);
    return () => {
      document.removeEventListener('visibilitychange', check);
      window.removeEventListener('offline', goOffline);
      window.removeEventListener('online', goOnline);
    };
  }, []);

  useEffect(() => {
    if (!showModelMenu) return;
    const handler = (e) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(e.target)) setShowModelMenu(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showModelMenu]);

  const adjustTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, []);

  useEffect(() => { adjustTextarea(); }, [input, adjustTextarea]);

  const activeChapter = useMemo(
    () => (chapterId && scopedChapters.length
      ? scopedChapters.find((ch) => ch.id === chapterId) ?? null
      : null),
    [chapterId, scopedChapters],
  );

  const onDismissChapter = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('chapter');
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const cardContext = useMemo(
    () => buildCardContext({
      subject: subjectId ? subject : null,
      scopedChapters,
      activeChapter,
      user,
      seedContext: seedCardContext,
    }),
    [subjectId, subject, scopedChapters, activeChapter, user, seedCardContext],
  );

  const effectiveLimit = credits.limit ?? user?.credits_limit ?? null;
  const remaining    = effectiveLimit !== null ? Math.max(0, effectiveLimit - credits.used) : null;
  const creditPercent = effectiveLimit != null && effectiveLimit > 0 ? Math.min(100, (credits.used / effectiveLimit) * 100) : 0;
  const isOutOfCredits = effectiveLimit !== null && effectiveLimit !== undefined && remaining !== null && remaining <= 0;
  const isLow = effectiveLimit !== null && effectiveLimit > 0 && remaining !== null && remaining > 0 && remaining <= 5;

  const handleNewChat = useCallback(() => {
    setMessages([]);
    setConversationId(null);
    setInput('');
    navigate('/chat', { replace: true });
  }, [navigate]);

  const handleStop = useCallback(() => {
    if (abortControllerRef.current) abortControllerRef.current.abort();
    setIsLoading(false);
    setMessages((prev) =>
      prev.map((m, i) =>
        i === prev.length - 1 && m.role === 'assistant' ? { ...m, streaming: false } : m
      )
    );
  }, []);

  const sendMsg = async (text) => {
    if (!text.trim() || isLoading || isOutOfCredits) return;
    // Cancel any pending auto-retry from a previous error.
    if (autoRetryTimerRef.current) {
      clearTimeout(autoRetryTimerRef.current);
      autoRetryTimerRef.current = null;
    }
    const msgId = Date.now().toString();
    const userMsg = { id: msgId + '_u', role: 'user', content: text, timestamp: new Date().toISOString() };
    const aiMsgId = msgId + '_a';
    const aiMsg   = { id: aiMsgId, role: 'assistant', content: '', streaming: true, timestamp: new Date().toISOString() };
    setMessages((prev) => [...prev, userMsg, aiMsg]);
    setInput('');
    setIsLoading(true);
    pendingSendScroll.current = true;
    // Reset the streaming-throttle baseline so the scroll effect doesn't
    // skip the pin-to-top run because the previous answer's length is
    // still cached in ``lastMsgLenRef`` (the new assistant bubble starts
    // empty, so without this reset the delta check wrongly suppresses
    // the scroll on the second-and-later sends in a conversation).
    lastMsgLenRef.current = 0;
    setSyncState('syncing');
    if (abortControllerRef.current) abortControllerRef.current.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    const payload = {
      message: text, conversation_id: conversationId,
      subject_id: subjectId || null, subject_name: subject?.name || null,
      chapter_id: chapterId || null, chapter_name: activeChapter?.title || null,
      board_id: user?.board_id || null, board_name: user?.board_name || null,
      class_id: user?.class_id || null, class_name: user?.class_name || null,
      stream_name: user?.stream_name || null, model,
      card_context: cardContext || null, document_id: documentId || null,
      // Task #37 — language selector is the SINGLE source of truth for
      // provider chain + Pinecone namespace + embed provider, so always
      // send it (not only when non-English). Backend's `chat_router`
      // collapses unknown codes to ``en`` deterministically.
      response_lang: responseLang || 'en',
    };
    // Task #610 — Firebase Performance custom traces + W3C trace propagation.
    // `chat_send_total` covers the full send→done lifecycle; `chat_send_first_token`
    // is stopped the moment the first SSE content event lands. Both are no-ops
    // when Firebase Perf is disabled / unsampled, so the chat path stays free.
    const _perfTotal = startTrace('chat_send_total', {
      model: model || 'default',
      auth: user ? 'user' : 'anon',
      has_subject: subjectId ? '1' : '0',
    });
    const _perfFirstToken = startTrace('chat_send_first_token', {
      model: model || 'default',
      auth: user ? 'user' : 'anon',
    });
    let _firstTokenStopped = false;
    const _stopFirstToken = () => {
      if (_firstTokenStopped) return;
      _firstTokenStopped = true;
      try { _perfFirstToken.stop(); } catch {}
    };
    const _tp = makeTraceparent();
    try {
      const fetchHeaders = { 'Content-Type': 'application/json' };
      if (_tp && _tp.traceparent) {
        fetchHeaders['traceparent'] = _tp.traceparent;
      }
      if (!user) {
        fetchHeaders['x-anon-id'] = getAnonId();
      }
      const response = await fetch(`${API_BASE}/ai/chat/stream`, {
        method: 'POST', headers: fetchHeaders,
        credentials: 'include', body: JSON.stringify(payload), signal: controller.signal,
        keepalive: false,
        priority: 'high',
      });
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        if (response.status === 402) {
          toast.error('Credits exhausted — upgrade to continue.', { action: { label: 'Upgrade', onClick: () => navigate('/profile') } });
          setMessages((prev) => prev.filter((m) => m.id !== aiMsgId));
          return;
        }
        // Task #370 — backend's strict 2-leg Assamese chat chain raises
        // HTTPException(503, "Assamese chat service temporarily
        // unavailable...") when both Sarvam and Vertex are down. Render
        // the localized in-bubble error card (with a "Switch to English
        // mode" escape hatch) instead of the generic toast.
        if (response.status === 503 && /^assamese chat/i.test(String(errData.detail || ''))) {
          setMessages((prev) => prev.map((m) =>
            m.id === aiMsgId
              ? {
                  ...m,
                  content: '',
                  isAiUnavailable: true,
                  isAssameseUnavailable: true,
                  retryText: text,
                  streaming: false,
                }
              : m
          ));
          return;
        }
        // Task #41 — V4 §12 fail-loud router branches (web_empty,
        // rag_empty) ship a structured detail body
        // ``{message, error_kind, route_trace}``. Render the
        // generic AI-unavailable card AND attach route_trace to
        // the failed message so the dev-only QA badge surfaces
        // the router decision (route=web / route=rag) on the
        // failed bubble. Without this, dev environments with no
        // seeded Pinecone vectors / empty web tool would never
        // show the badge for fail-loud turns even though the
        // routing decision is exactly what engineers want to
        // inspect.
        if (
          response.status === 503 &&
          errData &&
          errData.detail &&
          typeof errData.detail === 'object' &&
          (errData.detail.error_kind === 'web_empty' ||
            errData.detail.error_kind === 'rag_empty')
        ) {
          if (autoRetryTimerRef.current) clearTimeout(autoRetryTimerRef.current);
          setMessages((prev) => prev.map((m) =>
            m.id === aiMsgId
              ? {
                  ...m,
                  content: '',
                  isAiUnavailable: true,
                  isAssameseUnavailable: false,
                  retryText: text,
                  streaming: false,
                  route_trace: errData.detail.route_trace || null,
                }
              : m
          ));
          // Auto-retry once after 8s, mirroring the in-stream
          // error path. A second fail-loud will simply re-render
          // the same card with the same badge, which is the
          // intended dev signal.
          autoRetryTimerRef.current = setTimeout(() => {
            autoRetryTimerRef.current = null;
            setMessages((prev) => prev.filter((m) => m.id !== aiMsgId));
            sendMsgRef.current?.(text);
          }, 8000);
          return;
        }
        if (response.status === 429) {
          const detail = String(errData.detail || '');
          const capError = errData.error || '';
          const isDailyCap = capError === 'chat_daily_soft_cap' || /daily chat allowance|daily.*limit.*reached/i.test(detail);
          const isMonthlyCap = capError === 'chat_budget_exhausted' || /monthly.*budget|monthly.*chat/i.test(detail);
          const isAiRateLimit = /ai rate limit|rate limit exceeded|slow down/i.test(detail);
          if (isDailyCap && !user) {
            toast.error('Daily free limit reached — resets at midnight UTC. Sign in for more messages.', {
              action: { label: 'Sign in', onClick: () => navigate('/login') },
              duration: 8000,
            });
          } else if (isDailyCap && user) {
            toast.error('Daily chat allowance reached. Resets at midnight UTC.', { duration: 6000 });
          } else if (isMonthlyCap) {
            toast.error('Monthly chat budget reached. Resets at the start of next month.', { duration: 8000 });
          } else if (isAiRateLimit) {
            toast.error('Sending too fast — please wait a few seconds and try again.', { duration: 5000 });
          } else {
            toast.error(detail || 'Too many requests — please wait a moment and try again.', { duration: 6000 });
          }
          setMessages((prev) => prev.filter((m) => m.id !== aiMsgId));
          return;
        }
        throw new Error(errData.detail || 'Stream failed');
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let fullContent = '';
      const meta = {
        convId: conversationId, ragSource: 'none', ragChunks: 0,
        ragSubjectId: null, ragSubjectName: null, ragSubjectIcon: null,
        ragSubjectGradient: null, ragChapterName: null, ragChapterSlug: null,
        ragBoardName: null, ragClassName: null, ragTopicName: null,
        ragChunkSnippet: null, ragStreamName: null, ragBoardSlug: null,
        ragClassSlug: null, ragSubjectSlug: null, libSources: [], hasError: false,
      };

      let pendingChunk = '';
      let flushTimer = null;
      const FLUSH_INTERVAL = 5;
      const flushPending = () => {
        if (!pendingChunk) return;
        fullContent += pendingChunk; pendingChunk = '';
        flushTimer = null;
        const snapshot = fullContent;
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.id === aiMsgId) {
            const updated = prev.slice();
            updated[updated.length - 1] = { ...last, content: snapshot, translating: false };
            return updated;
          }
          return prev;
        });
      };
      let sseBuffer = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        sseBuffer += decoder.decode(value, { stream: true });
        const lines = sseBuffer.split('\n');
        sseBuffer = lines.pop() || '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6);
          if (raw === '[DONE]') break;
          let parsed;
          try { parsed = JSON.parse(raw); } catch { continue; }
          if (parsed.conversation_id) meta.convId = parsed.conversation_id;
          if (parsed.rag_source) meta.ragSource = parsed.rag_source;
          if (parsed.rag_chunks !== undefined) meta.ragChunks = parsed.rag_chunks;
          if (parsed.rag_subject_id) meta.ragSubjectId = parsed.rag_subject_id;
          if (parsed.rag_subject_name) meta.ragSubjectName = parsed.rag_subject_name;
          if (parsed.rag_subject_icon) meta.ragSubjectIcon = parsed.rag_subject_icon;
          if (parsed.rag_subject_gradient) meta.ragSubjectGradient = parsed.rag_subject_gradient;
          if (parsed.rag_chapter_name) meta.ragChapterName = parsed.rag_chapter_name;
          if (parsed.rag_chapter_slug) meta.ragChapterSlug = parsed.rag_chapter_slug;
          if (parsed.ctx_board_name) meta.ragBoardName = parsed.ctx_board_name;
          if (parsed.ctx_class_name) meta.ragClassName = parsed.ctx_class_name;
          if (parsed.ctx_stream_name) meta.ragStreamName = parsed.ctx_stream_name;
          if (parsed.ctx_board_slug) meta.ragBoardSlug = parsed.ctx_board_slug;
          if (parsed.ctx_class_slug) meta.ragClassSlug = parsed.ctx_class_slug;
          if (parsed.ctx_subject_slug) meta.ragSubjectSlug = parsed.ctx_subject_slug;
          if (parsed.rag_topic_name) meta.ragTopicName = parsed.rag_topic_name;
          if (parsed.rag_chunk_snippet) meta.ragChunkSnippet = parsed.rag_chunk_snippet;
          if (parsed.content_card_name && !meta.ragTopicName) meta.ragTopicName = parsed.content_card_name;
          if (parsed.content_card_board && !meta.ragBoardName) meta.ragBoardName = parsed.content_card_board;
          if (parsed.content_card_class && !meta.ragClassName) meta.ragClassName = parsed.content_card_class;
          if (parsed.content_card_subject && !meta.ragSubjectName) meta.ragSubjectName = parsed.content_card_subject;
          if (parsed.wai_chapter_match) {
            meta.waiChapterMatch = parsed.wai_chapter_match;
            setMessages((prev) => prev.map((m) =>
              m.id === aiMsgId ? { ...m, wai_chapter_match: parsed.wai_chapter_match } : m
            ));
          }
          if (parsed.event && parsed.event.startsWith('discovery:')) {
            const ev = { event: parsed.event, value: parsed.value || null };
            setMessages((prev) => prev.map((m) =>
              m.id === aiMsgId
                ? { ...m, discovery_events: [...(m.discovery_events || []), ev] }
                : m
            ));
          }
          if (parsed.translating) {
            setMessages((prev) => prev.map((m) => m.id === aiMsgId ? { ...m, content: '', translating: true } : m));
            continue;
          }
          if (parsed.error) {
            meta.hasError = true;
            // Task #41 — even on a fail-loud error chunk the backend
            // now ships the per-turn router decision so the dev-only
            // QA badge can render alongside the AI-unavailable card.
            // Without this, route=web/route=rag turns that 503 in dev
            // (no seeded Pinecone vectors / empty web tool) leave the
            // QA badge invisible even though the routing decision is
            // exactly what engineers want to inspect.
            if (parsed.route_trace) meta.routeTrace = parsed.route_trace;
            // Clear any previous auto-retry timer before scheduling a new one.
            if (autoRetryTimerRef.current) clearTimeout(autoRetryTimerRef.current);
            // Task #370 — Assamese chat strict-chain exhaustion is a
            // *known* unrecoverable state for Assamese mode. Auto-retrying
            // just hits the same dead chain again, so surface a localized
            // Assamese error card with a "Switch to English mode" escape
            // hatch instead of the generic English "Syra is resting"
            // toast + 8s auto-retry.
            const isAssameseUnavailable =
              parsed.error_kind === 'assamese_unavailable' ||
              (responseLang === 'as' &&
                /assamese chat|indic language ai|অসমীয়া/i.test(String(parsed.error || '')));
            setMessages((prev) => prev.map((m) =>
              m.id === aiMsgId
                ? {
                    ...m,
                    content: '',
                    isAiUnavailable: true,
                    isAssameseUnavailable,
                    retryText: text,
                    streaming: false,
                  }
                : m
            ));
            if (!isAssameseUnavailable) {
              // Auto-retry once after 8 seconds using the latest sendMsg closure.
              autoRetryTimerRef.current = setTimeout(() => {
                autoRetryTimerRef.current = null;
                setMessages((prev) => prev.filter((m) => m.id !== aiMsgId));
                sendMsgRef.current?.(text);
              }, 8000);
            }
            continue;
          }
          if (meta.hasError) continue;
          if (parsed.content) {
            // Task #610 — first content chunk = first-token milestone.
            _stopFirstToken();
            pendingChunk += parsed.content;
            if (!fullContent) flushPending();
            else if (!flushTimer) flushTimer = setTimeout(flushPending, FLUSH_INTERVAL);
          }
          if (parsed.event === 'syrabit_done') {
            if (parsed.sources) meta.libSources = parsed.sources;
            // Task #37 — capture the per-turn router trace so the
            // dev-mode QA badge can show decision/provider/namespace.
            if (parsed.route_trace) meta.routeTrace = parsed.route_trace;
            if (parsed.credits_used_total != null) {
              setCredits((c) => ({ ...c, used: parsed.credits_used_total }));
            }
            const remaining = parsed.remaining_credits ?? 0;
            try {
              Analytics.chatMessage(meta.ragSource, remaining, model);
              if (remaining <= 0) Analytics.chatCreditsExhausted();
            } catch {}
          }
        }
      }
      // Task #796 — anon SSE stream omits credits_used_total /
      // remaining_credits (the chat route only emits them when
      // ``not is_anon``). Bump the refresh key so the credits
      // effect re-peeks the device-keyed Redis counter and the
      // "X / 30 free messages left today" badge ticks down without
      // a page reload. No-op for logged-in users (their counts
      // already came back inline above) but still cheap (one tiny
      // GET to a Redis-backed endpoint).
      if (!user) {
        setCreditsRefreshKey((k) => k + 1);
      }
      if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }
      if (pendingChunk) { fullContent += pendingChunk; pendingChunk = ''; }
      if (meta.convId && meta.convId !== conversationId) {
        ownedConvIds.current.add(meta.convId);
        setConversationId(meta.convId);
        setSearchParams((prev) => { const next = new URLSearchParams(prev); next.set('id', meta.convId); return next; }, { replace: true });
      } else { setConversationId(meta.convId); }
      setMessages((prev) => prev.map((m) =>
        m.id === aiMsgId
          ? { ...m, content: fullContent, streaming: false, rag_source: meta.ragSource, rag_chunks: meta.ragChunks, rag_subject_id: meta.ragSubjectId, rag_subject_name: meta.ragSubjectName, rag_chapter_name: meta.ragChapterName, rag_chapter_slug: meta.ragChapterSlug, rag_board_name: meta.ragBoardName, rag_class_name: meta.ragClassName, rag_stream_name: meta.ragStreamName, rag_board_slug: meta.ragBoardSlug, rag_class_slug: meta.ragClassSlug, rag_subject_slug: meta.ragSubjectSlug, rag_topic_name: meta.ragTopicName, rag_chunk_snippet: meta.ragChunkSnippet, ctx_subject_name: subject?.name || null, ctx_subject_icon: meta.ragSubjectIcon || subject?.icon || null, ctx_subject_gradient: meta.ragSubjectGradient || subject?.gradient || null, sources: meta.libSources, route_trace: meta.routeTrace || null }
          : m
      ));
      setSyncState('idle');
      // Task #653 (Trustpilot per #724) — Ask for a Trustpilot review after a clearly successful,
      // engaged chat session. Heuristic: this send completed without an
      // error AND the conversation now has at least 8 messages exchanged
      // (~4 back-and-forth turns) — long enough that the student got real
      // value out of Syra. ReviewPrompt enforces all throttling, dismissal,
      // and per-30-day rules, so it is safe to call on every qualifying
      // send. Tune the 8-message threshold here if needed.
      if (!meta.hasError) {
        const totalAfterSend = messages.length + 2; // +user +assistant just appended
        if (totalAfterSend >= 8) {
          try { requestReviewPrompt('chat_engagement'); } catch {}
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') return;
      try { _perfTotal.putAttribute('error', '1'); } catch {}
      toast.error(err.message || 'Failed to get AI response');
      setMessages((prev) => prev.filter((m) => m.id !== aiMsgId));
      setSyncState('offline');
    } finally {
      setIsLoading(false);
      // Task #610 — close any open Firebase Perf traces. Safe to call
      // multiple times; stub stop() is a no-op when Perf is disabled.
      try { _stopFirstToken(); } catch {}
      try { _perfTotal.stop(); } catch {}
    }
  };
  // Keep the ref in sync so auto-retry timers always call the freshest closure.
  sendMsgRef.current = sendMsg;

  const handleRegenerate = useCallback(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === 'user');
    if (lastUser) { setMessages((prev) => prev.slice(0, -1)); sendMsg(lastUser.content); }
  }, [messages]); // eslint-disable-line

  const { contentLang } = useContentLang();
  const defaultPrompts = (() => {
    if (subject) {
      return contentLang === 'as'
        ? [
            `${subject.name}ৰ মূল ধাৰণাবোৰ বুজাই দিয়ক`,
            `পৰীক্ষাৰ বাবে ${subject.name}ৰ আটাইতকৈ গুৰুত্বপূৰ্ণ বিষয়বোৰ কি?`,
            `${subject.name}ৰ এটা সমাধান কৰা উদাহৰণ দিয়ক`,
            `${subject.name}ত ছাত্ৰ-ছাত্ৰীয়ে কৰা সাধাৰণ ভুলবোৰ কি?`,
          ]
        : [
            `Explain the key concepts of ${subject.name}`,
            `What are the most important topics in ${subject.name} for exams?`,
            `Give me a solved example from ${subject.name}`,
            `What are common mistakes students make in ${subject.name}?`,
          ];
    }
    return contentLang === 'as'
      ? [
          'এই ধাৰণাটো ধাপে ধাপে বুজাই দিয়ক',
          'পৰীক্ষাৰ বাবে সাজু এটা উত্তৰ দিয়ক',
          'এটা সমাধান কৰা উদাহৰণ দেখুৱাওক',
          'মনত ৰাখিবলগীয়া মুখ্য কথাবোৰ কি?',
        ]
      : [
          'Explain this concept step by step',
          'Give me an exam-ready answer',
          'Show me a solved example',
          'What are the key points to remember?',
        ];
  })();

  return (
    <>
      <title>Syrabit AI Chat — Ask Anything About Your Syllabus</title>
      <meta
        name="description"
        content="Ask Syrabit's AI tutor anything about AHSEC, SEBA and Degree subjects. Get instant explanations, MCQs, definitions and exam-ready answers in English or Assamese."
      />
      <link rel="canonical" href="https://syrabit.ai/chat" />
      {/* /chat is auth-gated and personalized — keep it out of the index. */}
      <meta name="robots" content="noindex, follow" />
      <meta property="og:title" content="Syrabit AI Chat — Ask Anything About Your Syllabus" />
      <meta
        property="og:description"
        content="AI-powered tutor for Assam Board (AHSEC, SEBA) and Degree students. Free to start, no card needed."
      />
      <meta property="og:url" content="https://syrabit.ai/chat" />
      <meta name="twitter:title" content="Syrabit AI Chat — Ask Anything About Your Syllabus" />
      <meta
        name="twitter:description"
        content="AI-powered tutor for Assam Board (AHSEC, SEBA) and Degree students. Free to start, no card needed."
      />
      <AppLayout pageTitle={
        <ModelSelector
          model={model} setModel={setModel}
          showModelMenu={showModelMenu} setShowModelMenu={setShowModelMenu}
          modelMenuRef={modelMenuRef} handleNewChat={handleNewChat}
          responseLang={responseLang} setResponseLang={setResponseLang}
        />
      }>
      <div className="flex flex-col chat-viewport-height">
        {isOutOfCredits && (
          /*
            Task #796 — for anonymous students this banner is the
            soft-CTA conversion lever the spec asks for: instead of
            "Upgrade →" (which dumps them on /profile, where they then
            still have to sign in), they get "Sign in →" pointing
            straight at /login. Logged-in students keep the original
            "Credits exhausted — upgrade →" copy.
          */
          <div
            className="flex items-center justify-between px-4 py-2.5 text-sm flex-shrink-0"
            style={{ background: 'rgba(239,68,68,0.08)', borderBottom: '1px solid rgba(239,68,68,0.15)' }}
            role="alert"
          >
            <div className="flex items-center gap-2 text-red-400">
              <AlertTriangle size={14} aria-hidden="true" />
              <span>
                {!user
                  ? `Free daily messages used (${effectiveLimit ?? 20}/day) — sign in for more`
                  : credits.limit === 0
                  ? 'Free plan has no credits — upgrade to start chatting'
                  : 'Credits exhausted — upgrade to continue'}
              </span>
            </div>
            {!user ? (
              <button
                onClick={() => navigate('/login')}
                className="text-xs font-semibold text-red-300 hover:text-red-200 transition-colors underline"
                aria-label="Sign in for more daily messages"
                data-testid="chat-out-of-credits-signin"
              >
                Sign in →
              </button>
            ) : (
              <button
                onClick={() => navigate('/profile')}
                className="text-xs font-semibold text-red-300 hover:text-red-200 transition-colors underline"
                aria-label="Go to profile to upgrade plan"
              >
                Upgrade →
              </button>
            )}
          </div>
        )}
        <div className="flex-1 overflow-y-auto min-h-0 pb-[calc(8rem+68px+env(safe-area-inset-bottom,0px))] md:pb-32" onClick={() => setShowModelMenu(false)} role="log" aria-label="Chat messages" aria-live="polite">
          <div className="max-w-3xl mx-auto px-4 md:px-6 py-4">
            {messages.length === 0 && (
              <div style={{ minHeight: '420px' }}>
                <EmptyState subject={subject} documentId={documentId} defaultPrompts={defaultPrompts} setInput={setInput} textareaRef={textareaRef} />
              </div>
            )}
              {(() => {
                let lastUIdx = -1;
                for (let j = messages.length - 1; j >= 0; j--) { if (messages[j].role === 'user') { lastUIdx = j; break; } }
                const out = [];
                messages.forEach((msg, i) => {
                  out.push(
                    <div key={msg.id || i} ref={i === lastUIdx ? lastUserMsgRef : undefined}>
                      <MessageBubble
                        msg={msg}
                        isLast={i === messages.length - 1}
                        onCopy={handleCopy}
                        onRegenerate={msg.role === 'assistant' && i === messages.length - 1 ? handleRegenerate : null}
                        onRetry={msg.isAiUnavailable && msg.retryText ? () => {
                          if (autoRetryTimerRef.current) { clearTimeout(autoRetryTimerRef.current); autoRetryTimerRef.current = null; }
                          setMessages((prev) => prev.filter((m) => m.id !== msg.id));
                          sendMsgRef.current?.(msg.retryText);
                        } : null}
                        // Task #370 — when the strict Assamese chat chain
                        // is exhausted, surface a one-click escape to the
                        // English chain. Switch responseLang to 'en'
                        // (persisted via the same key the LanguageSelector
                        // uses, so the toggle in the header reflects the
                        // change) and re-send the same query through
                        // english_rag_chat.
                        onSwitchToEnglish={msg.isAssameseUnavailable && msg.retryText ? () => {
                          if (autoRetryTimerRef.current) { clearTimeout(autoRetryTimerRef.current); autoRetryTimerRef.current = null; }
                          setResponseLang('en');
                          try { localStorage.setItem('syrabit_response_lang', 'en'); } catch {}
                          setMessages((prev) => prev.filter((m) => m.id !== msg.id));
                          // Defer one tick so the responseLang state
                          // commits before sendMsg captures the payload.
                          setTimeout(() => sendMsgRef.current?.(msg.retryText), 0);
                        } : null}
                        messageIndex={i}
                        conversationId={conversationId}
                        responseLang={responseLang}
                        subject={subject}
                        scopedChapters={scopedChapters}
                      />
                    </div>
                  );
                });
                return out;
              })()}
            {/*
              ChatGPT-style "pin user message to top while answer streams":
              the scroll effect above calls scrollIntoView({block: 'start'})
              on the most recent user message after each send, but the
              browser can only scroll as far as the container's content
              allows. With a freshly-sent message the streaming AI bubble
              starts empty, so without this spacer there isn't enough room
              below the user message to actually push it to the top of the
              viewport — the message ends up centred or near-bottom.
              Reserving ~one viewport of empty space below the messages
              while the assistant is still streaming gives the browser the
              headroom it needs. The spacer collapses to 0 once streaming
              ends so the chat doesn't end with a giant blank gap.
            */}
            {(() => {
              const lastMsg = messages[messages.length - 1];
              const showSpacer = !!(lastMsg && lastMsg.role === 'assistant' && lastMsg.streaming);
              if (!showSpacer) return null;
              return (
                <div
                  aria-hidden="true"
                  data-testid="chat-scroll-spacer"
                  // 100vh - composer height (~196px sticky at bottom) keeps
                  // the spacer from pushing the page taller than the screen
                  // so the scrollbar doesn't suddenly grow when streaming
                  // finishes and the spacer disappears.
                  style={{ minHeight: 'calc(100vh - 220px)' }}
                />
              );
            })()}
            <div ref={messagesEndRef} />
          </div>
        </div>
        <InputBar
          subject={subject} messages={messages} scopedChapters={scopedChapters}
          input={input} setInput={setInput} isLoading={isLoading}
          isOutOfCredits={isOutOfCredits} isLow={isLow} credits={credits}
          effectiveLimit={effectiveLimit} remaining={remaining} creditPercent={creditPercent}
          textareaRef={textareaRef} adjustTextarea={adjustTextarea} sendMsg={sendMsg} handleStop={handleStop}
          isAnon={!user}
          activeChapter={activeChapter}
          onDismissChapter={onDismissChapter}
        />
      </div>
      </AppLayout>
    </>
  );
}
