import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { Activity, BookOpen, MessageSquare, RefreshCw, ShieldCheck, Users } from 'lucide-react';
import { API_BASE } from '@/utils/api';

function requestConfig(adminToken) {
  const config = { withCredentials: true };
  if (adminToken && typeof adminToken === 'string' && adminToken.split('.').length === 3) {
    config.headers = { Authorization: `Bearer ${adminToken}` };
  }
  return config;
}

function numberValue(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function SummaryCard({ icon: Icon, label, value, detail }) {
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-gray-400">{label}</span>
        <Icon size={16} className="text-violet-500" />
      </div>
      <p className="mt-3 text-2xl font-semibold text-gray-900">{value.toLocaleString()}</p>
      {detail && <p className="mt-1 text-xs text-gray-500">{detail}</p>}
    </div>
  );
}

export default function StaffDashboardOverview({ adminToken, onNavigate }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await axios.get(
        `${API_BASE}/staff/analytics/command-center?days=7`,
        requestConfig(adminToken),
      );
      setData(response.data || {});
    } catch (loadError) {
      setError(loadError?.response?.data?.detail || 'The Staff Dashboard could not load.');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section data-testid="staff-dashboard-overview" className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-violet-500">Staff overview</p>
          <h2 className="mt-1 text-2xl font-semibold text-gray-900">Dashboard</h2>
          <p className="mt-1 text-sm text-gray-500">A seven-day view of content, chat, and account health.</p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-600 shadow-sm hover:bg-gray-50 disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {loading && !data && (
        <div className="rounded-2xl border border-gray-200 bg-white p-8 text-sm text-gray-500">
          Loading dashboard metrics…
        </div>
      )}

      {error && (
        <div role="alert" data-testid="staff-dashboard-error" className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <p>{error}</p>
          <button type="button" onClick={load} className="mt-3 font-medium underline">
            Retry
          </button>
        </div>
      )}

      {data && !error && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <SummaryCard
              icon={Users}
              label="Active accounts"
              value={numberValue(data.users?.active_accounts)}
              detail={`${numberValue(data.users?.new_users)} new in 7 days`}
            />
            <SummaryCard
              icon={BookOpen}
              label="Published chapters"
              value={numberValue(data.content?.published)}
              detail={`${numberValue(data.content?.unpublished)} awaiting publication`}
            />
            <SummaryCard
              icon={Activity}
              label="RAG stale"
              value={numberValue(data.rag?.stale)}
              detail={`${numberValue(data.rag?.unindexed)} not indexed`}
            />
            <SummaryCard
              icon={MessageSquare}
              label="Chat completions"
              value={numberValue(data.chat?.completions)}
              detail={`${numberValue(data.chat?.failures)} failures in 7 days`}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-center gap-2">
                <ShieldCheck size={17} className="text-emerald-500" />
                <h3 className="font-semibold text-gray-900">Content health</h3>
              </div>
              <p className="mt-3 text-sm text-gray-600">
                {numberValue(data.rag?.indexed)} chapters are indexed and current.
                {' '}
                {numberValue(data.audit?.actions)} staff actions were recorded in the selected period.
              </p>
              <button
                type="button"
                onClick={() => onNavigate?.('contenthub')}
                className="mt-4 text-xs font-semibold text-violet-600 hover:text-violet-700"
              >
                Open Content Editor →
              </button>
            </div>
            <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-center gap-2">
                <Activity size={17} className="text-violet-500" />
                <h3 className="font-semibold text-gray-900">Operations</h3>
              </div>
              <p className="mt-3 text-sm text-gray-600">
                Average chat latency: {numberValue(data.chat?.average_latency_ms).toLocaleString()} ms.
                {' '}
                {numberValue(data.incidents?.failed_publish_jobs)} failed publish jobs need review.
              </p>
              <button
                type="button"
                onClick={() => onNavigate?.('analytics')}
                className="mt-4 text-xs font-semibold text-violet-600 hover:text-violet-700"
              >
                Open Analytics →
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}