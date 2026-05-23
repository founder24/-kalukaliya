import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  Cloud, ShieldCheck, Search, RefreshCw,
  CheckCircle2, AlertCircle, Play, Send,
  ExternalLink,
} from 'lucide-react';
import { API_BASE, adminHeaders } from '@/utils/api';

const adminHdr = (token) => ({
  headers: adminHeaders(token),
  withCredentials: true,
});

// Task #333 — observability rewire. The Scheduler + Tasks tabs were
// retired alongside the GCP cron / async-worker tier; their live data
// is now sourced from Azure ACA Jobs (`AdminCronJobsCard`) and AWS SQS
// (`AdminAwsInfraCard`) under the Infrastructure tab. The remaining
// tabs cover the inference-only Vertex / Discovery / Web Security
// Scanner dependencies that surface in `/api/readyz` and stay on GCP.
const TABS = [
  { id: 'overview',  label: 'Overview',         icon: Cloud      },
  { id: 'wss',       label: 'Security Scanner', icon: ShieldCheck},
  { id: 'discovery', label: 'Discovery Engine', icon: Search     },
];

function StatusPill({ ok, label }) {
  const cls = ok
    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
    : 'bg-amber-50 text-amber-700 border-amber-200';
  const Icon = ok ? CheckCircle2 : AlertCircle;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full border ${cls}`}>
      <Icon size={12} />
      {label}
    </span>
  );
}

function Card({ children, className = '' }) {
  return (
    <div className={`bg-white border border-gray-200 rounded-lg shadow-sm p-5 ${className}`}>
      {children}
    </div>
  );
}

function Btn({ children, onClick, disabled, variant = 'default', icon: Icon }) {
  const base = 'inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md border transition-colors disabled:opacity-50 disabled:cursor-not-allowed';
  const styles = {
    default: 'bg-white border-gray-300 hover:bg-gray-50 text-gray-700',
    primary: 'bg-blue-600 border-blue-600 hover:bg-blue-700 text-white',
    danger:  'bg-white border-red-300 hover:bg-red-50 text-red-700',
  };
  return (
    <button onClick={onClick} disabled={disabled} className={`${base} ${styles[variant]}`}>
      {Icon && <Icon size={14} />}
      {children}
    </button>
  );
}

// ── Overview tab ────────────────────────────────────────────────────────
function OverviewTab({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/admin/gcp/services-status`, adminHdr(adminToken));
      setData(res.data);
    } catch (e) {
      toast.error(`Failed to load GCP status: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  if (!data) {
    return <Card>{loading ? 'Loading…' : 'No data'}</Card>;
  }

  const services = Object.entries(data.services || {});
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm text-gray-500">Service Account</div>
          <div className="font-mono text-sm">
            {data.service_account_configured ? (
              <span className="text-emerald-700">{data.service_account_project}</span>
            ) : (
              <span className="text-amber-700">Not configured — set GOOGLE_APPLICATION_CREDENTIALS_JSON</span>
            )}
          </div>
        </div>
        <Btn onClick={load} icon={RefreshCw}>Refresh</Btn>
      </div>

      <div className="flex gap-2">
        <StatusPill ok={true} label={`${data.configured_count} configured`} />
        {data.disabled_count > 0 && (
          <StatusPill ok={false} label={`${data.disabled_count} disabled`} />
        )}
      </div>

      <Card>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {services.map(([key, svc]) => (
            <div
              key={key}
              className="flex items-start justify-between p-3 border border-gray-100 rounded-md hover:border-gray-300 transition-colors"
            >
              <div className="min-w-0 flex-1">
                <div className="font-medium text-sm capitalize">{key.replace(/_/g, ' ')}</div>
                <div className="text-xs text-gray-500 mt-0.5">{svc.auth_mode}</div>
                {svc.endpoint && (
                  <code className="text-xs text-gray-400 block mt-1 truncate">{svc.endpoint}</code>
                )}
                {!svc.configured && svc.key?.candidates && (
                  <div className="text-xs text-amber-700 mt-1">
                    Set: {svc.key.candidates.join(' or ')}
                  </div>
                )}
                {!svc.configured && svc.extra_env_required && (
                  <div className="text-xs text-amber-700 mt-1">
                    Also needs: {svc.extra_env_required[0]}
                  </div>
                )}
              </div>
              <StatusPill ok={!!svc.configured} label={svc.configured ? 'OK' : 'Off'} />
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

// Task #333 — SchedulerTab + TasksTab removed; cron health is now
// rendered by `AdminCronJobsCard` (Azure ACA Jobs source) and async
// queue health by `AdminAwsInfraCard` (AWS SQS source) inside the
// AdminHealth Infrastructure tab.

// ── Web Security Scanner tab ───────────────────────────────────────────
function WssTab({ adminToken }) {
  const [configs, setConfigs] = useState([]);
  const [err, setErr] = useState(null);
  const load = useCallback(async () => {
    setErr(null);
    try {
      const res = await axios.get(`${API_BASE}/admin/gcp/wss/configs`, adminHdr(adminToken));
      if (res.data.status === 'ok') setConfigs(res.data.scan_configs || []);
      else setErr(res.data.error || res.data.status);
    } catch (e) { setErr(e.response?.data?.detail || e.message); }
  }, [adminToken]);
  useEffect(() => { load(); }, [load]);

  const startScan = async (name) => {
    try {
      const res = await axios.post(
        `${API_BASE}/admin/gcp/wss/configs/start`,
        { name },
        adminHdr(adminToken),
      );
      if (res.data.status === 'ok') toast.success('Scan started — check back in ~10 min');
      else toast.error(res.data.error || 'Failed');
    } catch (e) { toast.error(e.response?.data?.detail || e.message); }
  };

  return (
    <div className="space-y-3">
      <div className="flex justify-between items-center">
        <div className="text-sm text-gray-600">{configs.length} scan configs</div>
        <Btn onClick={load} icon={RefreshCw}>Refresh</Btn>
      </div>
      {err && <Card className="border-amber-200 bg-amber-50 text-amber-800 text-sm">{err}</Card>}
      {configs.length === 0 && !err && (
        <Card className="text-sm text-gray-500">
          No scan configs. Create one in the{' '}
          <a className="text-blue-600 inline-flex items-center gap-1" target="_blank" rel="noreferrer"
             href="https://console.cloud.google.com/security/web-scanner">
            GCP console <ExternalLink size={12} />
          </a>{' '}pointing at https://syrabit.ai.
        </Card>
      )}
      {configs.map((c) => (
        <Card key={c.name}>
          <div className="flex justify-between items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="font-medium">{c.display_name}</div>
              <div className="text-xs text-gray-500 mt-1 font-mono truncate">{c.name?.split('/').pop()}</div>
              <div className="text-sm text-gray-700 mt-2">{c.starting_urls?.join(', ')}</div>
            </div>
            <Btn variant="primary" icon={Play} onClick={() => startScan(c.name)}>Start scan</Btn>
          </div>
        </Card>
      ))}
    </div>
  );
}

// ── Discovery Engine tab ───────────────────────────────────────────────
function DiscoveryTab({ adminToken }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);

  const search = async () => {
    if (!q.trim()) return;
    setLoading(true);
    try {
      const res = await axios.post(
        `${API_BASE}/admin/discovery/engine/search`,
        { query: q, page_size: 10 },
        adminHdr(adminToken),
      );
      setResults(res.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally { setLoading(false); }
  };

  return (
    <div className="space-y-3">
      <Card>
        <div className="flex gap-2">
          <input
            type="text"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search()}
            placeholder="Search the Vertex AI data store…"
            className="flex-1 px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:border-blue-500"
          />
          <Btn variant="primary" icon={Send} onClick={search} disabled={loading || !q.trim()}>
            Search
          </Btn>
        </div>
      </Card>

      {results && results.status !== 'ok' && (
        <Card className="border-amber-200 bg-amber-50 text-amber-800 text-sm">
          <div className="font-medium">{results.status}</div>
          <div className="text-xs mt-1">{results.error}</div>
        </Card>
      )}

      {results && results.status === 'ok' && (
        <div className="space-y-2">
          <div className="text-sm text-gray-500">
            {results.count} of {results.total_size ?? '?'} results · {Math.round(results.elapsed_ms || 0)}ms
          </div>
          {(results.results || []).map((r) => (
            <Card key={r.id || r.name}>
              <div className="font-medium">{r.title || r.name?.split('/').pop()}</div>
              {r.uri && (
                <a href={r.uri} target="_blank" rel="noreferrer"
                   className="text-xs text-blue-600 inline-flex items-center gap-1 mt-1">
                  {r.uri} <ExternalLink size={11} />
                </a>
              )}
              {r.snippet && (
                <div className="text-sm text-gray-700 mt-2">
                  {typeof r.snippet === 'string' ? r.snippet : JSON.stringify(r.snippet).slice(0, 240)}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Root ────────────────────────────────────────────────────────────────
export default function AdminGcpPanel({ adminToken }) {
  const [tab, setTab] = useState('overview');
  const TabComponent = {
    overview:  OverviewTab,
    wss:       WssTab,
    discovery: DiscoveryTab,
  }[tab];

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold flex items-center gap-2">
          <Cloud size={24} /> GCP Integrations
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          10 wired Google Cloud services + Slack notifier. Set
          <code className="mx-1 px-1.5 py-0.5 bg-gray-100 rounded text-xs">GOOGLE_APPLICATION_CREDENTIALS_JSON</code>
          to enable the SA-gated services.
        </p>
      </div>

      <div className="border-b border-gray-200 mb-5">
        <nav className="-mb-px flex gap-1 overflow-x-auto">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = t.id === tab;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`inline-flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                  active
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                <Icon size={16} />
                {t.label}
              </button>
            );
          })}
        </nav>
      </div>

      <TabComponent adminToken={adminToken} />
    </div>
  );
}
