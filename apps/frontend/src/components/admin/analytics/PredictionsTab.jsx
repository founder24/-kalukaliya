import { DollarSign, TrendingUp, Target, Users, Zap, FileText,
  ArrowUpRight, AlertTriangle } from 'lucide-react';
import { Card, Stat, fmtInr } from './shared';

export default function PredictionsTab({ widgetErrors, load, mrr, predicted, growth, aiInsight, topSubject, predict }) {
  return (
    <div className="space-y-4">
      {widgetErrors.predictions && (
        <div className="flex items-center gap-3 p-3.5 rounded-xl" style={{
          background: 'rgba(245,158,11,0.06)',
          border: '1px solid rgba(245,158,11,0.15)',
        }}>
          <AlertTriangle size={14} className="text-amber-700 flex-shrink-0" />
          <p className="text-xs text-amber-700/80 flex-1">Predictions data failed to load — showing estimates only.</p>
          <button onClick={() => load(true)} className="text-xs text-amber-700 hover:text-gray-900 px-2.5 py-1 rounded-lg transition-colors"
            style={{ background: 'rgba(245,158,11,0.12)' }}>Retry</button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        {[
          { label: 'Current MRR',       value: fmtInr(mrr),       sub: 'last 30 days',          color: '#10b981', icon: DollarSign },
          { label: 'Predicted MRR',     value: fmtInr(predicted), sub: `${growth}% MoM rate`,   color: '#7c3aed', icon: TrendingUp },
          { label: 'Path to ₹1L MRR',
            value: mrr > 0 ? `${Math.max(0, Math.ceil(Math.log(100000 / mrr) / Math.log(1 + Math.max(growth, 1) / 100)))} mo` : '—',
            sub: 'at current growth',  color: '#f59e0b', icon: Target },
        ].map((item, i) => (
          <Stat key={i} icon={item.icon} label={item.label} value={item.value} color={item.color} sub={item.sub} />
        ))}
      </div>

      <Card title="Content Scale → Revenue Model">
        <div className="space-y-3">
          {[
            { pages: 100,   est: '₹2–5k',  label: 'Seed phase — 100 SEO pages' },
            { pages: 1000,  est: '₹15–30k', label: 'Growth phase — 1k SEO pages' },
            { pages: 5000,  est: '₹60–90k', label: 'Scale phase — 5k SEO pages' },
            { pages: 10000, est: '₹1–1.5L', label: '₹1Cr MRR target — 10k SEO pages' },
          ].map((row, i) => (
            <div key={i} className="flex items-center gap-3 p-3.5 rounded-xl transition-all duration-300 group" style={{
              background: '#f9fafb',
              border: '1px solid #f9fafb',
            }}>
              <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: 'rgba(139,92,246,0.12)' }}>
                <FileText size={13} className="text-violet-600" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-gray-600 text-sm font-medium">{row.label}</p>
                <p className="text-gray-700 text-xs">{row.pages.toLocaleString()} pages × ~200 organic visits/mo × 2% conversion</p>
              </div>
              <span className="text-emerald-600 font-bold text-sm flex-shrink-0">{row.est}/mo</span>
            </div>
          ))}
        </div>
      </Card>

      {aiInsight && (
        <Card title="AI Content Gap Insight">
          <div className="flex items-start gap-3 p-3.5 rounded-xl" style={{
            background: 'rgba(139,92,246,0.06)',
            border: '1px solid rgba(139,92,246,0.12)',
          }}>
            <Zap size={15} className="text-violet-600 flex-shrink-0 mt-0.5" />
            <p className="text-gray-500 text-sm leading-relaxed">{aiInsight}</p>
          </div>
          {topSubject && (
            <div className="mt-4 space-y-2">
              <p className="text-gray-700 text-xs font-medium uppercase tracking-wide">Revenue opportunity</p>
              {['MCQ Practice', 'Important Questions', 'Notes', 'Definitions'].map((pt, i) => (
                <div key={i} className="flex items-center justify-between p-2.5 rounded-xl" style={{
                  background: '#f9fafb',
                }}>
                  <span className="text-gray-500 text-sm">{topSubject.name} — {pt}</span>
                  <span className="text-xs text-emerald-600 font-medium">+{[120, 95, 80, 60][i]} organic/mo est.</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      <Card title="Signup Velocity">
        <div className="grid grid-cols-2 gap-3">
          <Stat icon={Users}      label="Signups this month" value={predict?.signups_this_month || 0} color="#7c3aed" />
          <Stat icon={ArrowUpRight} label="vs last month"    value={predict?.signups_last_month  || 0} color="#64748b"
            trend={predict?.signups_last_month > 0
              ? Math.round(((predict.signups_this_month - predict.signups_last_month) / predict.signups_last_month) * 100)
              : undefined} />
        </div>
      </Card>
    </div>
  );
}
