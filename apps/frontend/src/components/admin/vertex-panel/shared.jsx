export function readHubCtx() {
  try {
    const raw = localStorage.getItem('syrabit_hub_ctx');
    if (!raw) return null;
    const ctx = JSON.parse(raw);
    if (Date.now() - (ctx._ts || 0) > 2 * 60 * 60 * 1000) return null;
    return ctx;
  } catch { return null; }
}

export const card = {
  background: '#f9fafb',
  border: '1px solid #e5e7eb',
  borderRadius: 16,
  padding: 20,
};

export const btn = (color = '#8b5cf6') => ({
  background: `linear-gradient(135deg, ${color}22, ${color}11)`,
  border: `1px solid ${color}44`,
  color,
  borderRadius: 10,
  padding: '8px 16px',
  fontSize: 13,
  fontWeight: 600,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  transition: 'all 0.15s',
});

export function Badge({ label, color = '#8b5cf6' }) {
  return (
    <span style={{ background: `${color}22`, color, border: `1px solid ${color}44`, borderRadius: 20, padding: '2px 10px', fontSize: 11, fontWeight: 700 }}>
      {label}
    </span>
  );
}

export function ScoreBar({ label, value }) {
  const pct = Math.round((value / 10) * 100);
  const color = value >= 8 ? '#10b981' : value >= 6 ? '#f59e0b' : '#ef4444';
  return (
    <div className="flex items-center gap-3 mb-1">
      <span style={{ width: 130, fontSize: 12, color: '#6b7280' }}>{label}</span>
      <div style={{ flex: 1, height: 6, background: '#e5e7eb', borderRadius: 4, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 4, transition: 'width 0.4s' }} />
      </div>
      <span style={{ width: 24, fontSize: 12, fontWeight: 700, color }}>{value}</span>
    </div>
  );
}
