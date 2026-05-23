import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  Plus, Trash2, ShieldCheck, ShieldX, RefreshCcw, Loader2,
  AlertTriangle, Ban, Search, Globe, Info, Target, TrendingDown, TrendingUp, Minus,
  Inbox, Megaphone, CheckCircle2,
} from 'lucide-react';
import { API_BASE } from '@/utils/api';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';

const adminHeaders = (token) => {
  const isRealJwt = token && typeof token === 'string' && token.split('.').length === 3;
  return isRealJwt ? { Authorization: `Bearer ${token}` } : {};
};

function _fmtTime(ts) {
  if (!ts) return '—';
  const n = typeof ts === 'number' ? ts * 1000 : Date.parse(ts);
  if (!Number.isFinite(n)) return '—';
  const d = new Date(n);
  const now = Date.now();
  const diffMs = now - d.getTime();
  if (diffMs < 60_000) return 'just now';
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)}m ago`;
  if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)}h ago`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const REASON_STYLE = {
  not_allowlisted:   { label: 'Not on allowlist',    cls: 'text-amber-700 bg-amber-50 border-amber-200' },
  operator_blocked:  { label: 'Operator blocked',    cls: 'text-rose-700 bg-rose-50 border-rose-200' },
  hard_denied:       { label: 'Hard-denied',         cls: 'text-rose-700 bg-rose-50 border-rose-200' },
  redirect_not_allowed: { label: 'Unsafe redirect',  cls: 'text-amber-700 bg-amber-50 border-amber-200' },
  robots_disallow:   { label: 'robots.txt disallow', cls: 'text-slate-700 bg-slate-50 border-slate-200' },
  private_ip:        { label: 'Private/loopback IP', cls: 'text-rose-700 bg-rose-50 border-rose-200' },
  scheme:            { label: 'Bad URL scheme',      cls: 'text-slate-700 bg-slate-50 border-slate-200' },
  invalid_url:       { label: 'Invalid URL',         cls: 'text-slate-700 bg-slate-50 border-slate-200' },
  timeout:           { label: 'Fetch timeout',       cls: 'text-slate-700 bg-slate-50 border-slate-200' },
  too_large:         { label: 'Too large',           cls: 'text-slate-700 bg-slate-50 border-slate-200' },
};

function ReasonBadge({ reason }) {
  const s = REASON_STYLE[reason] || { label: reason || 'unknown', cls: 'text-gray-600 bg-gray-50 border-gray-200' };
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${s.cls}`}>
      {s.label}
    </span>
  );
}

function StatusBadge({ status }) {
  if (status === 'blocked') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-rose-50 border border-rose-200 text-rose-700 px-2 py-0.5 text-[11px] font-semibold">
        <ShieldX size={11} /> Blocked
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-700 px-2 py-0.5 text-[11px] font-semibold">
      <ShieldCheck size={11} /> Allowed
    </span>
  );
}

function SourceBadge({ source }) {
  if (source === 'educator') {
    return (
      <span
        className="inline-flex items-center rounded-full bg-indigo-50 border border-indigo-200 text-indigo-700 px-1.5 py-0.5 text-[10px] font-semibold ml-1"
        title="Auto-approved by an educator after kid-safe + robots.txt probe"
      >
        Educator
      </span>
    );
  }
  if (source === 'system') {
    return (
      <span className="inline-flex items-center rounded-full bg-gray-50 border border-gray-200 text-gray-600 px-1.5 py-0.5 text-[10px] font-semibold ml-1">
        System
      </span>
    );
  }
  return null;
}

function AllowlistTab({ adminToken }) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [effective, setEffective] = useState(null);
  const [overrides, setOverrides] = useState([]);
  const [filter, setFilter] = useState('');
  const [form, setForm] = useState({ domain: '', status: 'allowed', note: '' });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/admin/edu/allowlist`, {
        headers: adminHeaders(adminToken),
        withCredentials: true,
      });
      setEffective(res.data?.effective || null);
      setOverrides(Array.isArray(res.data?.overrides) ? res.data.overrides : []);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Could not load allowlist');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  const handleAdd = async (e) => {
    e?.preventDefault?.();
    const domain = form.domain.trim();
    if (!domain || domain.length < 3) {
      toast.error('Enter a domain (e.g. example.com)');
      return;
    }
    setSaving(true);
    try {
      await axios.post(`${API_BASE}/admin/edu/allowlist`,
        { domain, status: form.status, note: form.note?.trim() || '' },
        { headers: adminHeaders(adminToken), withCredentials: true },
      );
      toast.success(`${form.status === 'blocked' ? 'Blocked' : 'Allowed'} ${domain}`);
      setForm({ domain: '', status: 'allowed', note: '' });
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Could not save domain');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (domain) => {
    if (!confirm(`Remove override for "${domain}"?\nThe domain will fall back to the default rules.`)) return;
    try {
      await axios.delete(`${API_BASE}/admin/edu/allowlist/${encodeURIComponent(domain)}`, {
        headers: adminHeaders(adminToken),
        withCredentials: true,
      });
      toast.success(`Removed ${domain}`);
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Could not remove domain');
    }
  };

  const filteredOverrides = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return overrides;
    return overrides.filter((o) =>
      (o.domain || '').toLowerCase().includes(q) ||
      (o.note || '').toLowerCase().includes(q) ||
      (o.actor || '').toLowerCase().includes(q),
    );
  }, [overrides, filter]);

  return (
    <div className="space-y-4">
      {/* Add override card */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
        <div className="flex items-center justify-between p-4 border-b border-gray-100">
          <div>
            <h3 className="text-sm font-bold text-gray-900">Add or override a domain</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              Changes take effect within ~60 seconds (allowlist cache TTL).
            </p>
          </div>
          <button
            type="button"
            onClick={load}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-violet-600 px-2 py-1 rounded-lg hover:bg-gray-50"
            title="Refresh"
            disabled={loading}
          >
            <RefreshCcw size={13} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>
        <form onSubmit={handleAdd} className="p-4 grid grid-cols-1 md:grid-cols-12 gap-3">
          <div className="md:col-span-5">
            <label className="block text-[11px] font-semibold text-gray-500 mb-1 uppercase tracking-wide">Domain</label>
            <div className="relative">
              <Globe size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" aria-hidden="true" />
              <input
                type="text"
                value={form.domain}
                onChange={(e) => setForm((f) => ({ ...f, domain: e.target.value }))}
                placeholder="example.edu.in"
                spellCheck={false}
                autoComplete="off"
                className="w-full h-9 pl-8 pr-3 rounded-lg text-sm text-gray-900 bg-gray-50 border border-gray-200 focus:border-violet-400 focus:bg-white outline-none"
              />
            </div>
          </div>
          <div className="md:col-span-2">
            <label className="block text-[11px] font-semibold text-gray-500 mb-1 uppercase tracking-wide">Status</label>
            <select
              value={form.status}
              onChange={(e) => setForm((f) => ({ ...f, status: e.target.value }))}
              className="w-full h-9 px-2 rounded-lg text-sm text-gray-900 bg-gray-50 border border-gray-200 focus:border-violet-400 focus:bg-white outline-none"
            >
              <option value="allowed">Allowed</option>
              <option value="blocked">Blocked</option>
            </select>
          </div>
          <div className="md:col-span-3">
            <label className="block text-[11px] font-semibold text-gray-500 mb-1 uppercase tracking-wide">Note (optional)</label>
            <input
              type="text"
              value={form.note}
              onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
              placeholder="Why is this needed?"
              maxLength={280}
              className="w-full h-9 px-3 rounded-lg text-sm text-gray-900 bg-gray-50 border border-gray-200 focus:border-violet-400 focus:bg-white outline-none"
            />
          </div>
          <div className="md:col-span-2 flex items-end">
            <button
              type="submit"
              disabled={saving || !form.domain.trim()}
              className="w-full h-9 inline-flex items-center justify-center gap-1.5 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </form>
      </div>

      {/* Overrides table */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-2 p-4 border-b border-gray-100">
          <div>
            <h3 className="text-sm font-bold text-gray-900">Operator overrides</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              Stored in MongoDB — add/remove without a deploy. Total: {overrides.length}
            </p>
          </div>
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" aria-hidden="true" />
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search domain, note, actor…"
              className="h-8 pl-8 pr-3 rounded-lg text-xs text-gray-900 bg-gray-50 border border-gray-200 focus:border-violet-400 focus:bg-white outline-none w-full md:w-64"
            />
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-500">
              <tr>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Domain</th>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Status</th>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Note</th>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Actor</th>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Updated</th>
                <th className="text-right font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr><td colSpan={6} className="px-4 py-10 text-center text-gray-400 text-sm">
                  <Loader2 size={16} className="inline animate-spin mr-2" /> Loading overrides…
                </td></tr>
              ) : filteredOverrides.length === 0 ? (
                <tr><td colSpan={6} className="px-4 py-10 text-center text-gray-400 text-sm">
                  {overrides.length === 0
                    ? 'No operator overrides yet — the reader is running on the curated base list.'
                    : 'No matches for your search.'}
                </td></tr>
              ) : filteredOverrides.map((o) => (
                <tr key={o.domain} className="hover:bg-gray-50">
                  <td className="px-4 py-2.5 font-mono text-xs text-gray-900">
                    {o.domain}
                    <SourceBadge source={o.source} />
                  </td>
                  <td className="px-4 py-2.5"><StatusBadge status={o.status} /></td>
                  <td className="px-4 py-2.5 text-xs text-gray-600 max-w-xs truncate" title={o.note || ''}>{o.note || '—'}</td>
                  <td className="px-4 py-2.5 text-xs text-gray-500 max-w-[160px] truncate" title={o.actor || ''}>{o.actor || '—'}</td>
                  <td className="px-4 py-2.5 text-xs text-gray-500">{_fmtTime(o.updated_at)}</td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={() => handleDelete(o.domain)}
                      className="inline-flex items-center gap-1 text-xs font-medium text-rose-600 hover:bg-rose-50 px-2 py-1 rounded-lg"
                      title="Remove override"
                    >
                      <Trash2 size={12} /> Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Effective allowlist summary */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
        <div className="p-4 border-b border-gray-100">
          <h3 className="text-sm font-bold text-gray-900">Effective allowlist</h3>
          <p className="text-xs text-gray-500 mt-0.5 flex items-center gap-1">
            <Info size={11} /> The reader accepts a URL if its host matches any entry below OR ends with an edu suffix.
          </p>
        </div>
        <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
          <EffectiveBlock
            title="Base (baked-in)"
            items={effective?.base || []}
            loading={loading && !effective}
            tone="emerald"
            hint="Shipped with the release."
          />
          <EffectiveBlock
            title="Operator allowed"
            items={effective?.operator_allowed || []}
            loading={loading && !effective}
            tone="violet"
            hint="Added by admins above."
          />
          <EffectiveBlock
            title="Operator blocked"
            items={effective?.operator_blocked || []}
            loading={loading && !effective}
            tone="rose"
            hint="Overrides base — wins on conflict."
          />
          <EffectiveBlock
            title="Hard-denied"
            items={effective?.hard_denied || []}
            loading={loading && !effective}
            tone="slate"
            hint="Never fetchable — adult/unsafe."
          />
          <EffectiveBlock
            title="Auto-allowed suffixes"
            items={effective?.edu_suffixes || []}
            loading={loading && !effective}
            tone="sky"
            hint="Any host ending in these is accepted."
            mono
          />
        </div>
      </div>
    </div>
  );
}

const TONE_CLS = {
  emerald: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  violet:  'bg-violet-50 text-violet-700 border-violet-200',
  rose:    'bg-rose-50 text-rose-700 border-rose-200',
  slate:   'bg-slate-50 text-slate-700 border-slate-200',
  sky:     'bg-sky-50 text-sky-700 border-sky-200',
};

function EffectiveBlock({ title, items, tone = 'violet', hint, mono = false, loading = false }) {
  return (
    <div className="rounded-lg border border-gray-200 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 bg-gray-50 border-b border-gray-200">
        <span className="text-xs font-semibold text-gray-700">{title}</span>
        <span className="text-[11px] text-gray-400">{loading ? '—' : items.length}</span>
      </div>
      <div className="p-3 flex flex-wrap gap-1.5 max-h-48 overflow-y-auto">
        {loading ? (
          <span className="text-[11px] text-gray-400 inline-flex items-center gap-1">
            <Loader2 size={11} className="animate-spin" /> Loading…
          </span>
        ) : items.length === 0 ? (
          <span className="text-[11px] text-gray-400 italic">None.</span>
        ) : items.map((d) => (
          <span
            key={d}
            className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] ${mono ? 'font-mono' : ''} ${TONE_CLS[tone]}`}
          >
            {d}
          </span>
        ))}
      </div>
      {hint && <div className="px-3 pb-2 text-[10px] text-gray-400">{hint}</div>}
    </div>
  );
}

function BlockedLogTab({ adminToken }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [limit, setLimit] = useState(200);
  const [filter, setFilter] = useState('');
  const [sessionAllowed, setSessionAllowed] = useState(() => new Set());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/admin/edu/blocked-log`, {
        params: { limit },
        headers: adminHeaders(adminToken),
        withCredentials: true,
      });
      setItems(Array.isArray(res.data?.items) ? res.data.items : []);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Could not load blocked log');
    } finally {
      setLoading(false);
    }
  }, [adminToken, limit]);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) =>
      (it.domain || '').toLowerCase().includes(q) ||
      (it.reason || '').toLowerCase().includes(q) ||
      (it.url || '').toLowerCase().includes(q),
    );
  }, [items, filter]);

  const domainSummary = useMemo(() => {
    const tally = new Map();
    for (const it of items) {
      const d = it.domain || '(unknown)';
      tally.set(d, (tally.get(d) || 0) + 1);
    }
    return [...tally.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  }, [items]);

  const quickAllow = async (domain) => {
    if (!domain || domain === '(unknown)') return;
    if (!confirm(`Add "${domain}" to the allowlist?`)) return;
    try {
      await axios.post(`${API_BASE}/admin/edu/allowlist`,
        { domain, status: 'allowed', note: 'quick-allow from blocked log' },
        { headers: adminHeaders(adminToken), withCredentials: true },
      );
      setSessionAllowed((s) => { const n = new Set(s); n.add(domain); return n; });
      toast.success(`${domain} allowed — effective within ~60s`);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Could not allow domain');
    }
  };

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
        <div className="p-4 border-b border-gray-100 flex flex-col md:flex-row md:items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
              <Ban size={14} className="text-rose-500" /> Recent blocks
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">
              Showing {filtered.length} of {items.length} in the last {limit} records.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="h-8 px-2 rounded-lg text-xs text-gray-700 bg-gray-50 border border-gray-200 focus:border-violet-400 outline-none"
            >
              <option value={50}>Last 50</option>
              <option value={200}>Last 200</option>
              <option value={500}>Last 500</option>
              <option value={1000}>Last 1000</option>
            </select>
            <div className="relative">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" aria-hidden="true" />
              <input
                type="search"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter by domain/reason"
                className="h-8 pl-8 pr-3 rounded-lg text-xs text-gray-900 bg-gray-50 border border-gray-200 focus:border-violet-400 focus:bg-white outline-none w-48 md:w-64"
              />
            </div>
            <button
              type="button"
              onClick={load}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-violet-600 px-2 py-1 rounded-lg hover:bg-gray-50"
              disabled={loading}
            >
              <RefreshCcw size={13} className={loading ? 'animate-spin' : ''} /> Refresh
            </button>
          </div>
        </div>

        {domainSummary.length > 0 && (
          <div className="px-4 py-3 border-b border-gray-100 bg-amber-50/50">
            <div className="text-[11px] uppercase font-semibold tracking-wide text-amber-700 mb-2 flex items-center gap-1">
              <AlertTriangle size={11} /> Top blocked domains
            </div>
            <div className="flex flex-wrap gap-2">
              {domainSummary.map(([d, n]) => (
                <div key={d} className="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-white px-2 py-1">
                  <span className="font-mono text-[11px] text-gray-700">{d}</span>
                  <span className="text-[10px] font-semibold text-amber-700">×{n}</span>
                  {d !== '(unknown)' && (
                    sessionAllowed.has(d) ? (
                      <span className="text-[10px] font-semibold text-emerald-600 inline-flex items-center gap-0.5">
                        <ShieldCheck size={10} /> allowed
                      </span>
                    ) : (
                      <button
                        onClick={() => quickAllow(d)}
                        className="text-[10px] font-semibold text-violet-600 hover:text-violet-700 hover:underline"
                        title="Add this domain to the allowlist"
                      >
                        allow
                      </button>
                    )
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-500">
              <tr>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Domain</th>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Reason</th>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">URL</th>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">When</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr><td colSpan={4} className="px-4 py-10 text-center text-gray-400 text-sm">
                  <Loader2 size={16} className="inline animate-spin mr-2" /> Loading blocks…
                </td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan={4} className="px-4 py-10 text-center text-gray-400 text-sm">
                  {items.length === 0 ? 'No blocked requests yet.' : 'No matches for your filter.'}
                </td></tr>
              ) : filtered.map((it, i) => (
                <tr key={`${it.ts}_${i}`} className="hover:bg-gray-50">
                  <td className="px-4 py-2.5 font-mono text-xs text-gray-900">{it.domain || '—'}</td>
                  <td className="px-4 py-2.5"><ReasonBadge reason={it.reason} /></td>
                  <td className="px-4 py-2.5 text-xs text-gray-500 max-w-md truncate font-mono" title={it.url || ''}>{it.url || '—'}</td>
                  <td className="px-4 py-2.5 text-xs text-gray-500 whitespace-nowrap">{_fmtTime(it.ts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function RequestedSitesTab({ adminToken, initialDomain = '' }) {
  const [loading, setLoading] = useState(true);
  const [items, setItems] = useState([]);
  // When this tab is entered via a deep-link (?tab=requested&domain=X)
  // from a Slack appeal-spike alert, prefilter to the appeals view
  // and preload the domain into the search box so the admin lands on
  // exactly the row that fired the alert.
  const [filter, setFilter] = useState(() => (initialDomain ? 'appeals' : 'all')); // all | appeals
  const [search, setSearch] = useState(() => initialDomain || '');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/admin/edu/requested-sites`, {
        headers: adminHeaders(adminToken),
        withCredentials: true,
        params: { limit: 200 },
      });
      const list = Array.isArray(res.data?.items) ? res.data.items : (Array.isArray(res.data) ? res.data : []);
      setItems(list);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Could not load requested sites');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    let out = items;
    if (filter === 'appeals') {
      out = out.filter((i) => i.source === 'educator_appeal' || i.appeal === true);
    }
    const q = search.trim().toLowerCase();
    if (q) {
      out = out.filter((i) =>
        (i.domain || '').toLowerCase().includes(q) ||
        (i.last_actor || '').toLowerCase().includes(q) ||
        (i.last_reason || '').toLowerCase().includes(q),
      );
    }
    return out;
  }, [items, filter, search]);

  const appealCount = useMemo(
    () => items.filter((i) => i.source === 'educator_appeal' || i.appeal === true).length,
    [items],
  );

  // Task #625 — a domain is "spiking" when 5+ educators have appealed
  // the same probe rejection. Surface these at the top so admins catch
  // a regressed safety probe within minutes rather than when someone
  // happens to open this tab.
  const APPEAL_SPIKE_THRESHOLD = 5;
  const spikingDomains = useMemo(
    () => items.filter((i) =>
      (i.appeal_count || 0) >= APPEAL_SPIKE_THRESHOLD
      && (i.source === 'educator_appeal' || i.appeal === true)
      && !i.dismissed,
    ),
    [items],
  );

  const allowDomain = async (domain, note = '') => {
    try {
      await axios.post(`${API_BASE}/admin/edu/allowlist`,
        { domain, status: 'allowed', note: note?.slice(0, 280) || 'Approved from educator appeal' },
        { headers: adminHeaders(adminToken), withCredentials: true },
      );
      // Best-effort: clear it from the requested-sites queue so the
      // pending count and appeal badge stop showing stale work.
      try {
        await axios.delete(`${API_BASE}/admin/edu/requested-sites/${encodeURIComponent(domain)}`, {
          headers: adminHeaders(adminToken), withCredentials: true,
        });
      } catch (_) {
        // Non-fatal — the allowlist write is what matters.
      }
      toast.success(`Allowed ${domain}`);
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Could not allow domain');
    }
  };

  const dismissRequest = async (domain) => {
    if (!confirm(`Dismiss request for "${domain}" without allowing?`)) return;
    try {
      await axios.delete(`${API_BASE}/admin/edu/requested-sites/${encodeURIComponent(domain)}`, {
        headers: adminHeaders(adminToken), withCredentials: true,
      });
      toast.success(`Dismissed ${domain}`);
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Could not dismiss request');
    }
  };

  return (
    <div className="space-y-4">
      {spikingDomains.length > 0 && (
        <div
          className="rounded-xl border border-amber-300 bg-amber-50 shadow-sm p-4"
          data-testid="edu-appeal-spike-banner"
        >
          <div className="flex items-start gap-3">
            <div className="mt-0.5 rounded-full bg-amber-100 p-1.5">
              <Megaphone size={16} className="text-amber-700" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-bold text-amber-900">
                Educator appeals spiking on {spikingDomains.length === 1
                  ? '1 domain'
                  : `${spikingDomains.length} domains`}
              </div>
              <p className="text-xs text-amber-800 mt-0.5">
                {spikingDomains.length === 1
                  ? `5+ educators have appealed the probe rejection for this domain — the safety probe may be mis-classifying it. Review below.`
                  : `5+ educators appealed each of these domains — the safety probe may be mis-classifying them. Review below.`}
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {spikingDomains.map((it) => (
                  <button
                    key={it.domain}
                    type="button"
                    onClick={() => { setFilter('appeals'); setSearch(it.domain); }}
                    className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-white px-2 py-0.5 text-[11px] font-mono text-amber-900 hover:bg-amber-100"
                    title={`Filter the table to ${it.domain}`}
                    data-testid={`edu-appeal-spike-chip-${it.domain}`}
                  >
                    {it.domain}
                    <span className="text-amber-600 font-sans">· {it.appeal_count} appeals</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-2 p-4 border-b border-gray-100">
          <div>
            <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
              <Inbox size={14} className="text-violet-500" /> Site requests &amp; educator appeals
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">
              {items.length} pending · {appealCount} educator appeal{appealCount === 1 ? '' : 's'}.
              Educator appeals come from the rejection card and include the probe outcome they saw.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="h-8 px-2 rounded-lg text-xs text-gray-700 bg-gray-50 border border-gray-200 focus:border-violet-400 outline-none"
              data-testid="edu-requested-filter"
            >
              <option value="all">All requests</option>
              <option value="appeals">Educator appeals only</option>
            </select>
            <div className="relative">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" aria-hidden="true" />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Filter by domain/actor"
                className="h-8 pl-8 pr-3 rounded-lg text-xs text-gray-900 bg-gray-50 border border-gray-200 focus:border-violet-400 focus:bg-white outline-none w-48 md:w-64"
              />
            </div>
            <button
              type="button"
              onClick={load}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-violet-600 px-2 py-1 rounded-lg hover:bg-gray-50"
              disabled={loading}
            >
              <RefreshCcw size={13} className={loading ? 'animate-spin' : ''} /> Refresh
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-500">
              <tr>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Domain</th>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Source</th>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Last reason</th>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Probe</th>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Requests</th>
                <th className="text-left font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">When</th>
                <th className="text-right font-semibold px-4 py-2 text-[11px] uppercase tracking-wide">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr><td colSpan={7} className="px-4 py-10 text-center text-gray-400 text-sm">
                  <Loader2 size={16} className="inline animate-spin mr-2" /> Loading requests…
                </td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan={7} className="px-4 py-10 text-center text-gray-400 text-sm">
                  {items.length === 0 ? 'No site requests yet.' : 'No matches for your filter.'}
                </td></tr>
              ) : filtered.map((it, i) => {
                const isAppeal = it.source === 'educator_appeal' || it.appeal === true;
                const probe = it.last_probe || {};
                return (
                  <tr key={`${it.domain}_${i}`} className="hover:bg-gray-50 align-top">
                    <td className="px-4 py-2.5 font-mono text-xs text-gray-900">
                      {it.domain || '—'}
                      <SourceBadge source={it.source} />
                    </td>
                    <td className="px-4 py-2.5 text-xs">
                      {isAppeal ? (
                        <span
                          className="inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200 text-amber-800 px-1.5 py-0.5 text-[10px] font-semibold"
                          title="Educator appealed a probe rejection"
                          data-testid="edu-appeal-badge"
                        >
                          <Megaphone size={10} /> Educator appeal
                        </span>
                      ) : (
                        <span className="text-gray-500">{it.source || 'student'}</span>
                      )}
                      {it.last_actor && (
                        <div className="text-[11px] text-gray-400 mt-0.5 truncate max-w-[180px]" title={it.last_actor}>
                          {it.last_actor}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-gray-600 max-w-xs">
                      <div className="truncate" title={it.last_reason || ''}>{it.last_reason || '—'}</div>
                    </td>
                    <td className="px-4 py-2.5 text-[11px] text-gray-500 whitespace-nowrap">
                      {probe.reason ? <div className="font-mono">{probe.reason}</div> : null}
                      {typeof probe.kid_safe_density === 'number' && (
                        <div>kid-safe: {(probe.kid_safe_density * 100).toFixed(0)}%</div>
                      )}
                      {typeof probe.robots_ok === 'boolean' && (
                        <div>robots: {probe.robots_ok ? 'ok' : 'block'}</div>
                      )}
                      {typeof probe.http_status === 'number' && (
                        <div>HTTP {probe.http_status}</div>
                      )}
                      {!probe.reason && typeof probe.kid_safe_density !== 'number'
                        && typeof probe.robots_ok !== 'boolean'
                        && typeof probe.http_status !== 'number' && '—'}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-gray-700 whitespace-nowrap">
                      <div>{it.count ?? 1}×</div>
                      {it.appeal_count ? (
                        <div className="text-[10px] text-amber-700">{it.appeal_count} appeal{it.appeal_count === 1 ? '' : 's'}</div>
                      ) : null}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-gray-500 whitespace-nowrap">
                      {_fmtTime(it.last_at || it.first_at)}
                    </td>
                    <td className="px-4 py-2.5 text-right whitespace-nowrap">
                      <div className="inline-flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => allowDomain(it.domain, it.last_reason)}
                          className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-700 hover:text-emerald-800 px-2 py-1 rounded-md hover:bg-emerald-50"
                          title="Add to allowlist and clear from queue"
                        >
                          <CheckCircle2 size={12} /> Allow
                        </button>
                        <button
                          type="button"
                          onClick={() => dismissRequest(it.domain)}
                          className="inline-flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-rose-700 px-2 py-1 rounded-md hover:bg-rose-50"
                          title="Dismiss without allowing"
                        >
                          <Trash2 size={12} /> Dismiss
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function GroundedRecallTile({ adminToken, language = null, label = 'Grounded-answer recall' }) {
  const [state, setState] = useState({ loading: true, data: null, err: null });

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true }));
    try {
      const r = await axios.get(`${API_BASE}/admin/grounded-recall/latest`, {
        headers: adminHeaders(adminToken),
        withCredentials: true,
        params: language ? { language } : undefined,
      });
      setState({ loading: false, data: r.data || null, err: null });
    } catch (e) {
      setState({ loading: false, data: null, err: e?.response?.data?.detail || e.message || 'Failed' });
    }
  }, [adminToken, language]);

  useEffect(() => { load(); }, [load]);

  const { loading, data, err } = state;
  const latest = data?.latest || null;
  const baseline = data?.baseline || null;
  const metrics = latest?.metrics || null;

  const renderMetric = (key) => {
    if (!metrics) return null;
    const cur = metrics[key];
    const base = baseline?.metrics?.[key];
    let Icon = Minus;
    let color = 'text-gray-500';
    if (base != null && cur != null) {
      const delta = cur - base;
      if (delta > 0.001) { Icon = TrendingUp; color = 'text-emerald-600'; }
      else if (delta < -0.001) { Icon = TrendingDown; color = 'text-rose-600'; }
    }
    const pct = cur != null ? `${(cur * 100).toFixed(1)}%` : '—';
    const baseText = base != null ? `baseline ${(base * 100).toFixed(1)}%` : 'no baseline';
    return (
      <div key={key} className="flex-1 min-w-[140px] rounded-md border border-gray-200 bg-white px-3 py-2">
        <div className="text-[10px] uppercase tracking-wide text-gray-500 font-semibold">{key}</div>
        <div className="flex items-baseline gap-1.5 mt-0.5">
          <span className="text-lg font-bold text-gray-900">{pct}</span>
          <Icon size={13} className={color} />
        </div>
        <div className="text-[10px] text-gray-400 mt-0.5">{baseText}</div>
      </div>
    );
  };

  return (
    <div className="rounded-lg border border-violet-100 bg-violet-50/40 p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <Target size={14} className="text-violet-600" />
          <h3 className="text-xs font-bold text-gray-900">{label}</h3>
          {language && (
            <span
              className="text-[10px] uppercase tracking-wide rounded bg-violet-100 border border-violet-200 px-1.5 py-0.5 text-violet-700 font-semibold"
              data-testid={`recall-lang-badge-${language}`}
            >
              {language === 'as' ? 'অসমীয়া · as' : language}
            </span>
          )}
          {latest?.started_at && (
            <span className="text-[11px] text-gray-500">· {_fmtTime(latest.started_at)}</span>
          )}
          {latest?.retriever && (
            <span className="text-[10px] uppercase tracking-wide rounded bg-white border border-gray-200 px-1.5 py-0.5 text-gray-600">
              {latest.retriever}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={load}
          className="inline-flex items-center gap-1 text-[11px] text-gray-600 hover:text-gray-900"
          data-testid="recall-refresh"
        >
          <RefreshCcw size={11} /> Refresh
        </button>
      </div>
      {loading ? (
        <div className="text-xs text-gray-500 flex items-center gap-2">
          <Loader2 size={12} className="animate-spin" /> Loading…
        </div>
      ) : err ? (
        <div className="text-xs text-rose-600">{err}</div>
      ) : !latest ? (
        <div className="text-xs text-gray-600">
          No benchmark runs yet. Run <code className="bg-white px-1 rounded border border-gray-200">python -m bench.grounded_recall --save-results</code> to populate this tile.
        </div>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            {['recall@1', 'recall@3', 'recall@5'].map(renderMetric)}
            <div className="flex-1 min-w-[140px] rounded-md border border-gray-200 bg-white px-3 py-2">
              <div className="text-[10px] uppercase tracking-wide text-gray-500 font-semibold">cases · latency</div>
              <div className="text-lg font-bold text-gray-900 mt-0.5">
                {latest.total_cases}
                <span className="ml-1 text-xs font-normal text-gray-500">cases</span>
              </div>
              <div className="text-[10px] text-gray-400 mt-0.5">mean {latest.mean_latency_ms?.toFixed?.(0) ?? '—'} ms</div>
            </div>
          </div>
          {(metrics?.adversarial_clean_rate != null || metrics?.adversarial_mean_citations != null) && (
            <div className="mt-2 flex flex-wrap gap-2" data-testid="recall-adversarial-row">
              {metrics?.adversarial_clean_rate != null && (
                <div className="flex-1 min-w-[160px] rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2">
                  <div className="text-[10px] uppercase tracking-wide text-amber-700 font-semibold">
                    adversarial clean rate
                  </div>
                  <div className="text-lg font-bold text-gray-900 mt-0.5">
                    {(metrics.adversarial_clean_rate * 100).toFixed(1)}%
                  </div>
                  <div className="text-[10px] text-gray-500 mt-0.5">
                    {baseline?.metrics?.adversarial_clean_rate != null
                      ? `baseline ${(baseline.metrics.adversarial_clean_rate * 100).toFixed(1)}%`
                      : 'no baseline'}
                  </div>
                </div>
              )}
              {metrics?.adversarial_mean_citations != null && (
                <div className="flex-1 min-w-[160px] rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2">
                  <div className="text-[10px] uppercase tracking-wide text-amber-700 font-semibold">
                    adversarial mean citations
                  </div>
                  <div className="text-lg font-bold text-gray-900 mt-0.5">
                    {metrics.adversarial_mean_citations.toFixed(2)}
                  </div>
                  <div className="text-[10px] text-gray-500 mt-0.5">
                    {baseline?.metrics?.adversarial_mean_citations != null
                      ? `baseline ${baseline.metrics.adversarial_mean_citations.toFixed(2)}`
                      : 'no baseline'}
                    {' · lower is better'}
                  </div>
                </div>
              )}
            </div>
          )}
          {baseline && (
            <p className="text-[11px] text-gray-500 mt-2">
              Baseline locked on {baseline.recorded_at?.slice(0, 10) || 'unknown'} · {baseline.total_cases} cases.
              Nightly runs re-publish these numbers.
            </p>
          )}
        </>
      )}
    </div>
  );
}

// Task #625 — parse `?tab=requested&domain=<d>` from the URL so a
// Slack appeal-spike alert can deep-link admins straight to the Site
// requests tab filtered to the spiking domain. Exported as a pure
// function on `search` so it's testable without a DOM.
export function parseAdminEduDeepLink(search) {
  try {
    const params = new URLSearchParams(search || '');
    const rawTab = (params.get('tab') || '').toLowerCase();
    const tab = rawTab === 'requested' || rawTab === 'allowlist' || rawTab === 'blocked' ? rawTab : null;
    const domain = (params.get('domain') || '').slice(0, 253);
    return { tab, domain };
  } catch (_) {
    return { tab: null, domain: '' };
  }
}

function _readDeepLink() {
  if (typeof window === 'undefined' || !window.location) return { tab: null, domain: '' };
  return parseAdminEduDeepLink(window.location.search || '');
}

export default function AdminEduBrowser({ adminToken }) {
  const deep = useMemo(_readDeepLink, []);
  const [tab, setTab] = useState(deep.tab || 'allowlist');

  return (
    <SectionErrorBoundary name="Edu Browser">
      <div className="space-y-4 max-w-6xl">
        <div>
          <h2 className="text-lg font-bold text-gray-900">Educational Browser</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Curate which sites Syra can read for students and audit what gets blocked.
          </p>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
          <GroundedRecallTile
            adminToken={adminToken}
            label="Grounded-answer recall · global"
          />
          <GroundedRecallTile
            adminToken={adminToken}
            language="as"
            label="Grounded-answer recall · Assamese"
          />
          <GroundedRecallTile
            adminToken={adminToken}
            language="bn"
            label="Grounded-answer recall · Bengali"
          />
          <GroundedRecallTile
            adminToken={adminToken}
            language="hi"
            label="Grounded-answer recall · Hindi"
          />
        </div>

        <div className="flex items-center gap-1 border-b border-gray-200">
          {[
            { id: 'allowlist', label: 'Allowlist', icon: ShieldCheck },
            { id: 'requested', label: 'Site requests', icon: Inbox },
            { id: 'blocked',   label: 'Blocked requests', icon: Ban },
          ].map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              className={`inline-flex items-center gap-1.5 px-3 py-2 text-xs font-semibold border-b-2 transition-colors ${
                tab === id
                  ? 'border-violet-500 text-violet-700'
                  : 'border-transparent text-gray-500 hover:text-gray-800'
              }`}
              data-testid={`edu-tab-${id}`}
            >
              <Icon size={13} /> {label}
            </button>
          ))}
        </div>

        {tab === 'allowlist' ? (
          <AllowlistTab adminToken={adminToken} />
        ) : tab === 'requested' ? (
          <RequestedSitesTab adminToken={adminToken} initialDomain={deep.domain} />
        ) : (
          <BlockedLogTab adminToken={adminToken} />
        )}
      </div>
    </SectionErrorBoundary>
  );
}
