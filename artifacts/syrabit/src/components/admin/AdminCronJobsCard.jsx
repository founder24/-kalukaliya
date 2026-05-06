import React, { useState, useEffect, useCallback, useMemo } from 'react';
import axios from 'axios';
import { ShieldCheck, AlertTriangle, RefreshCw, Clock, ExternalLink } from 'lucide-react';
import { API_BASE } from '@/utils/api';

// AdminCronJobsCard
// =================
//
// Phase 4 — Cron port (Task #332).
//
// Replaces the GCP Cloud Scheduler-sourced rendering inside
// CronHealthPill. Polls the Azure Container Apps Jobs run history
// (proxied by `routes/admin_azure_cron.py` on the backend so the
// React bundle never holds an Azure ARM token).
//
// Backend contract — `GET /admin/azure/cron/health`:
// {
// asOf: "2026-05-04T12:34:56Z",
// composite: "ok" | "degraded" | "failed" | "unknown",
// jobs: [
// {
// key:           "seo-auto-publish",
// jobName:       "aca-job-seo-auto-publish",
// kind:          "scheduler" | "loop",
// cron:          "*/15 * * * *",
// lastRunAt:     "2026-05-04T12:30:00Z",
// lastRunStatus: "Succeeded" | "Failed" | "Running" | "Aborted",
// consecutiveFailures: 0,
// nextRunAt:     "2026-05-04T12:45:00Z",
// alertState:    "OK" | "ALARM" | "INSUFFICIENT_DATA"
// }, ...
// ]
// }
//
// ─ Why a separate card and not a re-source inside CronHealthPill ─
// CronHealthPill renders one *named* cron with a workflow URL and an
// alert-history drawer; the Azure source lists ~48 jobs. Trying to
// fan that count through CronHealthPill's per-pill render path would
// blow the AdminHealth scroll length and double the existing
// per-cron useState plumbing for no benefit. This card lists them
// in a compact table; CronHealthPill keeps rendering the few crons
// that actually have a paged-history drawer (Trustpilot refresh,
// edge-proxy deploy, CF-WAF drift, unified-logs CF pull). Those four
// pills now read from Azure too (see the same backend route, fields
// `lastRunAt` + `lastRunStatus`) but their tile shape is unchanged.

const STATUS_STYLES = {
  ok:       { wrap: 'bg-emerald-50 border border-emerald-200', icon: <ShieldCheck size={20} className="text-emerald-500" />, text: 'text-emerald-700', label: 'Cron — all jobs green' },
  degraded: { wrap: 'bg-amber-50 border border-amber-200',     icon: <AlertTriangle size={20} className="text-amber-500" />, text: 'text-amber-700',   label: 'Cron — at least one job late or failing' },
  failed:   { wrap: 'bg-red-50 border border-red-200',         icon: <AlertTriangle size={20} className="text-red-500" />,   text: 'text-red-700',     label: 'Cron — paged failure in last 15 min' },
  unknown:  { wrap: 'bg-gray-50 border border-gray-200',       icon: <Clock size={20} className="text-gray-400" />,          text: 'text-gray-500',    label: 'Cron — health probe pending' },
};

const adminHeaders = (token) => (token ? { Authorization: `Bearer ${token}` } : {});

function ageLabel(iso) {
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return 'in future';
  const m = Math.floor(ms / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export default function AdminCronJobsCard({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('all'); // all | scheduler | loop | failing

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    axios
      .get(`${API_BASE}/admin/azure/cron/health`, {
        headers: adminHeaders(adminToken),
        withCredentials: true,
      })
      .then((r) => setData(r.data))
      .catch((e) => setError(e?.response?.status ? `HTTP ${e.response.status}` : 'unreachable'))
      .finally(() => setLoading(false));
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  const composite = data?.composite || (error ? 'unknown' : 'unknown');
  const style = STATUS_STYLES[composite] || STATUS_STYLES.unknown;

  const jobs = useMemo(() => {
    const list = (data?.jobs || []).slice();
    list.sort((a, b) => {
      // Failing first, then late, then by name
      const rank = (j) => (j.lastRunStatus === 'Failed' ? 0
        : j.consecutiveFailures > 0 ? 1
          : j.alertState === 'ALARM' ? 2
            : 3);
      const r = rank(a) - rank(b);
      if (r !== 0) return r;
      return a.key.localeCompare(b.key);
    });
    if (filter === 'all') return list;
    if (filter === 'failing') return list.filter((j) => j.lastRunStatus === 'Failed' || j.consecutiveFailures > 0);
    return list.filter((j) => j.kind === filter);
  }, [data, filter]);

  const counts = useMemo(() => {
    const all = data?.jobs || [];
    return {
      all: all.length,
      scheduler: all.filter((j) => j.kind === 'scheduler').length,
      loop: all.filter((j) => j.kind === 'loop').length,
      failing: all.filter((j) => j.lastRunStatus === 'Failed' || j.consecutiveFailures > 0).length,
    };
  }, [data]);

  const FilterBtn = ({ id, label }) => (
    <button
      onClick={() => setFilter(id)}
      className={`px-2.5 py-1 rounded-lg text-[11px] font-semibold border transition-colors ${
        filter === id
          ? 'bg-violet-50 border-violet-200 text-violet-700'
          : 'border-gray-200 text-gray-500 hover:text-gray-700'
      }`}
      data-testid={`admin-cron-filter-${id}`}
    >
      {label} <span className="text-gray-400 font-normal">({counts[id] ?? 0})</span>
    </button>
  );

  return (
    <div className="rounded-2xl bg-white border border-gray-200 p-4 space-y-4" data-testid="admin-cron-jobs-card">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          Cron · Azure Container Apps Jobs
        </h3>
        <button
          onClick={load}
          disabled={loading}
          className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100"
          data-testid="admin-cron-jobs-refresh"
          title="Refresh"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className={`rounded-2xl p-3 flex items-center gap-3 ${style.wrap}`} data-testid="admin-cron-jobs-banner">
        {style.icon}
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold ${style.text}`} data-testid="admin-cron-jobs-status">
            {error ? `Cron — health probe error (${error})` : style.label}
          </p>
          {data?.asOf && (
            <p className="text-[11px] text-gray-500 mt-0.5">
              Snapshot {new Date(data.asOf).toLocaleString()} · {counts.all} jobs
            </p>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        <FilterBtn id="all" label="All" />
        <FilterBtn id="scheduler" label="Scheduler" />
        <FilterBtn id="loop" label="Loop" />
        <FilterBtn id="failing" label="Failing" />
      </div>

      {jobs.length > 0 ? (
        <div className="overflow-x-auto -mx-1">
          <table className="w-full text-xs">
            <thead className="text-gray-400">
              <tr className="text-left">
                <th className="px-2 py-1 font-medium">Job</th>
                <th className="px-2 py-1 font-medium">Schedule</th>
                <th className="px-2 py-1 font-medium">Last run</th>
                <th className="px-2 py-1 font-medium">Status</th>
                <th className="px-2 py-1 font-medium">Next</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {jobs.map((j) => {
                const failing = j.lastRunStatus === 'Failed' || j.consecutiveFailures > 0;
                const rowCls = failing ? 'bg-red-50/50' : j.lastRunStatus === 'Aborted' ? 'bg-amber-50/40' : '';
                return (
                  <tr key={j.key} className={rowCls} data-testid={`admin-cron-job-row-${j.key}`}>
                    <td className="px-2 py-1.5 font-mono text-gray-700">
                      {j.jobName}
                      <span className="ml-1 text-[10px] text-gray-400">({j.kind})</span>
                    </td>
                    <td className="px-2 py-1.5 font-mono text-gray-500">{j.cron || '—'}</td>
                    <td className="px-2 py-1.5 text-gray-600" title={j.lastRunAt || ''}>
                      {ageLabel(j.lastRunAt)}
                    </td>
                    <td className="px-2 py-1.5">
                      <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border ${
                        j.lastRunStatus === 'Succeeded'
                          ? 'bg-emerald-100 text-emerald-700 border-emerald-200'
                          : j.lastRunStatus === 'Failed'
                            ? 'bg-red-100 text-red-700 border-red-200'
                            : j.lastRunStatus === 'Running'
                              ? 'bg-blue-100 text-blue-700 border-blue-200'
                              : j.lastRunStatus === 'Aborted'
                                ? 'bg-amber-100 text-amber-700 border-amber-200'
                                : 'bg-gray-100 text-gray-500 border-gray-200'
                      }`}>
                        {j.lastRunStatus || 'UNKNOWN'}
                      </span>
                      {j.consecutiveFailures > 1 && (
                        <span className="ml-1 text-[10px] text-red-600 font-semibold">×{j.consecutiveFailures}</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-gray-500" title={j.nextRunAt || ''}>{ageLabel(j.nextRunAt)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-[11px] text-gray-400">No jobs match this filter.</p>
      )}

      <p className="text-[11px] text-gray-400 mt-1">
        Source: Azure Container Apps Jobs run history (proxied through the backend so this bundle
        never holds an Azure ARM token). Per-job failure paging fans out via the existing
        <code className="mx-1 px-1 rounded bg-gray-100">ops_alerts</code> action group. Runbook:
        <a
          href="/docs/infra/cron-on-azure"
          className="text-violet-600 hover:text-violet-700 inline-flex items-center gap-0.5 ml-1"
        >
          cron-on-azure.md <ExternalLink size={10} />
        </a>
      </p>
    </div>
  );
}
