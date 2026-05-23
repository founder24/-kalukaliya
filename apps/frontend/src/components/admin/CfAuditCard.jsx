/**
 * Task #133 — Cloudflare weekly audit card for the admin health panel.
 *
 * Shows the latest cloudflare-weekly-audit.yml run (run date, conclusion,
 * and per-status item counts) so on-call can see the full 19-item audit
 * result without leaving the dashboard and navigating to GitHub Actions.
 *
 * Data source: GET /api/admin/health/cf-audit/latest
 * Backend: routes/admin_health.py _get_cf_audit_latest()
 *
 * Status mapping (matches EdgeProxyDeployCronPill convention):
 *   healthy   = last run succeeded, run age ≤ 8 days          → green badge
 *   silent    = last run failed (conclusion=failure)           → red badge
 *   degraded  = last run succeeded but age > 8 days            → amber badge
 *   never_observed = no runs found                             → gray badge
 *   not_configured = GITHUB_REPO env-var not set               → gray badge
 *   unknown   = GitHub API error                               → gray badge
 *
 * Count chips: PASS (emerald) / WARN (amber) / FAIL (red) / PLAN_REQUIRED (violet).
 * When the artifact download fails, chips are replaced by a subtle "counts unavailable" note.
 * Stale indicator: amber "⚠ stale" tag appended to the date when age > 8 days.
 * Clicking "View in GitHub" opens the run in a new tab.
 */

import React from 'react';
import { ExternalLink, RefreshCw, Shield, Clock, AlertTriangle, CheckCircle2, XCircle, AlertCircle, CreditCard } from 'lucide-react';

const STALE_THRESHOLD_S = 8 * 86400;

const STATUS_CONFIG = {
  healthy: {
    badge: 'bg-emerald-100 text-emerald-700 border-emerald-200',
    icon: <CheckCircle2 size={13} className="shrink-0" />,
    label: 'PASSING',
    headerColor: 'text-emerald-700',
    borderColor: 'border-emerald-200',
  },
  silent: {
    badge: 'bg-red-100 text-red-700 border-red-200',
    icon: <XCircle size={13} className="shrink-0" />,
    label: 'FAILING',
    headerColor: 'text-red-700',
    borderColor: 'border-red-200',
  },
  degraded: {
    badge: 'bg-amber-100 text-amber-700 border-amber-200',
    icon: <AlertTriangle size={13} className="shrink-0" />,
    label: 'STALE RUN',
    headerColor: 'text-amber-700',
    borderColor: 'border-amber-200',
  },
  never_observed: {
    badge: 'bg-gray-100 text-gray-500 border-gray-200',
    icon: <AlertCircle size={13} className="shrink-0" />,
    label: 'NEVER RUN',
    headerColor: 'text-gray-500',
    borderColor: 'border-gray-200',
  },
  not_configured: {
    badge: 'bg-gray-100 text-gray-500 border-gray-200',
    icon: <AlertCircle size={13} className="shrink-0" />,
    label: 'NOT CONFIGURED',
    headerColor: 'text-gray-500',
    borderColor: 'border-gray-200',
  },
  unknown: {
    badge: 'bg-gray-100 text-gray-500 border-gray-200',
    icon: <AlertCircle size={13} className="shrink-0" />,
    label: 'UNKNOWN',
    headerColor: 'text-gray-500',
    borderColor: 'border-gray-200',
  },
};

const HEADER_TEXT = {
  healthy:       'Cloudflare audit — all items passing',
  silent:        'Cloudflare audit — run failed',
  degraded:      'Cloudflare audit — run is stale',
  never_observed:'Cloudflare audit — no run yet',
  not_configured:'Cloudflare audit — not configured',
  unknown:       'Cloudflare audit — status unknown',
};

function formatAge(ageSeconds) {
  if (ageSeconds == null) return null;
  if (ageSeconds < 3600) return `${Math.floor(ageSeconds / 60)}m ago`;
  if (ageSeconds < 86400) return `${Math.floor(ageSeconds / 3600)}h ago`;
  return `${Math.floor(ageSeconds / 86400)}d ago`;
}

function CountChip({ label, value, colorClass, testId }) {
  return (
    <div className={`flex flex-col items-center rounded-xl px-3 py-2 border ${colorClass}`} data-testid={testId}>
      <span className="text-[10px] uppercase tracking-wider opacity-70 leading-none mb-1">{label}</span>
      <span className="text-lg font-bold font-mono leading-none">{value}</span>
    </div>
  );
}

export default function CfAuditCard({ data, loading, onRefresh }) {
  const status = data?.status || 'unknown';
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.unknown;
  const summary = data?.summary;
  const ageSeconds = data?.ageSeconds;
  const lastRunUrl = data?.lastRunUrl;
  const workflowUrl = data?.workflowUrl || 'https://github.com/syrabit/syrabit/actions/workflows/cloudflare-weekly-audit.yml';
  const updatedAt = data?.updatedAt;
  const isStale = ageSeconds != null && ageSeconds > (data?.staleThresholdSeconds || STALE_THRESHOLD_S);
  const ageLabel = formatAge(ageSeconds);
  const runNumber = data?.runNumber;
  const headSha = data?.headSha;
  const error = data?.error;

  const showCounts = summary != null;

  return (
    <div className={`rounded-2xl border bg-white shadow-sm overflow-hidden`} data-testid="cf-audit-card">
      {/* Header bar */}
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
        <div className="flex items-center gap-2 min-w-0">
          <Shield size={16} className={`shrink-0 ${cfg.headerColor}`} />
          <span className={`text-sm font-semibold truncate ${cfg.headerColor}`} data-testid="cf-audit-header">
            {HEADER_TEXT[status]}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {/* Status badge */}
          <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full border ${cfg.badge}`} data-testid="cf-audit-status-badge">
            {cfg.icon}
            {cfg.label}
          </span>
          {/* Refresh */}
          <button
            onClick={onRefresh}
            disabled={loading}
            title="Refresh"
            className="text-gray-400 hover:text-gray-600 transition-colors disabled:opacity-40"
            data-testid="cf-audit-refresh"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-3">
        {/* Error banner */}
        {error && (
          <div className="flex items-start gap-2 p-2.5 rounded-xl bg-amber-50 border border-amber-200 text-xs text-amber-700">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Not-configured hint */}
        {status === 'not_configured' && (
          <div className="flex items-start gap-2 p-2.5 rounded-xl bg-gray-50 border border-gray-200 text-xs text-gray-600">
            <AlertCircle size={13} className="mt-0.5 shrink-0" />
            <span>Set the <code className="font-mono bg-gray-100 px-1 rounded">GITHUB_REPO</code> env-var on the backend to enable this card.</span>
          </div>
        )}

        {/* Count chips: PASS / WARN / FAIL / PLAN_REQUIRED */}
        {showCounts ? (
          <div className="grid grid-cols-4 gap-2" data-testid="cf-audit-counts">
            <CountChip
              label="Pass"
              value={summary.pass}
              colorClass="bg-emerald-50 text-emerald-700 border-emerald-200"
              testId="cf-audit-count-pass"
            />
            <CountChip
              label="Warn"
              value={summary.warn}
              colorClass="bg-amber-50 text-amber-700 border-amber-200"
              testId="cf-audit-count-warn"
            />
            <CountChip
              label="Fail"
              value={summary.fail}
              colorClass={summary.fail > 0 ? 'bg-red-50 text-red-700 border-red-200' : 'bg-gray-50 text-gray-500 border-gray-200'}
              testId="cf-audit-count-fail"
            />
            <CountChip
              label={<span className="flex items-center gap-0.5"><CreditCard size={9} />Plan</span>}
              value={summary.plan_required}
              colorClass="bg-violet-50 text-violet-700 border-violet-200"
              testId="cf-audit-count-plan"
            />
          </div>
        ) : status !== 'never_observed' && status !== 'not_configured' && !loading ? (
          <p className="text-[11px] text-gray-400 italic">
            Item counts unavailable — artifact could not be downloaded.
          </p>
        ) : null}

        {/* Meta row: last run date + stale indicator + run number + sha */}
        {(ageLabel || updatedAt) && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500" data-testid="cf-audit-meta">
            <span className="flex items-center gap-1">
              <Clock size={10} />
              {ageLabel
                ? <span>Last run <strong className="text-gray-700">{ageLabel}</strong></span>
                : <span>Last run {new Date(updatedAt).toLocaleDateString()}</span>
              }
              {isStale && (
                <span className="ml-1 inline-flex items-center gap-0.5 bg-amber-100 text-amber-700 border border-amber-200 text-[10px] font-semibold px-1.5 py-0.5 rounded-full" data-testid="cf-audit-stale-badge">
                  <AlertTriangle size={9} /> stale
                </span>
              )}
            </span>
            {runNumber && (
              <span className="font-mono text-gray-400">#{runNumber}</span>
            )}
            {headSha && (
              <span className="font-mono text-gray-400">{headSha}</span>
            )}
          </div>
        )}

        {/* Loading skeleton */}
        {loading && !data && (
          <div className="space-y-2 animate-pulse">
            <div className="h-3 bg-gray-100 rounded w-2/3" />
            <div className="grid grid-cols-4 gap-2">
              {[...Array(4)].map((_, i) => (
                <div key={i} className="h-12 bg-gray-100 rounded-xl" />
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Footer: GitHub links */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-t border-gray-100 bg-gray-50/60">
        {lastRunUrl ? (
          <a
            href={lastRunUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] text-violet-600 hover:text-violet-700 inline-flex items-center gap-1 font-medium"
            data-testid="cf-audit-run-link"
          >
            View run <ExternalLink size={11} />
          </a>
        ) : (
          <a
            href={workflowUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] text-gray-400 hover:text-gray-600 inline-flex items-center gap-1"
            data-testid="cf-audit-workflow-link"
          >
            View workflow <ExternalLink size={11} />
          </a>
        )}
        {lastRunUrl && (
          <a
            href={workflowUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] text-gray-400 hover:text-gray-600 inline-flex items-center gap-1"
            data-testid="cf-audit-workflow-link"
          >
            All runs <ExternalLink size={11} />
          </a>
        )}
        {showCounts && (
          <span className="ml-auto text-[10px] text-gray-400 font-mono">
            {summary.total} items total
          </span>
        )}
      </div>
    </div>
  );
}
