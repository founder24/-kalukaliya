/**
 * ChatPage — /chat
 * Full spec rebuild: 5-element animated empty state, typed bubbles,
 * actions bar (copy / regenerate / timestamp / credit badge),
 * credit progress bar, sync indicator, source badge.
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useNavigate, useSearchParams, useLocation } from 'react-router-dom';
import { buildCardContext } from '@/utils/cardContext';
import { AlertTriangle, Sparkles, X as XIcon } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { useContentLang } from '@/context/LanguageContext';
import { getConversation, getAnonConversation, getSubject, getChapters, API_BASE, apiClient, getAnonId } from '@/utils/api';
import { getToken } from '@/hooks/useTokenManager';
import { AppLayout } from '@/components/layout/AppLayout';
import { toast } from 'sonner';

import { MessageBubble } from './chat/MessageBubble';
import ChatSponsoredCard from '@/components/ads/ChatSponsoredCard';
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
import { getChatSponsorIndex } from '@/utils/chatAdPlacement';

const MAX_TRANSPORT_AUTO_RETRIES = 1;
const TRANSPORT_RETRY_DELAY_MS = import.meta.env.MODE === 'test' ? 10 : 3000;

function createChatRequestId() {
  try {
    return crypto.randomUUID();
  } catch {
    return `chat_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  }
}
// ─────────────────────────────────────────────────────────────────────────────
// Sponsored content is inserted only after completed assistant turns. It never
// renders inside a streaming turn or between a question and its answer.
// ─────────────────────────────────────────────────────────────────────────────

// ── ChatPage ──────────────────────────────────────────────────────────────────
export default function ChatPage() {
  const { user, authChecked } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const convId      = searchParams.get('id');
  const subjectId   = searchParams.get('subject');
  const documentId  = searchParams.get('document_id');
  const chapterId   = searchParams.get('chapter');
  const sourceSection = searchParams.get('section') || null;

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

  // Context passed from Ask AI buttons — carries chapter/subject info so the
  // chat page can show a "Answering from: …" banner and generate relevant prompts.
  const [chatContext] = useState(() => {
    const ctx = location.state?.chatContext;
    return (ctx && ctx.sourceTitle) ? ctx : null;
  });
  const [chatContextDismissed, setChatContextDismissed] = useState(false);

  const [messages, setMessages]           = useState([]);
  const [input, setInput]                 = useState('');
  const [isLoading, setIsLoading]         = useState(false);
  const [conversationId, setConversationId] = useState(convId || null);
  const [model, setModel]                 = useState('workers-ai-fast');
  const [subject, setSubject]             = useState(null);
  const [scopedChapters, setScopedChapters] = useState([]);
  const [credits, setCredits]             = useState({
    used: user?.credits_used || 0,
    limit: user?.credits_limit ?? null,
    resetAt: null,
    languages: {},
  });
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
  const activeChatRequestIdRef = useRef(null);
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

  // Read the current one-minute D1 bucket without consuming a request.
  const [creditsRefreshKey, setCreditsRefreshKey] = useState(0);
  useEffect(() => {
    // Wait for the /me round-trip so logged-in students don't fire a
    // throwaway anonymous request first; on the very first paint
    // ``user`` is null even for them.
    if (!authChecked) return;
    const anonId = user ? null : getAnonId();
    const creditHeaders = anonId ? { 'x-anon-id': anonId } : undefined;
    apiClient().get('/user/credits', creditHeaders ? { headers: creditHeaders } : undefined)
      .then((res) => {
        const c = res.data;
        const normalizeLanguageQuota = (quota = {}) => ({
          limit: Number(quota.limit ?? c.rpm_limit ?? 6),
          used: Number(quota.used ?? 0),
          remaining: Number(quota.remaining ?? quota.limit ?? c.rpm_limit ?? 6),
          resetAt: quota.resetAt ?? quota.reset_at ?? c.reset_at ?? null,
        });
        setCredits({
          used: c.credits_used ?? c.used ?? 0,
          limit: c.rpm_limit ?? 6,
          resetAt: c.reset_at ?? null,
          languages: {
            en: normalizeLanguageQuota(c.languages?.en),
            as: normalizeLanguageQuota(c.languages?.as),
          },
        });
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
    if (!authChecked) return; // wait for auth to settle before choosing anon vs. user fetcher
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
  }, [convId, user, authChecked]);

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
      next.delete('section');
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
      sourceSection,
    }),
    [subjectId, subject, scopedChapters, activeChapter, user, seedCardContext, sourceSection],
  );

  const languageCredits = credits.languages?.[responseLang] || null;
  const effectiveLimit = languageCredits?.limit ?? credits.limit ?? 6;
  const remaining = languageCredits?.remaining
    ?? (effectiveLimit !== null && languageCredits?.used != null
      ? Math.max(0, effectiveLimit - languageCredits.used)
      : effectiveLimit);
  const creditPercent = effectiveLimit != null && effectiveLimit > 0 ? Math.min(100, (credits.used / effectiveLimit) * 100) : 0;
  // RPM exhaustion is temporary and must never leave the composer disabled.
  const isOutOfCredits = false;
  const isLow = false;

  const handleNewChat = useCallback(() => {
    setMessages([]);
    setConversationId(null);
    setInput('');
    navigate('/chat', { replace: true });
  }, [navigate]);

  const handleStop = useCallback(() => {
    const requestId = activeChatRequestIdRef.current;
    if (requestId) {
      const headers = { 'Content-Type': 'application/json' };
      const token = getToken();
      if (token) headers.Authorization = `Bearer ${token}`;
      else {
        const anonId = getAnonId();
        if (anonId) headers['x-anon-id'] = anonId;
      }
      void fetch(`${API_BASE}/chat/cancel`, {
        method: 'POST',
        headers,
        credentials: 'include',
        keepalive: true,
        body: JSON.stringify({ client_request_id: requestId }),
      }).catch(() => {});
    }
    if (abortControllerRef.current) abortControllerRef.current.abort();
    setIsLoading(false);
    setMessages((prev) =>
      prev.map((m, i) =>
        i === prev.length - 1 && m.role === 'assistant'
          ? { ...m, streaming: false, isStopped: true, autoRetryScheduled: false }
          : m
      )
    );
  }, []);

  const sendMsg = async (text, retry = null) => {
    if (!text.trim() || isLoading || isOutOfCredits) return;
    // Cancel any pending auto-retry from a previous error.
    if (autoRetryTimerRef.current) {
      clearTimeout(autoRetryTimerRef.current);
      autoRetryTimerRef.current = null;
    }
    const msgId = retry?.msgId || Date.now().toString();
    const userMsgId = retry?.userMsgId || msgId + '_u';
    const aiMsgId = retry?.aiMsgId || msgId + '_a';
    const chatRequestId = retry?.chatRequestId || createChatRequestId();
    activeChatRequestIdRef.current = chatRequestId;
    const retryAttempt = retry?.attempt || 0;
    const userMsg = {
      id: userMsgId,
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    };
    const aiMsg = {
      id: aiMsgId,
      role: 'assistant',
      content: '',
      streaming: true,
      timestamp: new Date().toISOString(),
      retryText: text,
      userMsgId,
      chatRequestId,
      retryAttempt,
    };
    if (retry) {
      setMessages((prev) => prev.map((message) =>
        message.id === aiMsgId
          ? {
              ...message,
              ...aiMsg,
              isAiUnavailable: false,
              isAssameseUnavailable: false,
              isConnectionInterrupted: false,
              isStopped: false,
              autoRetryScheduled: false,
              failureStage: null,
              serverRequestId: null,
            }
          : message
      ));
    } else {
      setMessages((prev) => [...prev, userMsg, aiMsg]);
    }
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
      client_request_id: chatRequestId,
      subject_id: subjectId || null, subject_name: subject?.name || null,
      chapter_id: chapterId || null, chapter_name: activeChapter?.title || null,
      source_type: sourceSection || null,
      board_id: user?.board_id || null, board_name: user?.board_name || null,
      class_id: user?.class_id || null, class_name: user?.class_name || null,
      stream_name: user?.stream_name || null, model,
      card_context: cardContext || null, document_id: documentId || null,
      // Task #37 — language selector is the SINGLE source of truth for
      // provider chain + Pinecone namespace + embed provider, so always
      // send it (not only when non-English). Backend's `chat_router`
      // collapses unknown codes to ``en`` deterministically.
      response_lang: responseLang || 'en',
      // The backend ChatRequest model expects `lang` (not response_lang).
      // Map the frontend language selection so the explicit override reaches
      // the backend and is not silently dropped by Pydantic.
      lang: responseLang || null,
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
    // Request-scoped so a mid-stream failure can retain useful partial text.
    let fullContent = '';
    const _stopFirstToken = () => {
      if (_firstTokenStopped) return;
      _firstTokenStopped = true;
      try { _perfFirstToken.stop(); } catch {}
    };
    const _tp = makeTraceparent();
    let failureStage = 'pre_response';
    let serverRequestId = null;
    let receivedHttpResponse = false;
    const scheduleRetry = (delayMs = TRANSPORT_RETRY_DELAY_MS) => {
      if (retryAttempt >= MAX_TRANSPORT_AUTO_RETRIES) return false;
      if (autoRetryTimerRef.current) clearTimeout(autoRetryTimerRef.current);
      autoRetryTimerRef.current = setTimeout(() => {
        autoRetryTimerRef.current = null;
        sendMsgRef.current?.(text, {
          msgId,
          userMsgId,
          aiMsgId,
          chatRequestId,
          attempt: retryAttempt + 1,
        });
      }, delayMs);
      return true;
    };
    try {
      const fetchHeaders = { 'Content-Type': 'application/json' };
      if (_tp && _tp.traceparent) {
        fetchHeaders['traceparent'] = _tp.traceparent;
      }
      const _chatToken = getToken();
      if (_chatToken) {
        fetchHeaders['Authorization'] = `Bearer ${_chatToken}`;
      } else {
        const anonId = getAnonId();
        if (anonId) fetchHeaders['x-anon-id'] = anonId;
      }
      const response = await fetch(`${API_BASE}/chat/stream`, {
        method: 'POST', headers: fetchHeaders,
        credentials: 'include', body: JSON.stringify(payload), signal: controller.signal,
      });
      receivedHttpResponse = true;
      failureStage = 'http_response';
      serverRequestId = response.headers?.get?.('X-Request-ID') || null;
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        serverRequestId = serverRequestId || errData.request_id || null;
        failureStage = response.headers?.get?.('X-Failure-Stage')
          || errData.failure_stage
          || 'http_response';
        if (response.status === 402) {
          toast.error('You are sending messages too quickly. Please wait a minute and try again.');
          setMessages((prev) => prev.filter((m) => m.id !== aiMsgId));
          return;
        }
        if (response.status === 422 && errData.error_code === 'curriculum_scope_ambiguous') {
          setMessages((prev) => prev.map((m) =>
            m.id === aiMsgId
              ? {
                  ...m,
                  content: String(errData.detail || 'Please include both your class and subject.'),
                  streaming: false,
                  isAiUnavailable: false,
                  isCurriculumClarification: true,
                }
              : m
          ));
          setSyncState('idle');
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
                  userMsgId,
                  chatRequestId,
                  retryAttempt,
                  serverRequestId,
                  failureStage,
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
                  userMsgId,
                  chatRequestId,
                  retryAttempt,
                  serverRequestId,
                  failureStage,
                  streaming: false,
                  route_trace: errData.detail.route_trace || null,
                }
              : m
          ));
          scheduleRetry(8000);
          return;
        }
        if (response.status === 429) {
          // Pull structured error from JSON body (may be {} if CF WAF returned HTML)
          const detail = String(errData.detail || '');
          const capError = errData.error || '';
          // Also check response headers — CF Worker sets X-Chat-Cap-Error and X-Cap
          const capHeader = response.headers?.get?.('X-Chat-Cap-Error') || response.headers?.get?.('X-Cap') || '';

          const isDailyCap =
            capError === 'chat_daily_soft_cap' ||
            capHeader === 'chat_daily_soft_cap' ||
            /daily.*(chat|free|quota).*allowance|daily.*limit.*reached|daily.*quota.*exhausted|free quota exhausted/i.test(detail);
          const isMonthlyCap =
            capError === 'chat_budget_exhausted' ||
            capHeader === 'chat_budget_exhausted' ||
            /monthly.*budget|monthly.*chat/i.test(detail);
          const isAiRateLimit =
            /ai rate limit|rate limit exceeded|slow down|sending too fast/i.test(detail) ||
            capHeader === 'anon_rate_limited';
          const isNetworkBlock =
            capHeader === 'ip_daily_cap' ||
            /request ceiling.*network|network.*daily.*ceiling/i.test(detail);
          const isSessionLimit =
            capHeader === 'session_mint_limit' ||
            /too many new sessions|cookies.*enabled/i.test(detail);

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
          } else if (isNetworkBlock) {
            toast.error('Your network has reached its daily request limit. Sign in for a personal quota that isn\'t shared.', {
              action: { label: 'Sign in', onClick: () => navigate('/login') },
              duration: 8000,
            });
          } else if (isSessionLimit) {
            toast.error('Too many new connections from your network — wait a minute and try again.', { duration: 6000 });
          } else {
            // Fallback: detail may be non-empty (pass through) or empty (CF WAF HTML block)
            toast.error(
              detail || 'Too many requests — please wait a moment and try again.',
              { duration: 6000 }
            );
          }
          setMessages((prev) => prev.filter((m) => m.id !== aiMsgId));
          return;
        }
        // CF Worker infrastructure error (cold-start or upstream 502 converted
        // to 503). These use {"error":"...","status":...} without a `detail` field.
        // Show a user-friendly message and auto-retry once after 5 s.
        if (response.status >= 500 && !errData.detail) {
          const infraMsg = 'Server temporarily unavailable — retrying in a moment…';
          toast.error(infraMsg, { duration: 5000 });
          setMessages((prev) => prev.map((m) =>
            m.id === aiMsgId
              ? {
                  ...m,
                  content: '',
                  isAiUnavailable: true,
                  retryText: text,
                  userMsgId,
                  chatRequestId,
                  retryAttempt,
                  serverRequestId,
                  failureStage,
                  streaming: false,
                }
              : m
          ));
          scheduleRetry(5000);
          return;
        }
        throw new Error(errData.detail || errData.error || 'Stream failed');
      }
      failureStage = 'stream';
      if (!response.body) {
        throw new TypeError('Chat stream response had no body');
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const meta = {
        convId: conversationId, ragSource: 'none', ragChunks: 0,
        ragSubjectId: null, ragSubjectName: null, ragSubjectIcon: null,
        ragSubjectGradient: null, ragChapterName: null, ragChapterSlug: null,
        ragBoardName: null, ragClassName: null, ragTopicName: null,
        ragChunkSnippet: null, ragStreamName: null, ragBoardSlug: null,
        ragClassSlug: null, ragSubjectSlug: null, libSources: [], sourceEntries: [], hasError: false,
        // Source card fields emitted by backend before LLM starts
        matchScore: null, sourceType: null, confidenceTier: null, ragPath: null,
         chapterId: null, matchedPassage: null, retrievalMethod: null, sourceConfidence: null,
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
      let streamCompleted = false;
      let sawDoneMarker = false;
      while (!sawDoneMarker) {
        const { value, done } = await reader.read();
        sseBuffer += done
          ? decoder.decode()
          : decoder.decode(value, { stream: true });
        if (done && sseBuffer) sseBuffer += '\n';
        const lines = sseBuffer.split(/\r?\n/);
        sseBuffer = done ? '' : (lines.pop() || '');
        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          const raw = line.slice(5).trimStart();
          if (raw === '[DONE]') {
            sawDoneMarker = true;
            break;
          }
          let parsed;
          try { parsed = JSON.parse(raw); } catch { continue; }
          if (parsed.conversation_id) meta.convId = parsed.conversation_id;
          if (parsed.request_id) serverRequestId = parsed.request_id;
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
           if (parsed.rag_board_name) meta.ragBoardName = parsed.rag_board_name;
           if (parsed.rag_class_name) meta.ragClassName = parsed.rag_class_name;
           if (parsed.rag_stream_name) meta.ragStreamName = parsed.rag_stream_name;
          if (parsed.ctx_stream_name) meta.ragStreamName = parsed.ctx_stream_name;
          if (parsed.ctx_board_slug) meta.ragBoardSlug = parsed.ctx_board_slug;
          if (parsed.ctx_class_slug) meta.ragClassSlug = parsed.ctx_class_slug;
          if (parsed.ctx_subject_slug) meta.ragSubjectSlug = parsed.ctx_subject_slug;
          if (parsed.rag_topic_name) meta.ragTopicName = parsed.rag_topic_name;
          if (parsed.rag_chunk_snippet) meta.ragChunkSnippet = parsed.rag_chunk_snippet;
          if (parsed.chapter_id) meta.chapterId = parsed.chapter_id;
          if (parsed.matched_passage) meta.matchedPassage = parsed.matched_passage;
          if (parsed.retrieval_method) meta.retrievalMethod = parsed.retrieval_method;
          if (parsed.source_confidence != null) meta.sourceConfidence = parsed.source_confidence;
          if (parsed.content_card_name && !meta.ragTopicName) meta.ragTopicName = parsed.content_card_name;
          if (parsed.content_card_board && !meta.ragBoardName) meta.ragBoardName = parsed.content_card_board;
          if (parsed.content_card_class && !meta.ragClassName) meta.ragClassName = parsed.content_card_class;
          if (parsed.content_card_subject && !meta.ragSubjectName) meta.ragSubjectName = parsed.content_card_subject;
          // Source card fields — populated by the backend source_card SSE event
          // emitted before LLM starts (confidence_tier, match_score, source_type, rag_path)
          if (parsed.match_score != null) meta.matchScore = parsed.match_score;
          if (parsed.source_type) meta.sourceType = parsed.source_type;
          if (parsed.confidence_tier) meta.confidenceTier = parsed.confidence_tier;
          if (parsed.rag_path) meta.ragPath = parsed.rag_path;
          // `source_card` arrives before tokens. Preserve its detailed source
          // entries rather than waiting for the completion event, which only
          // carries legacy library sources on some routes.
          if (parsed.event === 'source_card' && Array.isArray(parsed.sources)) {
            meta.sourceEntries = parsed.sources;
          }
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
            if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }
            flushPending();
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
                    content: fullContent,
                    isAiUnavailable: true,
                    isConnectionInterrupted: Boolean(fullContent),
                    isPartialResponse: Boolean(fullContent),
                    isAssameseUnavailable,
                    retryText: text,
                    userMsgId,
                    chatRequestId,
                    retryAttempt,
                    serverRequestId,
                    failureStage: parsed.failure_stage || 'provider_stream',
                    streaming: false,
                  }
                : m
            ));
            if (!isAssameseUnavailable) {
              scheduleRetry(8000);
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
            streamCompleted = true;
            if (parsed.sources) meta.libSources = parsed.sources;
            // Task #37 — capture the per-turn router trace so the
            // dev-mode QA badge can show decision/provider/namespace.
            if (parsed.route_trace) meta.routeTrace = parsed.route_trace;
            if (parsed.credits_used_total != null) {
              setCredits((c) => ({
                ...c,
                used: parsed.credits_used_total,
                languages: {
                  ...c.languages,
                  [responseLang]: {
                    ...(c.languages?.[responseLang] || {}),
                    used: parsed.credits_used_total,
                    remaining: Math.max(0, (c.languages?.[responseLang]?.limit ?? c.limit ?? 6) - parsed.credits_used_total),
                  },
                },
              }));
            }
            const responseRemaining = Number(response.headers?.get?.('X-RateLimit-Remaining'));
            const responseReset = response.headers?.get?.('X-RateLimit-Reset');
            if (Number.isFinite(responseRemaining)) {
              setCredits((c) => ({
                ...c,
                languages: {
                  ...c.languages,
                  [responseLang]: {
                    ...(c.languages?.[responseLang] || {}),
                    used: Math.max(0, (c.languages?.[responseLang]?.limit ?? c.limit ?? 6) - responseRemaining),
                    remaining: responseRemaining,
                    limit: Number(c.languages?.[responseLang]?.limit ?? c.limit ?? 6),
                    resetAt: responseReset ? new Date(Number(responseReset) * 1000).toISOString() : c.resetAt,
                  },
                },
              }));
            }
            const remaining = parsed.remaining_credits ?? 0;
            try {
              Analytics.chatMessage(meta.ragSource, remaining, model);
              if (remaining <= 0) Analytics.chatCreditsExhausted();
            } catch {}
          }
        }
        if (done) break;
      }
      if (!streamCompleted && !meta.hasError) {
        throw new TypeError('Chat stream ended before completion');
      }
      if (meta.hasError) {
        if (flushTimer) clearTimeout(flushTimer);
        pendingChunk = '';
        setSyncState('idle');
        return;
      }
      // Anonymous SSE responses omit quota totals. Re-read the current
      // minute bucket after a send so the informational RPM state stays
      // current without changing the reservation count.
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
          ? { ...m, content: fullContent, streaming: false, rag_source: meta.ragSource, rag_chunks: meta.ragChunks, rag_subject_id: meta.ragSubjectId, rag_subject_name: meta.ragSubjectName, rag_chapter_id: meta.chapterId, rag_chapter_name: meta.ragChapterName, rag_chapter_slug: meta.ragChapterSlug, rag_board_name: meta.ragBoardName, rag_class_name: meta.ragClassName, rag_stream_name: meta.ragStreamName, rag_board_slug: meta.ragBoardSlug, rag_class_slug: meta.ragClassSlug, rag_subject_slug: meta.ragSubjectSlug, rag_topic_name: meta.ragTopicName, rag_chunk_snippet: meta.ragChunkSnippet, matched_passage: meta.matchedPassage, retrieval_method: meta.retrievalMethod, source_confidence: meta.sourceConfidence, ctx_subject_name: subject?.name || null, ctx_subject_icon: meta.ragSubjectIcon || subject?.icon || null, ctx_subject_gradient: meta.ragSubjectGradient || subject?.gradient || null, sources: meta.libSources, source_entries: meta.sourceEntries, route_trace: meta.routeTrace || null, match_score: meta.matchScore, source_type: meta.sourceType, confidence_tier: meta.confidenceTier, rag_path: meta.ragPath }
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
          requestReviewPrompt();
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') return;
      try { _perfTotal.putAttribute('error', '1'); } catch {}
      const isTransportFailure =
        !receivedHttpResponse ||
        failureStage === 'stream';
      if (isTransportFailure) {
        const autoRetryScheduled = scheduleRetry();
        console.warn('[chat] transport interrupted', {
          requestId: serverRequestId || chatRequestId,
          failureStage,
          retryAttempt,
          autoRetryScheduled,
        });
        setMessages((prev) => prev.map((message) =>
          message.id === aiMsgId
            ? {
                ...message,
                content: fullContent,
                streaming: false,
                isAiUnavailable: true,
                isConnectionInterrupted: true,
                isAssameseUnavailable: false,
                retryText: text,
                userMsgId,
                chatRequestId,
                retryAttempt,
                serverRequestId: serverRequestId || chatRequestId,
                failureStage,
                autoRetryScheduled,
                retryDelaySeconds: autoRetryScheduled
                  ? Math.ceil(TRANSPORT_RETRY_DELAY_MS / 1000)
                  : null,
              }
            : message
        ));
        setSyncState('offline');
      } else {
        console.warn('[chat] HTTP chat failure', {
          requestId: serverRequestId || chatRequestId,
          failureStage,
          status: 'response_received',
        });
        toast.error(err.message || 'Failed to get AI response');
        setMessages((prev) => prev.filter((m) => m.id !== aiMsgId));
      }
    } finally {
      if (activeChatRequestIdRef.current === chatRequestId) {
        activeChatRequestIdRef.current = null;
      }
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

  const { contentLang, switchLang } = useContentLang();

  // Sync contentLang (EmptyState heading + suggestion chips) with responseLang
  // (the chat language toggle). They are stored under separate localStorage
  // keys; this effect bridges them so the UI switches to Assamese the moment
  // the user picks it in the header dropdown.
  useEffect(() => {
    switchLang(responseLang);
  }, [responseLang, switchLang]);

  const defaultPrompts = (() => {
    // Chapter-specific prompts when opened from an Ask AI button with chapter context
    const chTitle = chatContext?.chapterTitle || (chatContext && !chatContext.chapterId ? null : chatContext?.sourceTitle) || '';
    if (chTitle && chatContext?.chapterId) {
      return contentLang === 'as'
        ? [
            `${chTitle} চমুকৈ বুজাই দিয়ক`,
            `${chTitle}ৰ পৰীক্ষাৰ বাবে গুৰুত্বপূৰ্ণ প্ৰশ্নবোৰ কি?`,
            `${chTitle}ৰ মূল ধাৰণাবোৰৰ তালিকা দিয়ক`,
            `${chTitle}ৰ পৰা এটা সমাধান কৰা উদাহৰণ দেখুৱাওক`,
          ]
        : [
            `Summarize ${chTitle} in key points`,
            `What are the most important exam questions from ${chTitle}?`,
            `Explain the main concepts of ${chTitle}`,
            `Give me a solved example from ${chTitle}`,
          ];
    }
    // Subject-level prompts (Ask AI from subject page or library card)
    const sName = chatContext?.subjectName || subject?.name || '';
    if (sName) {
      return contentLang === 'as'
        ? [
            `${sName}ৰ মূল ধাৰণাবোৰ বুজাই দিয়ক`,
            `পৰীক্ষাৰ বাবে ${sName}ৰ আটাইতকৈ গুৰুত্বপূৰ্ণ বিষয়বোৰ কি?`,
            `${sName}ৰ এটা সমাধান কৰা উদাহৰণ দিয়ক`,
            `${sName}ত ছাত্ৰ-ছাত্ৰীয়ে কৰা সাধাৰণ ভুলবোৰ কি?`,
          ]
        : [
            `Explain the key concepts of ${sName}`,
            `What are the most important topics in ${sName} for exams?`,
            `Give me a solved example from ${sName}`,
            `What are common mistakes students make in ${sName}?`,
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
      <meta name="robots" content="index, follow" />
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
        {/* Context banner — shown when user arrived via an Ask AI button with chapter/subject context */}
        {chatContext && !chatContextDismissed && (
          <div
            className="flex items-center justify-between px-4 py-2 text-xs flex-shrink-0"
            style={{ background: 'rgba(124,58,237,0.06)', borderBottom: '1px solid rgba(124,58,237,0.12)' }}
          >
            <div className="flex items-center gap-2 min-w-0">
              <Sparkles size={12} className="text-violet-500 shrink-0" aria-hidden="true" />
              <div className="min-w-0">
                <span className="font-medium text-violet-700 dark:text-violet-400 truncate">
                  {chatContext.chapterId ? 'Answering from:' : 'Topic:'}{' '}
                  {chatContext.sourceTitle}
                </span>
                {chatContext.sourceSubtitle && (
                  <span className="text-muted-foreground ml-1.5">· {chatContext.sourceSubtitle}</span>
                )}
              </div>
            </div>
            <button
              onClick={() => setChatContextDismissed(true)}
              className="shrink-0 ml-2 p-1 rounded text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Clear context"
            >
              <XIcon size={12} />
            </button>
          </div>
        )}
        <div
          className="sr-only"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          {isLoading ? 'Syra is writing an answer.' : messages.length > 0 ? 'Answer complete.' : ''}
        </div>
        <div className="flex-1 overflow-y-auto min-h-0 bg-background pb-[calc(7rem+64px+env(safe-area-inset-bottom,0px))] md:pb-32" onClick={() => setShowModelMenu(false)} role="log" aria-label="Chat messages">
          <div className="max-w-3xl mx-auto px-3 sm:px-4 md:px-6 py-3 sm:py-4">
            {messages.length === 0 && (
              <div style={{ minHeight: 'min(420px, calc(100dvh - 240px))' }}>
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
                        onRetry={(msg.isAiUnavailable || msg.isStopped) && msg.retryText ? () => {
                          if (autoRetryTimerRef.current) { clearTimeout(autoRetryTimerRef.current); autoRetryTimerRef.current = null; }
                          sendMsgRef.current?.(msg.retryText, {
                            msgId: String(msg.id || '').replace(/_a$/, ''),
                            userMsgId: msg.userMsgId,
                            aiMsgId: msg.id,
                            chatRequestId: msg.chatRequestId,
                            attempt: (msg.retryAttempt || 0) + 1,
                          });
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
                  const sponsorIndex = getChatSponsorIndex(messages, i);
                  if (sponsorIndex !== null) {
                    out.push(
                      <ChatSponsoredCard
                        key={`sponsor-after-${msg.id || i}`}
                        placementIndex={sponsorIndex}
                      />,
                    );
                  }
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
          sourceSection={sourceSection}
          responseLang={responseLang}
        />
      </div>
      </AppLayout>
    </>
  );
}
