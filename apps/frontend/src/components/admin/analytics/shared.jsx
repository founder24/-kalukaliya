import { RefreshCw, ArrowUpRight, ArrowDownRight, AlertTriangle, Flame } from 'lucide-react';

export const TT = {
  contentStyle: {
    background: '#ffffff',
    border: '1px solid #e5e7eb',
    borderRadius: '10px',
    color: '#374151',
    fontSize: 12,
    boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
  },
  cursor: false,
};

export const PLAN_COLORS = { free: '#6b7280', starter: '#7c3aed', pro: '#10b981' };
export const FUNNEL_COLORS = ['#3b82f6', '#8b5cf6', '#10b981'];

export const fmt = (d) => d?.slice(5) ?? d;
export const fmtInr = (n) => n >= 100000 ? `₹${(n / 100000).toFixed(1)}L` : n >= 1000 ? `₹${(n / 1000).toFixed(1)}k` : `₹${n}`;

function GlassCard({ children, className = '', glow }) {
  return (
    <div className={`relative rounded-2xl overflow-hidden bg-white border border-gray-200 shadow-sm ${className}`}>
      {glow && (
        <div className="absolute inset-0 pointer-events-none" style={{
          background: `radial-gradient(ellipse at top left, ${glow}08, transparent 60%)`,
        }} />
      )}
      <div className="relative">{children}</div>
    </div>
  );
}

export function Card({ title, children, empty, emptyMsg, action, error, onRetry }) {
  return (
    <div className="relative rounded-2xl overflow-hidden p-5 bg-white border border-gray-200 shadow-sm">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-gray-500 text-sm font-medium">{title}</h3>
        <div className="flex items-center gap-2">
          {error && onRetry && (
            <button onClick={onRetry} className="text-xs text-amber-700 hover:text-amber-700 px-2 py-0.5 rounded-lg transition-colors flex items-center gap-1 bg-amber-50 border border-amber-200">
              <RefreshCw size={10} /> Retry
            </button>
          )}
          {action}
        </div>
      </div>
      {error
        ? (
          <div className="flex items-center gap-2 py-6 justify-center">
            <AlertTriangle size={14} className="text-amber-500" />
            <p className="text-amber-700 text-sm">Failed to load — data unavailable</p>
          </div>
        )
        : empty
          ? <p className="text-gray-600 text-sm text-center py-6">{emptyMsg || 'No data yet'}</p>
          : children}
    </div>
  );
}

export function Stat({ icon: Icon, label, value, color, sub, trend }) {
  const up = trend > 0;
  return (
    <div className="flex items-center gap-3 p-3.5 rounded-xl group transition-all duration-300 relative overflow-hidden bg-white border border-gray-200 shadow-sm">
      <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: `${color}15` }}>
        <Icon size={15} style={{ color }} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-gray-900 font-bold text-lg leading-none truncate">{value ?? '—'}</p>
        <p className="text-gray-500 text-xs mt-0.5">{label}</p>
        {sub && <p className="text-gray-600 text-[10px] mt-0.5">{sub}</p>}
      </div>
      {trend !== undefined && (
        <div className={`flex items-center gap-0.5 text-xs font-semibold flex-shrink-0 ${up ? 'text-emerald-600' : 'text-red-500'}`}>
          {up ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}
          {Math.abs(trend)}%
        </div>
      )}
    </div>
  );
}

export function InsightBar({ label, value, max, color }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  const heat = pct > 70 ? '#ef4444' : pct > 40 ? '#f59e0b' : '#3b82f6';
  const c = color || heat;
  return (
    <div className="flex items-center gap-2">
      <Flame size={11} style={{ color: c }} className="flex-shrink-0" />
      <span className="text-gray-700 text-sm flex-1 truncate">{label}</span>
      <div className="w-20 h-2 rounded-full overflow-hidden flex-shrink-0 bg-gray-100">
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: c }} />
      </div>
      <span className="text-gray-600 text-xs w-8 text-right flex-shrink-0">{value}</span>
    </div>
  );
}
