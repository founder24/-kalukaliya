import { useEffect, useState } from 'react';
import { Languages, CheckCircle, AlertTriangle, XCircle, KeyRound, Loader2 } from 'lucide-react';

const STATUS_STYLE = {
  healthy:        { bg: '#ecfdf5', fg: '#047857', border: '#a7f3d0', label: 'HEALTHY',        Icon: CheckCircle },
  degraded:       { bg: '#fffbeb', fg: '#b45309', border: '#fde68a', label: 'DEGRADED',       Icon: AlertTriangle },
  down:           { bg: '#fef2f2', fg: '#b91c1c', border: '#fecaca', label: 'DOWN',           Icon: XCircle },
  not_configured: { bg: '#f3f4f6', fg: '#4b5563', border: '#d1d5db', label: 'NOT CONFIGURED', Icon: KeyRound },
};

function pct(v) { return `${(v * 100).toFixed(1)}%`; }
function money(v) { return `$${Number(v || 0).toFixed(4)}`; }

export default function SarvamHealthCard({ token }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const r = await fetch('/api/admin/health/sarvam', {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (!cancelled) { setData(j); setErr(null); }
      } catch (e) {
        if (!cancelled) setErr(String(e));
      }
    }
    load();
    const id = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [token]);

  if (err) {
    return (
      <div style={{ padding: 16, background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 12, color: '#991b1b', fontSize: 13 }}>
        Failed to load Sarvam health: {err}
      </div>
    );
  }
  if (!data) {
    return (
      <div style={{ padding: 16, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Loader2 size={14} className="animate-spin" />
        <span style={{ fontSize: 13, color: '#6b7280' }}>Loading Sarvam status…</span>
      </div>
    );
  }

  const style = STATUS_STYLE[data.status] || STATUS_STYLE.degraded;
  const { Icon } = style;
  const floorPct = (data.alert_floor * 100).toFixed(0);

  return (
    <div style={{ padding: 16, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <Languages size={16} color="#7c3aed" />
        <div style={{ fontSize: 13, fontWeight: 700, color: '#111827', flex: 1 }}>
          Sarvam — Assamese chat (sarvam-m)
        </div>
        <span style={{
          fontSize: 10, fontWeight: 700, color: style.fg, background: style.bg,
          border: `1px solid ${style.border}`, borderRadius: 6, padding: '2px 8px',
          display: 'inline-flex', alignItems: 'center', gap: 4,
        }}>
          <Icon size={11} />
          {style.label}
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 8 }}>
        <Stat label="Success / 5m"    value={pct(data.success_rate_5m)} sub={`${data.total_5m} calls`} tone={data.success_rate_5m < data.alert_floor ? 'warn' : 'ok'} />
        <Stat label="Success / 1h"    value={pct(data.success_rate)} sub={`${data.total} calls`} tone={data.alert ? 'warn' : 'ok'} />
        <Stat label="p50 / p95"       value={`${Math.round(data.p50_latency_ms)} / ${Math.round(data.p95_latency_ms)} ms`} />
        <Stat label="Cost / 24h"      value={money(data.cost_usd_24h)} sub={`${data.tokens_24h.toLocaleString()} tok`} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8, marginBottom: 12 }}>
        <Stat label="Per-user cap"  value={data.per_user_monthly_cap > 0 ? `${data.per_user_monthly_cap}/mo` : 'off'} />
        <Stat label="Last error"    value={data.last_error ? data.last_error.slice(0, 40) : '—'} tone={data.last_error ? 'warn' : 'ok'} />
      </div>

      <div style={{ fontSize: 11, color: '#6b7280', lineHeight: 1.6 }}>
        Role: <strong>{data.role}</strong> in <code>assamese_rag_chat</code> chain
        → fallback <code>{data.fallback}</code>.
        {' '}Sentry alert fires when 1h success rate &lt; {floorPct}%
        (with ≥ {data.min_samples} samples).
      </div>
      {data.error && (
        <div style={{ marginTop: 8, fontSize: 11, color: '#b91c1c' }}>
          {data.error}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, sub, tone }) {
  const fg = tone === 'warn' ? '#b45309' : tone === 'ok' ? '#047857' : '#111827';
  return (
    <div style={{ background: '#f9fafb', border: '1px solid #f3f4f6', borderRadius: 8, padding: '8px 10px' }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 700, color: fg }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: '#9ca3af', marginTop: 2 }}>{sub}</div>}
    </div>
  );
}
