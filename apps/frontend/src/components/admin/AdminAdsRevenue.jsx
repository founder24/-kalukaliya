import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, CheckCircle2, RefreshCw, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import { adminGetAdsRevenue } from '@/utils/api';

const money = (paise, currency = 'INR') => {
  if (currency !== 'INR') return `${currency} ${Number(paise ?? 0).toLocaleString()}`;
  return `₹${(Number(paise ?? 0) / 100).toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
};

const timestamp = value => value
  ? new Date(Number(value) * 1000).toLocaleString()
  : 'Not recorded';

function reconciliationLabel(data) {
  if (!data) return 'No run recorded';
  if (data.status === 'running') return 'Running or interrupted';
  if (data.status === 'skipped') return 'Skipped';
  if (data.status === 'failed') return 'Failed';
  if (data.failures?.length) return 'Completed with failures';
  return 'Completed';
}

export default function AdminAdsRevenue({ adminToken }) {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await adminGetAdsRevenue(adminToken, days);
      setData(response.data);
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || 'Provider revenue could not be loaded.');
      toast.error('Provider revenue could not be loaded');
    } finally {
      setLoading(false);
    }
  }, [adminToken, days]);

  useEffect(() => { load(); }, [load]);

  const receipts = data?.receipts ?? [];
  const totals = data?.totals ?? {};
  const reconciliation = data?.reconciliation;
  const configured = data?.endpoint_configured === true;
  const healthy = configured
    && reconciliation?.status === 'completed'
    && reconciliation.failures?.length === 0;

  return (
    <SectionErrorBoundary name="Ad revenue">
      <div className="space-y-5 p-6" data-testid="admin-ads-revenue">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold text-gray-900">Ad revenue receipts</h2>
            <p className="mt-1 text-sm text-gray-500">
              Finalized aggregate AdSense provider reports only. No estimates or client impression beacons are counted as revenue.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              aria-label="Revenue period"
              value={days}
              onChange={event => setDays(Number(event.target.value))}
              className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs text-gray-700"
            >
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
            </select>
            <button
              type="button"
              onClick={load}
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs text-gray-600 disabled:opacity-50"
            >
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Refresh
            </button>
          </div>
        </div>

        {error && <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>}

        {!loading && data && (
          <section
            className={`rounded-2xl border p-5 ${
              healthy ? 'border-emerald-200 bg-emerald-50/60' : 'border-amber-200 bg-amber-50/70'
            }`}
            data-testid="adsense-reconciliation-status"
          >
            <div className="flex items-start gap-2">
              {healthy
                ? <CheckCircle2 size={18} className="mt-0.5 text-emerald-600" />
                : <ShieldAlert size={18} className="mt-0.5 text-amber-700" />}
              <div className="min-w-0">
                <h3 className="font-semibold text-gray-900">Scheduled AdSense reconciliation</h3>
                <p className="mt-1 text-sm font-medium text-gray-800">{reconciliationLabel(reconciliation)}</p>
                <p className="mt-1 text-xs text-gray-600">
                  {configured
                    ? 'The production report endpoint is configured; the private URL and credentials are not shown here.'
                    : 'The production report endpoint is not configured. The daily importer will record a skipped run until it is bound.'}
                </p>
                {configured && (
                  <p className="mt-1 text-xs text-gray-600">
                    Endpoint auth token: {data.token_configured ? 'configured' : 'not configured (optional)'}.
                  </p>
                )}
                {reconciliation && (
                  <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-5">
                    <div><b>Started:</b> {timestamp(reconciliation.started_at)}</div>
                    <div><b>Completed:</b> {timestamp(reconciliation.completed_at)}</div>
                    <div><b>Reports fetched:</b> {reconciliation.fetched ?? 0} / {reconciliation.weeks ?? 0}</div>
                    <div><b>Imported:</b> {reconciliation.imported ?? 0}</div>
                    <div><b>Already recorded:</b> {reconciliation.idempotent ?? 0}</div>
                  </div>
                )}
                {(reconciliation?.failures ?? []).length > 0 && (
                  <div role="alert" className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
                    <b>Failure details:</b> {reconciliation.failures.join(' · ')}
                  </div>
                )}
                {reconciliation?.status === 'skipped' && !configured && (
                  <div className="mt-3 flex items-start gap-2 text-xs text-amber-800">
                    <AlertCircle size={14} className="mt-0.5 shrink-0" />
                    <span>Configure <code>ADSENSE_REPORT_URL</code> on the production API Worker to enable finalized receipts.</span>
                  </div>
                )}
              </div>
            </div>
          </section>
        )}

        <div className="grid gap-3 sm:grid-cols-3">
          {[
            ['Net provider revenue', money(totals.net_revenue_paise)],
            ['Gross provider revenue', money(totals.gross_revenue_paise)],
            ['Provider-reported monetized impressions', Number(totals.monetized_impressions ?? 0).toLocaleString()],
          ].map(([label, value]) => (
            <div key={label} className="rounded-2xl border border-gray-200 bg-white p-5">
              <p className="text-xl font-bold text-gray-900">{loading ? '…' : value}</p>
              <p className="mt-1 text-xs text-gray-500">{label} · last {days} days</p>
            </div>
          ))}
        </div>

        <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white">
          <div className="border-b border-gray-100 px-5 py-4">
            <h3 className="text-sm font-semibold text-gray-900">Finalized provider receipts</h3>
            <p className="mt-1 text-xs text-gray-500">
              Reports are aggregate-only. Slot-level earnings are not available from the current report endpoint.
            </p>
          </div>
          {loading ? (
            <p className="p-5 text-sm text-gray-500">Loading provider receipts…</p>
          ) : receipts.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-gray-50 text-gray-500">
                  <tr>
                    <th className="px-4 py-3">Period</th>
                    <th className="px-4 py-3">Settlement</th>
                    <th className="px-4 py-3">Gross</th>
                    <th className="px-4 py-3">Adjustments</th>
                    <th className="px-4 py-3">Fees</th>
                    <th className="px-4 py-3">Net</th>
                    <th className="px-4 py-3">Impressions</th>
                    <th className="px-4 py-3">Evidence freshness</th>
                  </tr>
                </thead>
                <tbody>
                  {receipts.map(receipt => (
                    <tr key={receipt.id} className="border-t border-gray-100 text-gray-700">
                      <td className="whitespace-nowrap px-4 py-3">
                        {new Date(receipt.period_start * 1000).toLocaleDateString()} – {new Date(receipt.period_end * 1000).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-3 font-mono">{receipt.settlement_period}</td>
                      <td className="px-4 py-3">{money(receipt.gross_revenue_paise, receipt.currency)}</td>
                      <td className="px-4 py-3">{money(receipt.adjustments_paise, receipt.currency)}</td>
                      <td className="px-4 py-3">{money(receipt.provider_fees_paise, receipt.currency)}</td>
                      <td className="px-4 py-3 font-semibold text-gray-900">{money(receipt.net_revenue_paise, receipt.currency)}</td>
                      <td className="px-4 py-3">{Number(receipt.monetized_impressions ?? 0).toLocaleString()}</td>
                      <td className={`px-4 py-3 ${receipt.stale ? 'text-amber-700' : 'text-emerald-700'}`}>
                        {receipt.stale ? 'Stale' : 'Current'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="p-5 text-sm text-gray-500">No finalized AdSense receipts in this period.</p>
          )}
        </section>
      </div>
    </SectionErrorBoundary>
  );
}
