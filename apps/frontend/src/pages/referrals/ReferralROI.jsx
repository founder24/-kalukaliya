import { useCallback, useEffect, useState } from 'react';
import { canStaffCapability } from '@/utils/staffAccess';
import { useAuth } from '@/context/AuthContext';
import { Download, RefreshCw, ShieldCheck, ShieldAlert, LockKeyhole } from 'lucide-react';
import { toast } from 'sonner';
import {
  adminCalculateReferralRoi,
  adminExportReferralRoi,
  adminGetReferralRoiDashboard,
} from '@/utils/api';

const unwrap = response => response?.data ?? response ?? {};
const money = paise => paise == null ? '—' : `₹${(Number(paise) / 100).toFixed(2)}`;
const warnings = raw => {
  try {
    const parsed = JSON.parse(raw || '[]');
    return Array.isArray(parsed) ? parsed.filter(item => typeof item === 'string') : [];
  } catch {
    return [];
  }
};
const statusTone = status => status === 'enabled'
  ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
  : status === 'disabled'
    ? 'border-slate-200 bg-slate-100 text-slate-500'
    : 'border-amber-200 bg-amber-50 text-amber-700';
const formatTimestamp = timestamp => {
  if (timestamp == null || !Number.isFinite(Number(timestamp))) return 'Not recorded';
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(Number(timestamp) * 1000));
};

export default function ReferralROI({ adminToken }) {
  const { user } = useAuth();
  const allowed = canStaffCapability(user, 'referral:settle');
  const [data, setData] = useState(null);
  const [weekId, setWeekId] = useState('');
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setData(unwrap(await adminGetReferralRoiDashboard(adminToken)));
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || 'ROI evidence could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => {
    if (allowed) load();
  }, [allowed, load]);

  const calculate = async () => {
    if (!weekId.trim()) return;
    try {
      await adminCalculateReferralRoi(adminToken, weekId.trim());
      toast.success('Weekly ROI recalculated.');
      setWeekId('');
      await load();
    } catch (requestError) {
      toast.error(requestError?.response?.data?.detail || 'ROI calculation failed.');
    }
  };

  const exportEvidence = async () => {
    setExporting(true);
    try {
      const response = await adminExportReferralRoi(adminToken);
      const blob = response.data instanceof Blob
        ? response.data
        : new Blob([JSON.stringify(response.data)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'referral-roi-evidence.json';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success('ROI evidence downloaded.');
    } catch (requestError) {
      toast.error(requestError?.response?.data?.detail || 'ROI evidence download failed.');
    } finally {
      setExporting(false);
    }
  };

  if (!allowed) {
    return <div className="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-sm text-amber-900" data-testid="referral-roi-forbidden"><LockKeyhole className="mb-2 text-amber-700" size={20} /><b>Referral ROI is restricted.</b><p className="mt-1">The referral:settle capability is required to view provider revenue and reward exposure.</p></div>;
  }

  const controls = data?.controls;
  const reports = data?.reports || [];
  const reconciliation = data?.reconciliation;
  const reconciliationFailures = Array.isArray(reconciliation?.failures)
    ? reconciliation.failures
    : [];
  const reconciliationHealthy = reconciliation?.status === 'completed'
    && reconciliationFailures.length === 0;
  const controlExpired = Boolean(
    controls?.expires_at != null
      && Number(controls.expires_at) <= Math.floor(Date.now() / 1000),
  );
  const controlWarnings = controls
    ? [
      ...(Array.isArray(controls.warnings) ? controls.warnings : []),
      ...(controlExpired ? ['roi-control-evidence-stale'] : []),
    ]
    : ['roi-control-evidence-missing'];
  const pauseRecommended = !controls
    || controlWarnings.length > 0
    || reports.some(report => report.pause_recommended === true || warnings(report.warnings_json).length > 0);
  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Ad-funded referral ROI</h2>
          <p className="mt-1 text-xs text-slate-500">
            Provider-finalized AdSense revenue only. Client ad-impression beacons never authorize rewards.
          </p>
        </div>
         <div className="flex flex-wrap gap-2">
           <button onClick={exportEvidence} disabled={loading || exporting} data-testid="button-download-referral-roi" className="inline-flex items-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2 text-xs font-semibold text-violet-700 disabled:opacity-60">
             <Download size={14} /> {exporting ? 'Preparing…' : 'Download evidence'}
           </button>
           <button onClick={load} disabled={loading || exporting} data-testid="button-refresh-referral-roi" className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 disabled:opacity-60">
             <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh evidence
           </button>
         </div>
      </div>

      {error && <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>}
      {pauseRecommended && (
        <div
          className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900"
          data-testid="referral-roi-pause-recommendation"
          role="alert"
        >
          <div className="flex items-center gap-2 font-bold"><ShieldAlert className="text-rose-600" size={18} />Pause recommendation</div>
          <p className="mt-1">Keep referral accrual paused until the ROI evidence is healthy and current.</p>
          <p className="mt-2 text-xs text-rose-700">Warnings: {[...controlWarnings, ...reports.flatMap(report => warnings(report.warnings_json))].join(', ')}</p>
        </div>
      )}

      <section
        className={`rounded-2xl border p-5 shadow-sm ${
          reconciliationHealthy
            ? 'border-emerald-200 bg-emerald-50/60'
            : 'border-amber-200 bg-amber-50/70'
        }`}
        data-testid="adsense-reconciliation-status"
      >
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              {reconciliationHealthy
                ? <ShieldCheck className="text-emerald-600" size={18} />
                : <ShieldAlert className="text-amber-700" size={18} />}
              <h3 className="font-bold text-slate-900">Automatic AdSense reconciliation</h3>
            </div>
            <p className="mt-1 text-xs text-slate-600">
              Aggregate provider evidence only; no credentials or user identities are shown here.
            </p>
          </div>
          <span className={`rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase ${
            reconciliationHealthy
              ? 'border-emerald-200 bg-white text-emerald-700'
              : 'border-amber-200 bg-white text-amber-800'
          }`}>
            {!reconciliation
              ? 'No run recorded'
              : reconciliation.status === 'running'
                ? 'Running or interrupted'
                : reconciliation.status === 'skipped'
                  ? 'Skipped'
                  : reconciliation.status === 'failed'
                    ? 'Failed'
                    : reconciliationFailures.length > 0
                      ? 'Completed with failures'
                      : 'Healthy'}
          </span>
        </div>
        <p className="mt-3 text-sm font-semibold text-slate-800">
          {!reconciliation
            ? 'No automatic reconciliation has completed yet.'
            : reconciliation.status === 'running'
              ? 'The last automatic run is still running or was interrupted before it finished.'
              : reconciliation.status === 'skipped'
                ? 'The last automatic run was skipped because the provider endpoint is not configured.'
                : reconciliation.status === 'failed'
                  ? 'The last automatic run failed before producing complete evidence.'
                  : reconciliationFailures.length > 0
                    ? 'The last automatic run completed, but some weekly provider reports were rejected.'
                    : 'The last automatic run completed without rejected weekly reports.'}
        </p>
        {reconciliation && <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
          <div className="rounded-lg bg-white/80 px-3 py-2"><p className="text-[10px] uppercase tracking-wide text-slate-500">Last run</p><p className="mt-1 text-xs font-semibold text-slate-800">{formatTimestamp(reconciliation.completed_at ?? reconciliation.started_at)}</p></div>
          <div className="rounded-lg bg-white/80 px-3 py-2"><p className="text-[10px] uppercase tracking-wide text-slate-500">Weeks fetched</p><p className="mt-1 text-sm font-bold text-slate-900">{reconciliation.fetched ?? 0} / {reconciliation.weeks ?? 0}</p></div>
          <div className="rounded-lg bg-white/80 px-3 py-2"><p className="text-[10px] uppercase tracking-wide text-slate-500">Imported</p><p className="mt-1 text-sm font-bold text-slate-900">{reconciliation.imported ?? 0}</p></div>
          <div className="rounded-lg bg-white/80 px-3 py-2"><p className="text-[10px] uppercase tracking-wide text-slate-500">Already recorded</p><p className="mt-1 text-sm font-bold text-slate-900">{reconciliation.idempotent ?? 0}</p></div>
          <div className="rounded-lg bg-white/80 px-3 py-2"><p className="text-[10px] uppercase tracking-wide text-slate-500">ROI recalculated</p><p className="mt-1 text-sm font-bold text-slate-900">{reconciliation.calculated ?? 0}</p></div>
        </div>}
        {reconciliationFailures.length > 0 && (
          <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
            <b>Safe failure summary:</b> {reconciliationFailures.join(' · ')}
          </div>
        )}
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="referral-roi-inventory">
        <div className="flex items-center justify-between gap-3">
          <div><h3 className="font-bold text-slate-900">Production ad inventory</h3><p className="mt-1 text-xs text-slate-500">Disabled and unconfigured networks contribute zero revenue.</p></div>
          <span className="rounded-full bg-violet-50 px-2.5 py-1 text-[11px] font-bold text-violet-700">Authoritative config</span>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {(data?.inventory || []).map(network => (
            <div key={network.network} className="rounded-xl border border-slate-100 bg-slate-50 p-3">
              <div className="flex items-center justify-between gap-2">
                <b className="text-sm text-slate-900">{network.network}</b>
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${statusTone(network.status)}`}>{network.status}</span>
              </div>
              <p className="mt-2 text-[11px] text-slate-500">{network.policy_notes}</p>
              <p className="mt-2 text-[10px] font-semibold text-slate-400">{network.contributes_to_revenue ? 'Counts toward finalized revenue' : 'Excluded from funding'}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-2"><h3 className="font-bold text-slate-900">Safety controls</h3>{controls && !controlExpired && !Object.entries(controls).some(([key, value]) => key.endsWith('_healthy') && value === false) ? <ShieldCheck className="text-emerald-600" size={18} /> : <ShieldAlert className="text-rose-600" size={18} />}</div>
        <p className="mt-1 text-xs text-slate-500">A missing or expired control record fails closed and recommends pausing accrual.</p>
        {controls ? <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{Object.entries(controls).filter(([key]) => key.endsWith('_healthy')).map(([key, value]) => <div key={key} className={`rounded-lg px-3 py-2 text-xs font-semibold ${value ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>{key.replaceAll('_', ' ')}: {value ? 'pass' : 'blocked'}</div>)}</div> : <p className="mt-4 text-sm font-semibold text-rose-700">No current control evidence.</p>}
        {controlWarnings.length > 0 && <p className="mt-3 text-xs text-rose-700">Warnings: {controlWarnings.join(', ')}</p>}
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="referral-roi-unit-economics">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div><h3 className="font-bold text-slate-900">Weekly unit economics</h3><p className="mt-1 text-xs text-slate-500">Rewards are compared with finalized net contribution and true operating cost.</p></div>
          <div className="flex gap-2"><input value={weekId} onChange={event => setWeekId(event.target.value)} placeholder="Week ID to recalculate" className="h-9 rounded-lg border border-slate-200 px-3 text-xs outline-none focus:border-violet-500" /><button onClick={calculate} disabled={!weekId.trim()} className="h-9 rounded-lg bg-slate-900 px-3 text-xs font-bold text-white disabled:opacity-40">Calculate</button></div>
        </div>
        <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[1380px] text-left text-xs"><thead><tr className="border-b border-slate-100 text-[10px] uppercase tracking-wide text-slate-400"><th className="pb-2">Week</th><th className="pb-2">Quality</th><th className="pb-2">Clicks</th><th className="pb-2">Browsers</th><th className="pb-2">Accounts</th><th className="pb-2">Mature</th><th className="pb-2">Repeat</th><th className="pb-2">Payable</th><th className="pb-2">Paid</th><th className="pb-2">Impressions</th><th className="pb-2">Net ad revenue</th><th className="pb-2">Cost</th><th className="pb-2">Margin</th><th className="pb-2">Payback</th><th className="pb-2">Warnings</th></tr></thead><tbody>{reports.map(report => <tr key={report.id} className="border-b border-slate-50"><td className="py-3 font-semibold text-slate-800">{report.week_key || report.week_id}</td><td className={`py-3 font-bold ${report.data_quality === 'healthy' ? 'text-emerald-700' : 'text-rose-700'}`}>{report.data_quality}</td><td className="py-3">{report.referral_clicks}</td><td className="py-3">{report.unique_browser_identities}</td><td className="py-3">{report.authenticated_accounts}</td><td className="py-3">{report.mature_verified_visitors}</td><td className="py-3">{report.repeat_week_visitors}</td><td className="py-3">{report.payable_statements}</td><td className="py-3">₹{report.cash_paid_inr}</td><td className="py-3">{report.actual_monetized_impressions}</td><td className="py-3">{money(report.finalized_net_ad_revenue_paise)}</td><td className="py-3">₹{report.true_program_cost_inr}</td><td className={`py-3 ${Number(report.contribution_margin_paise) < 0 ? 'text-rose-700' : 'text-emerald-700'}`}>{money(report.contribution_margin_paise)}</td><td className="py-3">{report.payback_ratio_milli == null ? '—' : `${(Number(report.payback_ratio_milli) / 10).toFixed(1)}x`}</td><td className="py-3 max-w-[260px] text-rose-700">{warnings(report.warnings_json).join(', ') || '—'}</td></tr>)}</tbody></table>{!loading && !reports.length && <p className="py-8 text-center text-sm text-slate-500">No weekly ROI reports have been calculated yet.</p>}</div>
      </section>
    </div>
  );
}