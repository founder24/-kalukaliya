import { useState, useEffect, useRef, useMemo } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { Helmet } from 'react-helmet-async';
import {
  ArrowLeft, Loader2, AlertCircle, Lock, Zap, BookOpen,
  Calendar, Target, ChevronRight, Sparkles, RefreshCw,
  Clock, FileText,
} from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import { useAuth } from '@/context/AuthContext';
import { apiClient } from '@/utils/api';
import { buildPlanSeedContext } from '@/utils/cardContext';

export default function PersonalizedCmsPage() {
  const { userId, slug } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [doc, setDoc]         = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);
  const [payError, setPayError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setPayError(null);
    setDoc(null);
    apiClient().get(`/cms/${userId}/${slug}`)
      .then(r => setDoc(r.data))
      .catch(e => {
        const status = e.response?.status;
        if (status === 402) setPayError(e.response?.data?.message || 'Upgrade required');
        else if (status === 403) setError('forbidden');
        else if (status === 404) setError('not-found');
        else setError('error');
      })
      .finally(() => setLoading(false));
  }, [userId, slug]);

  if (loading) {
    return (
      <AppLayout>
        <div className="min-h-screen flex items-center justify-center ">
          <Loader2 className="animate-spin text-violet-400" size={32} />
        </div>
      </AppLayout>
    );
  }

  if (payError) {
    return (
      <AppLayout>
        <div className="min-h-screen flex flex-col items-center justify-center gap-0  px-4">
          <div className="w-full max-w-md rounded-2xl overflow-hidden"
            style={{ border: '1px solid rgba(139,92,246,0.25)', background: 'var(--card)' }}>
            <div className="px-6 pt-6 pb-4" style={{ borderBottom: '1px solid rgba(139,92,246,0.10)' }}>
              <div className="flex items-center gap-2 mb-3">
                <div className="w-8 h-8 rounded-xl flex items-center justify-center" style={{ background: 'rgba(139,92,246,0.15)' }}>
                  <Lock size={16} className="text-violet-400" />
                </div>
                <span className="text-[11px] font-semibold text-violet-400 uppercase tracking-widest">Premium Feature</span>
              </div>
              <h1 className="text-lg font-bold text-white">Personalized Study Plans</h1>
              <p className="text-sm text-white/45 mt-1">Available on Starter & Pro plans</p>
            </div>
            <div className="px-6 py-5 space-y-3">
              <div className="text-sm text-white/60">{payError}</div>
              <Link to="/pricing"
                className="flex items-center justify-center gap-2 w-full h-11 rounded-xl text-sm font-semibold text-white transition-all hover:opacity-90"
                style={{ background: 'linear-gradient(135deg,#7c3aed 0%,#6d28d9 100%)', boxShadow: '0 4px 20px rgba(124,58,237,0.35)' }}>
                <Zap size={15} /> Upgrade to Starter — ₹99
              </Link>
              <div className="flex justify-center">
                <Link to="/library" className="text-xs text-white/35 hover:text-white/60 flex items-center gap-1 transition-colors">
                  <ArrowLeft size={11} /> Back to Library
                </Link>
              </div>
            </div>
          </div>
        </div>
      </AppLayout>
    );
  }

  if (error === 'forbidden') {
    return (
      <AppLayout>
        <div className="min-h-screen flex flex-col items-center justify-center gap-4 ">
          <Lock size={36} className="text-amber-400" />
          <h1 className="text-xl font-bold text-white">Private Plan</h1>
          <p className="text-white/50 text-sm">This study plan belongs to another account.</p>
          <Link to="/library" className="h-9 px-4 rounded-xl bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium flex items-center gap-2">
            <ArrowLeft size={14} /> Library
          </Link>
        </div>
      </AppLayout>
    );
  }

  if (error === 'not-found') {
    return (
      <AppLayout>
        <div className="min-h-screen flex flex-col items-center justify-center gap-4 ">
          <AlertCircle size={40} className="text-amber-400" />
          <h1 className="text-xl font-bold text-white">Plan Not Found</h1>
          <p className="text-white/50 text-sm">This study plan doesn't exist or was deleted.</p>
          <Link to="/profile" className="h-9 px-4 rounded-xl bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium flex items-center gap-2">
            <ArrowLeft size={14} /> My Plans
          </Link>
        </div>
      </AppLayout>
    );
  }

  if (error) {
    return (
      <AppLayout>
        <div className="min-h-screen flex flex-col items-center justify-center gap-4 ">
          <AlertCircle size={40} className="text-red-400" />
          <p className="text-white/50 text-sm">Failed to load plan. Please try again.</p>
          <button onClick={() => window.location.reload()} className="h-9 px-4 rounded-xl bg-white/10 hover:bg-white/15 text-white text-sm">
            Retry
          </button>
        </div>
      </AppLayout>
    );
  }

  const wordCount = doc?.word_count || doc?.content?.split(/\s+/).length || 0;
  const readMins  = Math.max(1, Math.ceil(wordCount / 200));

  return (
    <AppLayout>
      <Helmet>
        <title>{doc.title} | Syrabit.ai</title>
        <meta name="robots" content="noindex, nofollow" />
      </Helmet>

      <div className="max-w-4xl mx-auto px-3 sm:px-4 py-6 sm:py-8 min-h-screen">
        {/* Breadcrumb */}
        <nav className="flex items-center gap-2 text-xs text-white/40 mb-6">
          <Link to="/profile" className="hover:text-white/70 transition-colors">My Plans</Link>
          <ChevronRight size={12} />
          <span className="text-white/60 truncate max-w-xs">{doc.title}</span>
        </nav>

        {/* Header card */}
        <div className="rounded-2xl p-6 mb-8"
          style={{ border: '1px solid rgba(139,92,246,0.20)', background: 'rgba(124,58,237,0.06)' }}>
          <div className="flex items-center gap-2 mb-3">
            <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: 'rgba(139,92,246,0.15)' }}>
              <Sparkles size={14} className="text-violet-400" />
            </div>
            <span className="text-[11px] font-semibold text-violet-400 uppercase tracking-widest">Your Personalized Plan</span>
          </div>
          <h1 className="text-2xl font-bold text-white mb-3">{doc.title}</h1>
          <div className="flex flex-wrap items-center gap-4 text-xs text-white/40">
            {doc.subject_name && (
              <span className="flex items-center gap-1">
                <BookOpen size={11} /> {doc.subject_name}
              </span>
            )}
            {doc.days && (
              <span className="flex items-center gap-1">
                <Calendar size={11} /> {doc.days}-day sprint
              </span>
            )}
            <span className="flex items-center gap-1">
              <Clock size={11} /> ~{readMins} min read
            </span>
            {doc.created_at && (
              <span className="flex items-center gap-1">
                <FileText size={11} />
                {new Date(doc.created_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}
              </span>
            )}
          </div>
          {doc.weak_topics?.length > 0 && (
            <div className="flex flex-wrap gap-2 mt-4">
              {doc.weak_topics.map(t => (
                <span key={t} className="text-[11px] px-2.5 py-1 rounded-full font-medium"
                  style={{ background: 'rgba(245,158,11,0.12)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.20)' }}>
                  <Target size={9} className="inline mr-1" />{t}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Content */}
        <article
          className="prose prose-invert prose-sm max-w-none px-0 sm:px-0"
          dangerouslySetInnerHTML={{ __html: doc.content_html || `<pre>${doc.content}</pre>` }}
          style={{
            '--tw-prose-headings': '#ffffff',
            '--tw-prose-body': 'rgba(255,255,255,0.75)',
            '--tw-prose-bold': '#ffffff',
            '--tw-prose-code': 'rgba(167,139,250,1)',
            fontSize: '15px',
            lineHeight: '1.75',
          }}
        />

        {/* Bottom nav */}
        <div className="mt-12 pt-6 flex justify-between items-center" style={{ borderTop: '1px solid rgba(255,255,255,0.07)' }}>
          <Link to="/profile" className="text-xs text-white/35 hover:text-white/60 flex items-center gap-1.5 transition-colors">
            <ArrowLeft size={12} /> All My Plans
          </Link>
          {/* Task #409 — Ask AI about this plan must arrive at /chat
              with the plan content already in card_context so the
              backend's library-branch grounding (rag.py
              build_rag_system_prompt) can prioritise it over generic
              curriculum knowledge. The plan summary is passed via
              react-router Link state (not a query param) so a 2k+
              character plan body doesn't blow the URL length cap or
              leak into browser history; ChatPage reads it once on
              mount via useLocation().state.seedCardContext. The
              ?subject= param is kept so the existing subject pill
              and chapter syllabus loader still fire when the plan
              is tied to a real subject. */}
          <Link
            to={doc.subject_id ? `/chat?subject=${encodeURIComponent(doc.subject_id)}` : '/chat'}
            state={{ seedCardContext: buildPlanSeedContext(doc) }}
            data-testid="cms-ask-ai-link"
            className="flex items-center gap-2 h-9 px-4 rounded-xl text-xs font-semibold text-white hover:opacity-90 transition-all"
            style={{ background: 'linear-gradient(135deg,#7c3aed,#6d28d9)' }}>
            <Sparkles size={13} /> Ask AI about this plan
          </Link>
        </div>
      </div>
    </AppLayout>
  );
}
