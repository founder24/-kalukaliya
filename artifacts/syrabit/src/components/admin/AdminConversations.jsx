import { useState, useEffect, useMemo, useRef } from 'react';
import {
  Loader2, MessageSquare, BookOpen, Search, Mail, User, Ghost,
  ChevronRight, Crown, X, Clock, ArrowLeft, Sparkles, TrendingUp, RefreshCw, Flag,
} from 'lucide-react';
import AdminQuickLinks from './AdminQuickLinks';
import AdminFeedback from './AdminFeedback';
import { useSyraSelection, useSyraFilters, useSyraVisibleError } from '@/components/admin/syra/SyraContext';
import { adminGetConversations, extractFaqs, conversationsSentiment, syncConversations, API_BASE } from '@/utils/api';
import axios from 'axios';
import { toast } from 'sonner';
import { formatDistanceToNow, format } from 'date-fns';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
const PLAN_COLORS = {
  free: 'text-gray-500 bg-gray-100 border-gray-200',
  starter: 'text-violet-600 bg-violet-50 border-violet-200',
  pro: 'text-amber-600 bg-amber-50 border-amber-200',
};

function UserAvatar({ name, avatar, size = 32, isAnonymous = false }) {
  if (isAnonymous) {
    return (
      <div
        className="rounded-lg flex items-center justify-center flex-shrink-0 bg-gray-100"
        style={{ width: size, height: size }}
      >
        <Ghost size={size * 0.5} color="#9ca3af" />
      </div>
    );
  }
  const initials = (name || 'U').split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
  if (avatar) {
    return <img src={avatar} alt="" className="rounded-lg object-cover flex-shrink-0" style={{ width: size, height: size }} />;
  }
  return (
    <div
      className="rounded-lg flex items-center justify-center font-bold text-white flex-shrink-0 bg-violet-600"
      style={{ width: size, height: size, fontSize: size * 0.35 }}
    >
      {initials}
    </div>
  );
}

function SentimentBar({ data }) {
  if (!data || !data.total) return null;
  const { positive_pct, negative_pct, positive, negative, neutral, total } = data;
  return (
    <div className="rounded-xl p-4 bg-white border border-gray-200 shadow-sm">
      <div className="text-[11px] font-bold text-gray-400 uppercase mb-2">Sentiment Analysis ({total} messages)</div>
      <div className="flex h-2 rounded-full overflow-hidden gap-0.5">
        <div style={{ flex: positive, background: '#10b981' }} title={`Positive: ${positive_pct}%`} />
        <div style={{ flex: Math.max(0, total - positive - negative), background: '#e5e7eb' }} title={`Neutral`} />
        <div style={{ flex: negative, background: '#ef4444' }} title={`Negative: ${negative_pct}%`} />
      </div>
      <div className="flex gap-4 mt-2">
        {[
          { label: 'Positive', value: positive, pct: positive_pct, color: '#10b981' },
          { label: 'Neutral', value: neutral, color: '#9ca3af' },
          { label: 'Negative', value: negative, pct: negative_pct, color: '#ef4444' },
        ].map(s => (
          <div key={s.label} className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full" style={{ background: s.color }} />
            <span className="text-[11px] text-gray-500">{s.label}: <strong style={{ color: s.color }}>{s.value}</strong> {s.pct !== undefined ? `(${s.pct}%)` : ''}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const VALID_CONV_TABS = new Set(['conversations', 'faqs', 'feedback']);

export default function AdminConversations({ adminToken, onNavigate, navContext }) {
  const [conversations, setConversations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState('');
  // Task #296 — honour navContext.tab so deep-links from the legacy
  // `feedback` section id (and any future deep-links) land on the
  // requested tab instead of the default "conversations" pane.
  const initialTab = VALID_CONV_TABS.has(navContext?.tab) ? navContext.tab : 'conversations';
  const [tab, setTab] = useState(initialTab);
  useEffect(() => {
    if (VALID_CONV_TABS.has(navContext?.tab)) setTab(navContext.tab);
  }, [navContext?.tab]);
  const [faqs, setFaqs] = useState(null);
  const [faqLoading, setFaqLoading] = useState(false);
  const [sentiment, setSentiment] = useState(null);
  const [filterMode, setFilterMode] = useState('all');
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  // Section-level load error surfaced to Syra via useSyraVisibleError so
  // the orb can offer "want me to retry?" when Conversations fails.
  const [error, setError] = useState(null);
  const chatEndRef = useRef(null);

  const loadConversations = (token) => {
    setLoading(true);
    setError(null);
    adminGetConversations(token)
      .then((res) => { setConversations(Array.isArray(res?.data) ? res.data : []); setError(null); })
      .catch((e) => {
        const msg = e?.response?.data?.detail || 'Failed to load conversations';
        setError(msg);
        toast.error(msg);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadConversations(adminToken);
    conversationsSentiment(adminToken).then(r => setSentiment(r.data)).catch(() => {});
  }, [adminToken]);

  const handleSync = async () => {
    setSyncing(true);
    setSyncResult(null);
    try {
      const r = await syncConversations(adminToken);
      const d = r.data;
      setSyncResult(d);
      toast.success(`Sync complete — ${d.inserted} inserted, ${d.updated} updated, ${d.skipped} unchanged. PG now has ${d.pg_with_messages_after} conversations with ${d.pg_total_messages_after} messages.`);
      loadConversations(adminToken);
    } catch (err) {
      toast.error('Sync failed: ' + (err?.response?.data?.detail || err.message));
    } finally {
      setSyncing(false);
    }
  };

  const handleExtractFaqs = async () => {
    setFaqLoading(true);
    setTab('faqs');
    try {
      const r = await extractFaqs(adminToken, 150);
      setFaqs(r.data);
      toast.success(`Extracted ${r.data.faqs?.length || 0} FAQs from ${r.data.total_questions_analyzed} questions`);
    } catch {
      toast.error('FAQ extraction failed');
    } finally { setFaqLoading(false); }
  };

  const handleFlagConversation = async (e, convId) => {
    e.stopPropagation();
    const isRealJwt = adminToken && typeof adminToken === 'string' && adminToken.split('.').length === 3;
    const headers = isRealJwt ? { Authorization: `Bearer ${adminToken}` } : {};
    try {
      await axios.post(`${API_BASE}/admin/conversations/${convId}/flag`, {}, { headers, withCredentials: true });
      setConversations((prev) => prev.map((c) => c.id === convId ? { ...c, flagged: !c.flagged } : c));
      toast.success('Conversation flagged');
    } catch {
      toast.error('Failed to flag conversation');
    }
  };

  const totalMessages = useMemo(() => conversations.reduce((sum, c) => sum + (c.messages || []).length, 0), [conversations]);
  const withMessages = useMemo(() => conversations.filter(c => (c.messages || []).length > 0), [conversations]);
  const anonymousConvs = useMemo(() => conversations.filter(c => c.is_anonymous), [conversations]);
  const registeredConvs = useMemo(() => conversations.filter(c => !c.is_anonymous), [conversations]);

  const filtered = useMemo(() => {
    let base = conversations;
    if (filterMode === 'with_messages') base = withMessages;
    else if (filterMode === 'anonymous') base = anonymousConvs;
    else if (filterMode === 'registered') base = registeredConvs;
    if (!search.trim()) return base;
    const q = search.toLowerCase();
    return base.filter((c) =>
      (c.title || '').toLowerCase().includes(q) ||
      (c.subject_name || '').toLowerCase().includes(q) ||
      (c.user_email || '').toLowerCase().includes(q) ||
      (c.user_name || '').toLowerCase().includes(q) ||
      (c.messages || []).some(m => (m.content || '').toLowerCase().includes(q))
    );
  }, [conversations, withMessages, anonymousConvs, registeredConvs, search, filterMode]);

  const selectedConv = useMemo(() => conversations.find(c => c.id === selected), [conversations, selected]);
  // Task #298 — publish the selected conversation onto SyraContext so
  // the orb can resolve "flag this", "ban the user in this thread"
  // without forcing the operator to repeat the id.
  useSyraSelection(useMemo(() => (
    selectedConv
      ? {
          type: 'conversation',
          id: selectedConv.id,
          label: selectedConv.title || (selectedConv.user_name || 'Conversation'),
        }
      : null
  ), [selectedConv?.id, selectedConv?.title, selectedConv?.user_name]));
  // Task #298 — publish active filters + load error so Syra can answer
  // "what am I filtering by?" and offer a retry on visible errors.
  useSyraFilters(useMemo(() => ({
    search: search || undefined,
    mode: filterMode !== 'all' ? filterMode : undefined,
  }), [search, filterMode]));
  useSyraVisibleError(error || null);

  useEffect(() => {
    if (chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [selected, selectedConv?.messages?.length]);

  if (loading) return <div className="flex justify-center p-10"><Loader2 size={24} className="animate-spin text-violet-500" /></div>;

  const IMPORTANCE_COLOR = { high: '#ef4444', medium: '#f59e0b', low: '#10b981' };

  return (
    <SectionErrorBoundary name="Conversations">
      <div className="flex flex-col h-full" style={{ minHeight: 'calc(100vh - 120px)' }}>
        <div className="flex gap-1.5 px-4 py-2 items-center border-b border-gray-200 bg-white">
          {[
            { id: 'conversations', label: `Conversations (${conversations.length})` },
            { id: 'faqs', label: 'FAQ Extractor' },
            { id: 'feedback', label: 'Chat Feedback' },
          ].map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              data-testid={`admin-conversations-tab-${t.id}`}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                tab === t.id
                  ? 'bg-violet-600 text-white shadow-sm'
                  : 'bg-gray-100 text-gray-500 hover:text-gray-700'
              }`}>
              {t.label}
            </button>
          ))}
          <div className="flex-1" />
          <button onClick={handleSync} disabled={syncing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all border border-emerald-200 bg-emerald-50 text-emerald-700">
            {syncing ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
            {syncing ? 'Syncing…' : 'Sync Supabase → PG'}
          </button>
          <button onClick={handleExtractFaqs} disabled={faqLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all border border-violet-200 bg-violet-50 text-violet-700">
            {faqLoading ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />} Extract FAQs
          </button>
        </div>

        {tab === 'feedback' && (
          <div className="flex-1 overflow-y-auto">
            <AdminFeedback adminToken={adminToken} />
          </div>
        )}

        {tab === 'faqs' && (
          <div className="p-6 space-y-4 flex-1 overflow-y-auto">
            <SentimentBar data={sentiment} />
            {faqLoading ? (
              <div className="flex justify-center p-10"><Loader2 size={24} className="animate-spin text-violet-500" /></div>
            ) : faqs ? (
              <>
                <div className="rounded-xl p-4 bg-white border border-gray-200 shadow-sm">
                  <div className="flex items-center gap-2 mb-1">
                    <Sparkles size={14} color="#a78bfa" />
                    <span className="text-gray-900 font-bold text-sm">AI-Extracted FAQs</span>
                  </div>
                  <p className="text-gray-500 text-xs">
                    {faqs.faqs?.length || 0} FAQs from {faqs.total_questions_analyzed} student questions
                    {faqs.subjects?.length > 0 && ` · Subjects: ${faqs.subjects.slice(0,5).join(', ')}`}
                  </p>
                </div>
                <div className="space-y-2">
                  {(faqs.faqs || []).map((faq, i) => (
                    <div key={i} className="flex items-start gap-3 rounded-xl p-3 bg-gray-50 border border-gray-100">
                      <div className="w-6 h-6 rounded-lg flex items-center justify-center font-extrabold text-[11px] text-violet-600 flex-shrink-0 bg-violet-100">{i + 1}</div>
                      <div className="flex-1">
                        <p className="text-sm text-gray-700 font-medium leading-relaxed">{faq.question || faq}</p>
                        {faq.category && <p className="text-[11px] text-gray-400 mt-1">Category: {faq.category}</p>}
                      </div>
                      {faq.importance && (
                        <span className="text-[10px] font-extrabold px-2 py-0.5 rounded-full flex-shrink-0" style={{ background: `${IMPORTANCE_COLOR[faq.importance]}15`, color: IMPORTANCE_COLOR[faq.importance] }}>
                          {faq.importance}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
                {faqs.suggested_pages?.length > 0 && (
                  <div className="rounded-xl p-4 bg-emerald-50 border border-emerald-200">
                    <p className="text-[11px] font-bold text-emerald-700 mb-2 uppercase tracking-wide">Suggested SEO Pages</p>
                    {faqs.suggested_pages.slice(0, 8).map((p, i) => (
                      <div key={i} className="text-xs text-gray-500 py-1 border-b border-emerald-100">
                        → {p.title} <span className="text-emerald-600">({p.priority})</span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div className="text-center py-16">
                <Sparkles size={32} className="mx-auto mb-3 text-gray-300" />
                <p className="text-gray-400 text-sm">Click "Extract FAQs" to analyse student questions with AI</p>
              </div>
            )}
          </div>
        )}

        {tab === 'conversations' && (
        <div className="flex flex-1 min-h-0">
        <div className={`${selected ? 'hidden lg:flex' : 'flex'} flex-col w-full lg:w-[380px] lg:min-w-[380px] border-r border-gray-200`}>
          <div className="p-4 border-b border-gray-200 space-y-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <h2 className="text-gray-900 font-semibold" data-syra="conversations-header">Conversations ({conversations.length})</h2>
                <p className="text-xs text-gray-400">{withMessages.length} with messages · {totalMessages} total msgs · {anonymousConvs.length} anonymous</p>
              </div>
              <div className="flex gap-1 flex-shrink-0">
                {[
                  { id: 'all', label: 'All' },
                  { id: 'with_messages', label: 'With msgs' },
                  { id: 'anonymous', label: `Anon (${anonymousConvs.length})` },
                  { id: 'registered', label: 'Registered' },
                ].map(f => (
                  <button key={f.id} onClick={() => setFilterMode(f.id)}
                    className={`px-2.5 py-1 rounded-lg text-[10px] font-bold transition-all ${
                      filterMode === f.id
                        ? 'bg-violet-600 text-white'
                        : 'bg-gray-100 text-gray-500'
                    }`}>
                    {f.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                placeholder="Search name, email, title, subject..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full pl-9 pr-3 py-2 text-sm rounded-xl text-gray-700 placeholder-gray-400 focus:outline-none bg-gray-50 border border-gray-200 focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20"
              />
            </div>
          </div>

          <div className="flex-1 overflow-y-auto">
            {filtered.length === 0 && (
              <div className="text-center py-12 text-gray-300">
                <MessageSquare size={32} className="mx-auto mb-2 opacity-30" />
                <p className="text-sm">{search ? 'No conversations match' : 'No conversations yet'}</p>
              </div>
            )}
            {filtered.map((conv) => {
              const msgCount = (conv.messages || []).length;
              const hasMsgs = msgCount > 0;
              return (
              <div
                key={conv.id}
                onClick={() => setSelected(conv.id)}
                className={`flex items-start gap-3 p-4 cursor-pointer border-b border-gray-100 transition-colors ${
                  selected === conv.id ? 'bg-violet-50 border-l-2 border-l-violet-500' : 'hover:bg-gray-50'
                }`}
              >
                <UserAvatar name={conv.user_name} avatar={conv.user_avatar} size={36} isAnonymous={conv.is_anonymous} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium text-gray-700 truncate">{conv.title || 'Untitled'}</p>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <button
                        onClick={(e) => handleFlagConversation(e, conv.id)}
                        aria-label="Flag conversation"
                        title={conv.flagged ? 'Flagged' : 'Flag conversation'}
                        className={`p-1 rounded transition-colors ${conv.flagged ? 'text-red-500' : 'text-gray-300 hover:text-red-400'}`}
                      >
                        <Flag size={11} />
                      </button>
                      <span className="text-[10px] text-gray-400">
                        {formatDistanceToNow(new Date(conv.updated_at || conv.created_at), { addSuffix: true })}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <p className="text-xs text-gray-400 truncate">{conv.is_anonymous ? 'Anonymous User' : (conv.user_name || 'Unknown')}</p>
                    {conv.is_anonymous && (
                      <span className="text-[9px] font-extrabold px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500 border border-gray-200">
                        Anonymous
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 mt-1 text-[10px]">
                    {conv.subject_name && <span className="text-gray-400 truncate">{conv.subject_name}</span>}
                    {hasMsgs ? (
                      <span className="text-emerald-600 font-bold">{msgCount} msgs</span>
                    ) : (
                      <span className="text-gray-300 italic">no messages</span>
                    )}
                  </div>
                </div>
              </div>
              );
            })}
          </div>
        </div>

        <div className={`${selected ? 'flex' : 'hidden lg:flex'} flex-col flex-1 min-w-0`}>
          {!selectedConv ? (
            <div className="flex-1 flex items-center justify-center text-gray-300">
              <div className="text-center">
                <MessageSquare size={48} className="mx-auto mb-3 opacity-20" />
                <p className="text-sm">Select a conversation to view</p>
              </div>
            </div>
          ) : (
            <>
              <div className="p-4 border-b border-gray-200">
                <div className="flex items-start gap-3">
                  <button
                    onClick={() => setSelected(null)}
                    className="lg:hidden p-1.5 rounded-lg hover:bg-gray-100 text-gray-400 mt-1"
                  >
                    <ArrowLeft size={16} />
                  </button>
                  <UserAvatar name={selectedConv.user_name} avatar={selectedConv.user_avatar} size={44} isAnonymous={selectedConv.is_anonymous} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-gray-900 truncate">{selectedConv.is_anonymous ? 'Anonymous User' : (selectedConv.user_name || 'Unknown User')}</h3>
                      {selectedConv.is_anonymous ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border text-gray-500 bg-gray-100 border-gray-200">
                          <Ghost size={8} />
                          Anonymous
                        </span>
                      ) : (
                        <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border ${PLAN_COLORS[selectedConv.user_plan] || PLAN_COLORS.free}`}>
                          <Crown size={8} />
                          {(selectedConv.user_plan || 'free').charAt(0).toUpperCase() + (selectedConv.user_plan || 'free').slice(1)}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 text-xs text-gray-400 mt-0.5 flex-wrap">
                      {selectedConv.is_anonymous ? (
                        <span className="flex items-center gap-1">
                          <Ghost size={10} />
                          ID: {selectedConv.anon_id?.slice(0, 12)}…
                        </span>
                      ) : (
                        <>
                          {selectedConv.user_email && (
                            <span className="flex items-center gap-1"><Mail size={10} />{selectedConv.user_email}</span>
                          )}
                          {selectedConv.user_board && <span>{selectedConv.user_board}</span>}
                          {selectedConv.user_class && <span>{selectedConv.user_class}</span>}
                          {selectedConv.user_stream && <span>{selectedConv.user_stream}</span>}
                        </>
                      )}
                    </div>
                    <div className="flex items-center gap-3 text-xs text-gray-400 mt-1">
                      <span className="font-medium text-gray-600 truncate">{selectedConv.title || 'Untitled'}</span>
                      {selectedConv.subject_name && (
                        <span className="flex items-center gap-1"><BookOpen size={10} />{selectedConv.subject_name}</span>
                      )}
                      <span className="flex items-center gap-1">
                        <Clock size={10} />
                        {format(new Date(selectedConv.created_at || selectedConv.updated_at), 'MMM d, yyyy h:mm a')}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-gray-50">
                {(selectedConv.messages || []).map((msg, i) => (
                  <div key={i} className="flex gap-3">
                    <div className="flex-shrink-0 mt-1">
                      {msg.role === 'user' ? (
                        <UserAvatar name={selectedConv.user_name} avatar={selectedConv.user_avatar} size={28} isAnonymous={selectedConv.is_anonymous} />
                      ) : (
                        <div className="w-7 h-7 rounded-lg overflow-hidden flex-shrink-0 bg-violet-600 flex items-center justify-center">
                          <img
                            src={`${import.meta.env.BASE_URL}logo-56.webp`}
                            alt="Syra"
                            className="w-full h-full object-cover"
                            onError={e => { e.currentTarget.style.display = 'none'; }}
                          />
                        </div>
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className={`text-xs font-semibold ${msg.role === 'user' ? 'text-blue-600' : 'text-violet-600'}`}>
                          {msg.role === 'user' ? (selectedConv.is_anonymous ? 'Anonymous User' : (selectedConv.user_name || 'Student')) : 'Syra AI'}
                        </span>
                        {msg.timestamp && (
                          <span className="text-[10px] text-gray-300">
                            {format(new Date(msg.timestamp), 'h:mm a')}
                          </span>
                        )}
                      </div>
                      <div
                        className={`text-sm rounded-xl px-3.5 py-2.5 ${
                          msg.role === 'user'
                            ? 'bg-white border border-gray-200 text-gray-700'
                            : 'bg-violet-50 border border-violet-100 text-gray-700'
                        }`}
                      >
                        <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{msg.content}</span>
                      </div>
                    </div>
                  </div>
                ))}
                <div ref={chatEndRef} />
              </div>

              <div className="p-3 border-t border-gray-200 text-center bg-white">
                <p className="text-[10px] text-gray-400">
                  {(selectedConv.messages || []).length} messages · Conversation ID: {selectedConv.id?.slice(0, 8)}...
                </p>
              </div>
            </>
          )}
        </div>
        </div>
        )}
        <AdminQuickLinks links={['users','analytics','dashboard','vertex']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
