import { Loader2, RefreshCw, Sparkles, CheckCheck } from 'lucide-react';
import InsightCard from './InsightCard';

const PAGE_TYPES = [
  { id: 'notes',               label: 'Notes',               color: '#7c3aed' },
  { id: 'definition',          label: 'Definitions',         color: '#0891b2' },
  { id: 'important-questions', label: 'Important Questions', color: '#d97706' },
  { id: 'mcqs',                label: 'MCQs',                color: '#16a34a' },
  { id: 'examples',            label: 'Examples',            color: '#e11d48' },
];

export default function InsightsTab({
  insights, insightsLoading, loadInsights,
  handleInsightAction, actionLoading,
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold" style={{ color: '#374151' }}>AI Gap Analysis</p>
          <p className="text-xs mt-0.5" style={{ color: '#9ca3af' }}>Actionable insights based on your current topic and page coverage</p>
        </div>
        <button onClick={loadInsights} disabled={insightsLoading}
          className="h-8 px-3 rounded-lg text-xs flex items-center gap-1.5 border disabled:opacity-50"
          style={{ color: '#6b7280', borderColor: '#e5e7eb' }}>
          <RefreshCw size={12} className={insightsLoading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {insightsLoading ? (
        <div className="space-y-3">{[...Array(4)].map((_, i) => <div key={i} className="h-24 rounded-xl animate-pulse" style={{ background: '#f9fafb' }} />)}</div>
      ) : !insights ? (
        <div className="rounded-xl p-10 text-center border" style={{ background: '#ffffff', borderColor: '#e5e7eb' }}>
          <Sparkles size={28} className="mx-auto mb-3" style={{ color: '#e5e7eb' }} />
          <p className="text-sm mb-4" style={{ color: '#9ca3af' }}>Click Refresh to generate gap analysis</p>
          <button onClick={loadInsights} className="h-9 px-4 rounded-xl text-xs font-semibold mx-auto flex items-center gap-2"
            style={{ background: '#7c3aed', color: '#fff' }}>
            <Sparkles size={13} /> Analyse Gaps
          </button>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-xl p-3 border text-center" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
              <p className="text-xl font-bold" style={{ color: '#374151' }}>{insights.summary?.total_topics ?? 0}</p>
              <p className="text-[11px] mt-0.5" style={{ color: '#9ca3af' }}>Total Topics</p>
            </div>
            <div className="rounded-xl p-3 border text-center" style={{ background: 'rgba(239,68,68,0.06)', borderColor: 'rgba(239,68,68,0.18)' }}>
              <p className="text-xl font-bold" style={{ color: '#f87171' }}>{insights.summary?.topics_with_no_pages ?? 0}</p>
              <p className="text-[11px] mt-0.5" style={{ color: '#9ca3af' }}>No pages yet</p>
            </div>
            <div className="rounded-xl p-3 border text-center" style={{ background: 'rgba(124,58,237,0.06)', borderColor: 'rgba(124,58,237,0.18)' }}>
              <p className="text-xl font-bold" style={{ color: '#a78bfa' }}>
                {Object.values(insights.summary?.page_type_gaps || {}).reduce((a, b) => a + b, 0)}
              </p>
              <p className="text-[11px] mt-0.5" style={{ color: '#9ca3af' }}>Total gaps</p>
            </div>
          </div>

          {insights.insights?.length > 0 ? (
            <div className="space-y-2.5">
              {insights.insights.map((insight, i) => (
                <InsightCard key={i} insight={insight}
                  onAction={handleInsightAction}
                  loading={actionLoading === insight.title} />
              ))}
            </div>
          ) : (
            <div className="rounded-xl p-6 text-center border" style={{ background: 'rgba(16,185,129,0.05)', borderColor: 'rgba(16,185,129,0.15)' }}>
              <CheckCheck size={24} className="mx-auto mb-2" style={{ color: '#34d399' }} />
              <p className="text-sm font-semibold" style={{ color: '#34d399' }}>Full coverage!</p>
              <p className="text-xs mt-1" style={{ color: '#9ca3af' }}>All topics have all page types. Nothing to fill.</p>
            </div>
          )}

          {insights.subject_breakdown?.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: '#9ca3af' }}>Subject Breakdown</p>
              <div className="space-y-2">
                {insights.subject_breakdown.map((s, i) => (
                  <div key={i} className="rounded-xl p-3 border" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium" style={{ color: '#374151' }}>{s.subject}</span>
                      <span className="text-[10px]" style={{ color: '#9ca3af' }}>{s.board} · {s.class}</span>
                    </div>
                    <div className="flex gap-1.5 flex-wrap">
                      {PAGE_TYPES.map(pt => (
                        <span key={pt.id} className="text-[10px] px-2 py-0.5 rounded-full"
                          style={{
                            background: s[pt.id] > 0 ? `${pt.color}20` : '#f9fafb',
                            color: s[pt.id] > 0 ? pt.color : '#d1d5db',
                            border: `1px solid ${s[pt.id] > 0 ? pt.color + '40' : '#e5e7eb'}`,
                          }}>
                          {pt.label.split(' ')[0]} {s[pt.id] > 0 ? `×${s[pt.id]}` : '—'}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
