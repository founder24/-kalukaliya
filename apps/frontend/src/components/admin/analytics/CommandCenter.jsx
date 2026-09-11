import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { RefreshCw } from 'lucide-react';
import { WORKER_API } from '@/utils/api';
import { getToken } from '@/hooks/useTokenManager';

export const COMMAND_SECTIONS = [
  ['overview', 'Overview', 'users'],
  ['users', 'Users', 'users'],
  ['content', 'Content', 'content'],
  ['rag', 'RAG', 'rag'],
  ['chat', 'Chat', 'chat'],
  ['ads', 'Ads', 'ads'],
  ['reliability', 'Reliability', 'incidents'],
  ['audit', 'Audit', 'audit'],
];

const zeroSummary = () => ({
  generated_at: null,
  users: {},
  content: {},
  rag: {},
  chat: {},
  ads: {},
  consent: {},
  incidents: {},
  audit: {},
});
const metric = (group, ...keys) => {
  for (const key of keys) if (group?.[key] != null) return group[key];
  return 0;
};
const metricLabel = (key) => key.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
const definition = (key) => ({
  active_users: 'Distinct users active during the selected period.',
  new_users: 'Accounts created during the selected period.',
  indexed: 'Content records currently indexed for retrieval.',
  stale: 'Records changed since their last retrieval index.',
  incidents: 'Reliability incidents recorded during the selected period.',
  consent_rate: 'Share of recorded consent decisions that were granted.',
}[key] || 'Reported by the command-center summary for the selected period.');

export function commandCenterAuthConfig(token) {
  return {
    withCredentials: true,
    ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
  };
}

function Metrics({ title, source }) {
  const entries = Object.entries(source || {}).filter(([, value]) =>
    typeof value === 'number' || typeof value === 'string' || typeof value === 'boolean',
  );
  return <section className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
    <h3 className="font-semibold text-gray-900">{title}</h3>
    <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
      {entries.length ? entries.map(([key, value]) => <div key={key} className="min-w-0">
        <dt className="text-xs text-gray-500" title={definition(key)}>{metricLabel(key)}</dt>
        <dd className="mt-1 truncate text-xl font-bold tabular-nums text-gray-900">{String(value)}</dd>
      </div>) : <p className="col-span-full text-sm text-gray-500">No metrics reported for this period.</p>}
    </dl>
  </section>;
}

export default function CommandCenter() {
  const search = new URLSearchParams(typeof window === 'undefined' ? '' : window.location.search);
  const requested = search.get('section')?.toLowerCase();
  const [section, setSection] = useState(COMMAND_SECTIONS.some(([id]) => id === requested) ? requested : 'overview');
  const [days, setDays] = useState(Number(search.get('days')) === 30 ? 30 : 7);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const updateUrl = useCallback((nextSection = section, nextDays = days) => {
    const params = new URLSearchParams(window.location.search);
    params.set('section', nextSection);
    params.set('days', String(nextDays));
    window.history.replaceState({}, '', `${window.location.pathname}?${params}`);
  }, [section, days]);
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const response = await axios.get(`${WORKER_API}/staff/analytics/command-center`, {
        params: { days },
        ...commandCenterAuthConfig(getToken()),
      });
      setData({ ...zeroSummary(), ...(response.data || {}) });
    } catch (err) {
      setData(null);
      setError(err?.response?.data?.detail || 'The command-center summary could not be loaded.');
    } finally { setLoading(false); }
  }, [days]);
  useEffect(() => { updateUrl(); load(); }, [days, section, load, updateUrl]);
  const choose = (id) => { setSection(id); };
  const summary = data || zeroSummary();
  const selectedGroup = COMMAND_SECTIONS.find(([id]) => id === section);
  const panel = section === 'overview'
    ? [['Users', summary.users], ['Content', summary.content], ['RAG', summary.rag], ['Chat', summary.chat], ['Ads', summary.ads], ['Consent', summary.consent], ['Reliability', summary.incidents], ['Audit', summary.audit]]
    : [[selectedGroup?.[1] || 'Overview', summary[selectedGroup?.[2]]]];
  const generated = summary.generated_at ? new Date(summary.generated_at) : null;

  return <div className="p-4 sm:p-6 space-y-5">
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div><h2 className="text-lg font-semibold text-gray-900">Staff command center</h2>
        <p className="mt-1 text-xs text-gray-500">Operational summary only. Metrics are period totals or current counts as defined on each card.</p>
        <p className="mt-1 text-xs text-gray-400">{generated ? `Source generated ${generated.toLocaleString()}` : 'Freshness: awaiting the Worker summary.'}</p>
      </div>
      <div className="flex gap-2"><select aria-label="Summary period" value={days} onChange={e => setDays(Number(e.target.value))} className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm"><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option></select>
        <button onClick={load} disabled={loading} className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 disabled:opacity-60"><RefreshCw size={15} className={loading ? 'animate-spin' : ''} />Refresh</button></div>
    </div>
    <nav aria-label="Command center sections" className="flex gap-1 overflow-x-auto rounded-xl bg-gray-100 p-1">
      {COMMAND_SECTIONS.map(([id, label]) => <button key={id} onClick={() => choose(id)} className={`whitespace-nowrap rounded-lg px-3 py-2 text-xs font-semibold ${section === id ? 'bg-violet-600 text-white' : 'text-gray-600 hover:bg-white'}`}>{label}</button>)}
    </nav>
    {error ? <div role="alert" className="rounded-2xl border border-red-200 bg-red-50 p-5 text-sm text-red-800"> <strong>Unable to refresh.</strong> {error}<button onClick={load} className="ml-3 font-semibold underline">Retry</button></div> : null}
    {loading && !data ? <div role="status" className="rounded-2xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500">Loading command-center summary…</div> : null}
    {!loading || data ? <div className="grid gap-4 lg:grid-cols-2">{panel.map(([title, source]) => <Metrics key={title} title={title} source={source} />)}</div> : null}
  </div>;
}