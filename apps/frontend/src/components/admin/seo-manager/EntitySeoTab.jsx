/**
 * Task #940 — admin "Entity SEO" panel.
 *
 * Surfaces the latest weekly snapshot from the backend
 * `entity_seo_health` worker (Wikidata + Wikipedia + Crunchbase +
 * sameAs + Google Knowledge Graph), plus week-over-week deltas, the
 * list of missing Wikidata claims with one-click deep-links to the
 * Wikidata edit page, and a "refresh now" button that re-probes
 * outside the Mon 04:30 UTC schedule when an admin has just filed a
 * claim and wants to confirm it landed.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Loader2, RefreshCw, AlertTriangle, CheckCircle2,
  ExternalLink, Globe, Activity, Award, Link as LinkIcon,
  MessageSquare,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  adminEntitySeoStatus,
  adminEntitySeoRefresh,
} from '@/utils/api';

const SIGNAL_LABELS = {
  wikidata:   { label: 'Wikidata',           icon: Award },
  wikipedia:  { label: 'Wikipedia',          icon: Globe },
  crunchbase: { label: 'Crunchbase',         icon: Activity },
  sameas:     { label: 'sameAs profiles',    icon: LinkIcon },
  google_kg:  { label: 'Knowledge Graph',    icon: Award },
  mentions:   { label: 'Mention Opportunities', icon: MessageSquare },
};

const STATUS_COLORS = {
  ok:        { color: '#10b981', bg: '#ecfdf5', label: 'Healthy' },
  missing:   { color: '#f59e0b', bg: '#fffbeb', label: 'Missing' },
  error:     { color: '#ef4444', bg: '#fef2f2', label: 'Error' },
  degraded:  { color: '#ef4444', bg: '#fef2f2', label: 'Degraded' },
};

function fmt(dt) {
  if (!dt) return '—';
  try { return new Date(dt).toLocaleString(); } catch { return dt; }
}

function StatusPill({ status }) {
  const cfg = STATUS_COLORS[status] || { color: '#6b7280', bg: '#f3f4f6', label: status || '—' };
  return (
    <span
      className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border"
      style={{ color: cfg.color, background: cfg.bg, borderColor: cfg.color + '33' }}
      data-testid={`entity-status-pill-${status}`}
    >
      {cfg.label}
    </span>
  );
}

function DeltaCell({ label, current, previous, delta, polarity = 'higher_is_better' }) {
  const sign = delta > 0 ? '+' : '';
  // For "missing" / "broken" counters, lower is better — flip the colour.
  const positive = polarity === 'higher_is_better' ? delta > 0 : delta < 0;
  const negative = polarity === 'higher_is_better' ? delta < 0 : delta > 0;
  const color = positive ? '#10b981' : negative ? '#ef4444' : '#6b7280';
  return (
    <div className="flex items-baseline gap-2 text-[11px]">
      <span className="text-gray-500 uppercase tracking-wide">{label}</span>
      <span className="text-sm font-bold tabular-nums text-gray-900">{current ?? 0}</span>
      <span className="text-gray-400 tabular-nums">prev {previous ?? 0}</span>
      {delta !== 0 && delta != null && (
        <span style={{ color }} className="text-[11px] font-bold tabular-nums">
          {sign}{delta}
        </span>
      )}
    </div>
  );
}

function SignalCard({ name, signal }) {
  const cfg = SIGNAL_LABELS[name] || { label: name, icon: Globe };
  const Icon = cfg.icon;
  const status = signal?.status || 'error';
  const fields = signal?.fields || {};
  const linkUrl = fields.edit_url || fields.page_url || fields.url || fields.draft_url
    || fields.submit_url;
  return (
    <div
      className="rounded-xl border p-3 bg-white"
      style={{ borderColor: '#e5e7eb' }}
      data-testid={`entity-signal-${name}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <Icon size={14} className="text-gray-500" />
          <span className="text-xs font-semibold text-gray-900">{cfg.label}</span>
        </div>
        <StatusPill status={status} />
      </div>
      <p className="text-[11px] text-gray-500 mt-1.5 leading-snug">
        {signal?.summary || 'No data.'}
      </p>
      {name === 'wikidata' && (
        <div className="text-[11px] text-gray-400 mt-1">
          Claims filed: <span className="tabular-nums text-gray-700 font-medium">{fields.claim_count ?? 0}</span>
          {fields.qid && <> · QID <span className="font-mono text-gray-700">{fields.qid}</span></>}
        </div>
      )}
      {name === 'sameas' && (
        <div className="text-[11px] text-gray-400 mt-1">
          {fields.total ?? 0} profiles probed ·{' '}
          <span className="tabular-nums text-gray-700 font-medium">
            {(fields.broken || []).length}
          </span>{' '}
          broken
        </div>
      )}
      {name === 'crunchbase' && fields.completeness_pct != null && (
        <div className="text-[11px] text-gray-400 mt-1">
          Field completeness:{' '}
          <span className="tabular-nums text-gray-700 font-medium">{fields.completeness_pct}%</span>
        </div>
      )}
      {linkUrl && (
        <a
          href={linkUrl} target="_blank" rel="noreferrer"
          className="inline-flex items-center gap-1 text-[11px] mt-2 text-violet-600 hover:underline"
        >
          Open <ExternalLink size={11} />
        </a>
      )}
    </div>
  );
}

function MissingClaimsList({ claims }) {
  if (!claims || claims.length === 0) {
    return (
      <div
        className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 flex items-center gap-2"
        data-testid="entity-missing-claims-empty"
      >
        <CheckCircle2 size={14} className="text-emerald-600" />
        <span className="text-[12px] text-emerald-700 font-medium">
          All tracked Wikidata claims are present.
        </span>
      </div>
    );
  }
  return (
    <div
      className="rounded-xl border border-amber-200 bg-amber-50/60 overflow-hidden"
      data-testid="entity-missing-claims"
    >
      <div className="px-3 py-2 border-b border-amber-200 flex items-center gap-2">
        <AlertTriangle size={14} className="text-amber-600" />
        <span className="text-[12px] font-semibold text-amber-800">
          {claims.length} Wikidata claim{claims.length === 1 ? '' : 's'} to file
        </span>
      </div>
      <ul className="divide-y divide-amber-100">
        {claims.map((c) => (
          <li key={c.prop} className="px-3 py-2 flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[12px] font-semibold text-gray-900">
                <span className="font-mono text-gray-500 mr-1.5">{c.prop}</span>
                {c.label}
              </div>
              {c.expected && (
                <div className="text-[11px] text-gray-500 mt-0.5">
                  Expected value: <span className="font-mono">{c.expected}</span>
                </div>
              )}
            </div>
            <a
              href={c.edit_url} target="_blank" rel="noreferrer"
              className="shrink-0 inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-md border border-amber-300 text-amber-800 hover:bg-amber-100"
              data-testid={`entity-missing-claim-${c.prop}`}
            >
              File on Wikidata <ExternalLink size={11} />
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MissingMentionsList({ mentions }) {
  if (!mentions || mentions.length === 0) {
    return (
      <div
        className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 flex items-center gap-2"
        data-testid="entity-missing-mentions-empty"
      >
        <CheckCircle2 size={14} className="text-emerald-600" />
        <span className="text-[12px] text-emerald-700 font-medium">
          All tracked mention opportunities already cover us.
        </span>
      </div>
    );
  }
  return (
    <div
      className="rounded-xl border border-sky-200 bg-sky-50/60 overflow-hidden"
      data-testid="entity-missing-mentions"
    >
      <div className="px-3 py-2 border-b border-sky-200 flex items-center gap-2">
        <MessageSquare size={14} className="text-sky-600" />
        <span className="text-[12px] font-semibold text-sky-800">
          {mentions.length} mention opportunit{mentions.length === 1 ? 'y' : 'ies'} to pitch
        </span>
      </div>
      <ul className="divide-y divide-sky-100">
        {mentions.map((m) => (
          <li key={m.id || m.url} className="px-3 py-2 flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[12px] font-semibold text-gray-900">{m.label || m.url}</div>
              {m.summary && (
                <div className="text-[11px] text-gray-500 mt-0.5">{m.summary}</div>
              )}
            </div>
            <a
              href={m.url} target="_blank" rel="noreferrer"
              className="shrink-0 inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-md border border-sky-300 text-sky-800 hover:bg-sky-100"
              data-testid={`entity-missing-mention-${m.id || m.url}`}
            >
              Open page <ExternalLink size={11} />
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RegressionList({ regressions }) {
  if (!regressions || regressions.length === 0) return null;
  return (
    <div
      className="rounded-xl border border-rose-200 bg-rose-50/60 overflow-hidden"
      data-testid="entity-regressions"
    >
      <div className="px-3 py-2 border-b border-rose-200 flex items-center gap-2">
        <AlertTriangle size={14} className="text-rose-600" />
        <span className="text-[12px] font-semibold text-rose-800">
          {regressions.length} signal regression{regressions.length === 1 ? '' : 's'} since last week
        </span>
      </div>
      <ul className="divide-y divide-rose-100">
        {regressions.map((r, i) => (
          <li key={`${r.name}-${i}`} className="px-3 py-2">
            <div className="text-[12px] font-semibold text-gray-900">{r.name}</div>
            <div className="text-[11px] text-gray-600 mt-0.5">
              {r.from} → <span className="font-bold text-rose-700">{r.to}</span> · {r.summary}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function EntitySeoTab({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await adminEntitySeoStatus(adminToken);
      setData(r.data || null);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Failed to load entity SEO';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const r = await adminEntitySeoRefresh(adminToken);
      setData(r.data || null);
      const refreshInfo = r.data?.refresh;
      if (refreshInfo?.stored) {
        toast.success(`Re-probed entity SEO — ${refreshInfo.regression_count || 0} regression(s).`);
      } else {
        toast.info(`Re-probe finished (${refreshInfo?.reason || 'no change'}).`);
      }
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Refresh failed';
      toast.error(msg);
    } finally {
      setRefreshing(false);
    }
  };

  if (loading && !data) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500" data-testid="entity-loading">
        <Loader2 size={14} className="animate-spin" /> Loading entity SEO health…
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700" data-testid="entity-error">
        <div className="font-semibold mb-1">Couldn't load Entity SEO panel.</div>
        <div className="text-[12px]">{error}</div>
        <button
          onClick={load}
          className="mt-2 inline-flex items-center gap-1.5 text-[11px] font-semibold text-rose-700 underline">
          Retry
        </button>
      </div>
    );
  }

  const snapshot = data?.snapshot;
  const drift = data?.drift || { regressions: [], summaryDeltas: {} };
  const signals = snapshot?.signals || {};
  const summaryDeltas = drift.summaryDeltas || {};
  const wd = summaryDeltas.wikidata_claims || { current: 0, previous: 0, delta: 0 };
  const missing = summaryDeltas.wikidata_missing || { current: 0, previous: 0, delta: 0 };
  const broken = summaryDeltas.sameas_broken || { current: 0, previous: 0, delta: 0 };
  const aggStatus = snapshot?.aggregate_status || 'missing';

  return (
    <div className="space-y-4" data-testid="entity-seo-tab">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-bold text-gray-900">Entity SEO &amp; Knowledge Graph</h3>
            <StatusPill status={aggStatus} />
          </div>
          <p className="text-[11px] text-gray-500 mt-0.5">
            Weekly snapshot of off-site entity signals · last run{' '}
            <span className="text-gray-700">{fmt(snapshot?.generated_at)}</span>
            {snapshot?.iso_week && <> · week <span className="font-mono text-gray-700">{snapshot.iso_week}</span></>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load} disabled={loading}
            className="p-1.5 rounded-lg border border-gray-200 hover:bg-gray-50 text-gray-500"
            title="Reload from cache"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={handleRefresh} disabled={refreshing}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-violet-50 border border-violet-200 hover:bg-violet-100 text-violet-700 disabled:opacity-60"
            data-testid="entity-refresh-now"
          >
            {refreshing ? <Loader2 size={13} className="animate-spin" /> : <Activity size={13} />}
            Re-probe now
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-gray-200 bg-gray-50 p-3 grid sm:grid-cols-3 gap-2">
        <DeltaCell label="Wikidata claims"  current={wd.current}      previous={wd.previous}      delta={wd.delta}      polarity="higher_is_better" />
        <DeltaCell label="Missing claims"   current={missing.current} previous={missing.previous} delta={missing.delta} polarity="lower_is_better" />
        <DeltaCell label="Broken sameAs"    current={broken.current}  previous={broken.previous}  delta={broken.delta}  polarity="lower_is_better" />
      </div>

      <RegressionList regressions={drift.regressions} />

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {Object.keys(SIGNAL_LABELS).map((name) => (
          <SignalCard key={name} name={name} signal={signals[name]} />
        ))}
      </div>

      <MissingClaimsList claims={data?.missingClaims || snapshot?.missing_claims || []} />

      <MissingMentionsList mentions={data?.missingMentions || snapshot?.missing_mentions || []} />

      {data?.alertState?.lastPagedAt && (
        <div className="text-[11px] text-gray-500" data-testid="entity-alert-state">
          Last drift alert paged{' '}
          <span className="text-gray-700">{fmt(data.alertState.lastPagedAt)}</span>
          {data.alertState.regressionCount
            ? <> · {data.alertState.regressionCount} regression(s) at the time</>
            : null}
        </div>
      )}
    </div>
  );
}
