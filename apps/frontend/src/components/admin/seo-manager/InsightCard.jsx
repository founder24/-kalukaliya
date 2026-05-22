import { Loader2, Zap } from 'lucide-react';

export default function InsightCard({ insight, onAction, loading }) {
  const colors = {
    critical: { bg: 'rgba(239,68,68,0.07)', border: 'rgba(239,68,68,0.22)', badge: '#f87171', badgeBg: 'rgba(239,68,68,0.15)' },
    gap:      { bg: 'rgba(124,58,237,0.06)', border: 'rgba(124,58,237,0.22)', badge: '#a78bfa', badgeBg: 'rgba(139,92,246,0.15)' },
    info:     { bg: '#f9fafb', border: '#e5e7eb', badge: '#94a3b8', badgeBg: '#e5e7eb' },
  };
  const c = colors[insight.type] || colors.info;
  return (
    <div className="rounded-xl p-4 border" style={{ background: c.bg, borderColor: c.border }}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: c.badgeBg, color: c.badge }}>
              {insight.count} pages
            </span>
            {insight.page_type && (
              <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: '#e5e7eb', color: '#9ca3af' }}>
                {insight.page_type}
              </span>
            )}
          </div>
          <p className="text-sm font-semibold mb-1" style={{ color: '#374151' }}>{insight.title}</p>
          <p className="text-xs leading-relaxed" style={{ color: '#9ca3af' }}>{insight.description}</p>
        </div>
        <button
          onClick={() => onAction(insight)}
          disabled={loading}
          className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-50"
          style={{ background: 'linear-gradient(135deg,#7c3aed,#6d28d9)', color: '#fff' }}>
          {loading ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />}
          {insight.action === 'auto-run' ? 'Auto-Run' : 'Generate'}
        </button>
      </div>
    </div>
  );
}
