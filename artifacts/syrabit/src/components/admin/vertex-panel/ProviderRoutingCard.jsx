import { useEffect, useState } from 'react';
import { Network, Loader2, CheckCircle, AlertCircle, Lock, Repeat, ArrowDown, KeyRound } from 'lucide-react';
import { vertexProviderRouting } from '@/utils/api';
import { card, Badge } from './shared';

const ROLE_STYLE = {
  primary:     { bg: 'rgba(16,185,129,0.10)',  border: '1px solid rgba(16,185,129,0.35)',  fg: '#059669', label: 'PRIMARY',     icon: Lock },
  rotation:    { bg: 'rgba(59,130,246,0.10)',  border: '1px solid rgba(59,130,246,0.35)',  fg: '#2563eb', label: 'ROTATION',    icon: Repeat },
  fallback:    { bg: 'rgba(245,158,11,0.10)',  border: '1px solid rgba(245,158,11,0.35)',  fg: '#b45309', label: 'FALLBACK',    icon: ArrowDown },
  last_resort: { bg: 'rgba(107,114,128,0.10)', border: '1px solid rgba(107,114,128,0.35)', fg: '#4b5563', label: 'LAST RESORT', icon: ArrowDown },
};

function ProviderRow({ p }) {
  const style = ROLE_STYLE[p.role] || ROLE_STYLE.fallback;
  const RoleIcon = style.icon;
  const missing = p.missing_env_keys || [];
  const missingLabel = missing.length
    ? (missing.length === 1
        ? `Set ${missing[0]} in Replit Secrets to enable this provider.`
        : `Set ANY ONE of these in Replit Secrets to enable this provider: ${missing.join(', ')}.`)
    : '';
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1.5fr 1fr 90px 70px 90px',
      alignItems: 'center', gap: 10, padding: '8px 12px',
      background: style.bg, border: style.border, borderRadius: 10, marginBottom: 6,
    }}>
      <div className="flex items-center gap-2 min-w-0">
        {p.enabled
          ? <CheckCircle size={13} color="#10b981" />
          : <AlertCircle size={13} color="#ef4444" />}
        <span style={{ fontSize: 13, fontWeight: 700, color: '#111827' }}>{p.label}</span>
        <span style={{ fontSize: 10, color: '#9ca3af', fontFamily: 'monospace' }}>{p.name}</span>
        {!p.enabled && missing.length > 0 && (
          <span title={missingLabel} style={{ fontSize: 10, color: '#b91c1c', background: '#fee2e2', border: '1px solid #fecaca', borderRadius: 6, padding: '1px 6px', fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 320 }}>
            {missing.length === 1 ? `need ${missing[0]}` : `need any of: ${missing.join(' | ')}`}
          </span>
        )}
      </div>
      <code style={{ fontSize: 11, color: '#374151', fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {p.model || '—'}
      </code>
      <span style={{ fontSize: 11, color: '#6b7280', textAlign: 'right', fontFamily: 'monospace' }}>
        w={p.weight.toLocaleString()}
      </span>
      <span style={{ fontSize: 11, color: '#6b7280', textAlign: 'right' }}>
        {p.credits_usd ? `$${p.credits_usd.toLocaleString()}` : '—'}
      </span>
      <div className="flex items-center justify-end gap-1" style={{ color: style.fg, fontSize: 10, fontWeight: 800, letterSpacing: 0.4 }}>
        <RoleIcon size={11} />
        {style.label}
      </div>
    </div>
  );
}

function FeatureBlock({ f }) {
  return (
    <div style={{ marginBottom: 18, padding: 14, background: '#ffffff', border: '1px solid #e5e7eb', borderRadius: 12 }}>
      <div className="flex items-center gap-2 mb-2">
        <code style={{ fontSize: 11, color: '#6b7280', fontFamily: 'monospace', background: '#f3f4f6', padding: '1px 6px', borderRadius: 4 }}>{f.key}</code>
        <span style={{ fontSize: 14, fontWeight: 700, color: '#111827' }}>{f.label}</span>
        {f.strict_lock && <Badge label="🔒 Strict Lock" color="#10b981" />}
        {!f.strict_lock && <Badge label="⇄ Rotation" color="#3b82f6" />}
      </div>
      {f.description && (
        <p style={{ fontSize: 12, color: '#6b7280', margin: '0 0 10px 0', lineHeight: 1.5 }}>{f.description}</p>
      )}
      <div style={{
        display: 'grid', gridTemplateColumns: '1.5fr 1fr 90px 70px 90px',
        gap: 10, padding: '4px 12px', fontSize: 10, fontWeight: 700, color: '#9ca3af',
        textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4,
      }}>
        <span>Provider</span>
        <span>Model</span>
        <span style={{ textAlign: 'right' }}>Weight</span>
        <span style={{ textAlign: 'right' }}>Credits</span>
        <span style={{ textAlign: 'right' }}>Role</span>
      </div>
      {f.providers.map(p => <ProviderRow key={p.name} p={p} />)}
    </div>
  );
}

export default function ProviderRoutingCard({ token }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    vertexProviderRouting(token)
      .then(r => setData(r.data))
      .catch(e => setError(e?.response?.data?.detail || e.message || 'Failed to load routing'))
      .finally(() => setLoading(false));
  }, [token]);

  if (loading) {
    return (
      <div style={card}>
        <div className="flex items-center gap-2"><Loader2 size={16} className="animate-spin" color="#8b5cf6" /> <span style={{ fontSize: 13, color: '#6b7280' }}>Loading provider routing matrix…</span></div>
      </div>
    );
  }
  if (error) {
    return (
      <div style={card}>
        <div className="flex items-center gap-2 mb-2"><AlertCircle size={16} color="#ef4444" /> <span style={{ fontSize: 13, fontWeight: 700, color: '#ef4444' }}>Could not load routing matrix</span></div>
        <code style={{ fontSize: 11, color: '#6b7280' }}>{error}</code>
      </div>
    );
  }
  if (!data) return null;

  const allProviders = new Set();
  let missingCount = 0;
  data.features.forEach(f => f.providers.forEach(p => {
    allProviders.add(p.name);
    if (!p.enabled) missingCount++;
  }));

  return (
    <div style={card}>
      <div className="flex items-center gap-2 mb-2">
        <Network size={16} color="#8b5cf6" />
        <span style={{ fontWeight: 700, color: '#111827' }}>Provider Routing Matrix</span>
        <Badge label={`${data.features.length} features`} color="#8b5cf6" />
        <Badge label={`${allProviders.size} providers`} color="#3b82f6" />
        {missingCount > 0 && <Badge label={`${missingCount} unconfigured`} color="#ef4444" />}
      </div>
      <p style={{ fontSize: 12, color: '#6b7280', marginBottom: 14, lineHeight: 1.6 }}>
        Live view of <code style={{ fontFamily: 'monospace', background: '#f3f4f6', padding: '1px 5px', borderRadius: 4 }}>PROVIDER_PRIORITY</code> + <code style={{ fontFamily: 'monospace', background: '#f3f4f6', padding: '1px 5px', borderRadius: 4 }}>POOL_WEIGHTS</code> from <code style={{ fontFamily: 'monospace', background: '#f3f4f6', padding: '1px 5px', borderRadius: 4 }}>config.py</code> — what <code style={{ fontFamily: 'monospace', background: '#f3f4f6', padding: '1px 5px', borderRadius: 4 }}>select_provider()</code> will actually choose for each feature right now. Green = primary, blue = rotation peer, amber = fallback, grey = weight-0 last-resort. Red dot = the provider's env credentials are not configured.
      </p>

      <div style={{ marginBottom: 14, padding: 10, background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, fontSize: 11, color: '#4b5563', lineHeight: 1.7 }}>
        <div className="flex items-center gap-1 mb-1" style={{ color: '#374151', fontWeight: 700 }}><Lock size={11} /> Strict lock</div>
        {data.notes.strict_lock}
        <div className="flex items-center gap-1 mt-2 mb-1" style={{ color: '#374151', fontWeight: 700 }}><Repeat size={11} /> Rotation</div>
        {data.notes.rotation}
        <div className="flex items-center gap-1 mt-2 mb-1" style={{ color: '#374151', fontWeight: 700 }}><ArrowDown size={11} /> Last resort</div>
        {data.notes.last_resort}
      </div>

      {data.features.map(f => <FeatureBlock key={f.key} f={f} />)}

      {missingCount > 0 && (
        <div style={{ marginTop: 6, padding: 12, background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.25)', borderRadius: 10, fontSize: 12, color: '#7f1d1d', lineHeight: 1.6 }}>
          <div className="flex items-center gap-2 mb-1" style={{ color: '#b91c1c', fontWeight: 700 }}>
            <KeyRound size={13} /> Some providers have no credentials configured
          </div>
          Open <strong>API Config</strong> in the sidebar to set the missing keys. Until then, <code style={{ fontFamily: 'monospace', background: '#fee2e2', padding: '1px 5px', borderRadius: 4 }}>select_provider()</code> will skip those providers and the next eligible row in each chain handles the request.
        </div>
      )}
    </div>
  );
}
