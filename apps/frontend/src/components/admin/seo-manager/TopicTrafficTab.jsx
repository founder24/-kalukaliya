import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle, ChevronDown, ChevronLeft, ChevronRight, ExternalLink, Info,
  RefreshCw, Search, ShieldAlert,
} from 'lucide-react';
import { adminTopicTraffic } from '@/utils/api';

const PAGE_SIZE = 100;
const fmt = (value) => value == null || value === '' ? 'N/A' : Number(value).toLocaleString();
const dateLabel = (value, options = {}) => {
  if (!value) return 'N/A';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeZone: 'UTC', ...options }).format(date);
};
const timeLabel = (value) => {
  if (!value) return 'N/A';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
};
const humanize = (value) => String(value || 'unknown').replace(/[_-]+/g, ' ');
const showValue = (value) => value == null ? 'N/A' : fmt(value);

function Metric({ label, value, hint, testId }) {
  return (
    <div className="min-w-0 border-l-2 border-[#d5dfd9] pl-3" data-testid={testId}>
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-400">{label}</div>
      <div className="mt-1 font-mono text-xl font-semibold tracking-tight text-[#183c35]">{value}</div>
      {hint && <div className="mt-1 text-[10px] leading-4 text-gray-400">{hint}</div>}
    </div>
  );
}

function SourceCard({ title, status, note, children, testId }) {
  const statusText = humanize(status);
  const active = status === 'available' || status === 'active' || status === 'complete';
  return (
    <section className="rounded-lg border border-[#dfe6e1] bg-[#fbfcfa] p-3" data-testid={testId}>
      <div className="flex items-start justify-between gap-2">
        <h4 className="text-xs font-semibold text-[#29453e]">{title}</h4>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${active ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
          {statusText}
        </span>
      </div>
      {children && <div className="mt-2">{children}</div>}
      {note && <p className="mt-2 text-[10px] leading-4 text-gray-500">{note}</p>}
    </section>
  );
}

function StatusPill({ status }) {
  const palette = {
    met: 'bg-emerald-50 text-emerald-700',
    below_target: 'bg-amber-50 text-amber-800',
    in_progress: 'bg-sky-50 text-sky-700',
    not_available: 'bg-gray-100 text-gray-500',
    not_eligible: 'bg-gray-100 text-gray-500',
  };
  return (
    <span className={`inline-flex whitespace-nowrap rounded px-1.5 py-0.5 text-[9px] font-semibold ${palette[status] || 'bg-gray-100 text-gray-500'}`}>
      {humanize(status)}
    </span>
  );
}

function Count({ value }) {
  return <span className="font-mono tabular-nums">{showValue(value)}</span>;
}

export default function TopicTrafficTab({ adminToken }) {
  const [report, setReport] = useState(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [eligibility, setEligibility] = useState('all');
  const [expanded, setExpanded] = useState(null);
  const requestId = useRef(0);

  const load = useCallback(async (nextPage = page, isRefresh = false) => {
    const currentRequest = ++requestId.current;
    if (isRefresh && report) setRefreshing(true);
    else setLoading(true);
    setError('');
    try {
      const response = await adminTopicTraffic(adminToken, nextPage, PAGE_SIZE);
      if (currentRequest !== requestId.current) return;
      const nextReport = response?.data;
      if (!nextReport || typeof nextReport !== 'object') throw new Error('The traffic report response was empty.');
      setReport(nextReport);
      setPage(nextPage);
    } catch (err) {
      if (currentRequest !== requestId.current) return;
      setError(err?.response?.data?.detail || err?.message || 'Could not load topic traffic.');
    } finally {
      if (currentRequest === requestId.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [adminToken, page, report]);

  useEffect(() => {
    load(1);
    return () => { requestId.current += 1; };
  }, [adminToken]); // Load once when the operator/session changes.

  const topics = report?.topics || [];
  const filteredTopics = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return topics.filter((topic) => {
      const matchesEligibility = eligibility === 'all'
        || (eligibility === 'eligible' ? topic.eligible : !topic.eligible);
      const searchable = [
        topic.topic_title, topic.topic_name, topic.chapter_title, topic.subject_name,
        topic.language, topic.canonical_url, topic.exclusion_reason,
      ].filter(Boolean).join(' ').toLowerCase();
      return matchesEligibility && (!normalized || searchable.includes(normalized));
    });
  }, [topics, query, eligibility]);

  const inventory = report?.inventory || {};
  const pages = inventory.pages || {};
  const totalPages = Number(pages.total_pages) || 1;
  const coverage = report?.source_coverage || {};
  const analytics = coverage.first_party_analytics || {};
  const searchConsole = coverage.search_console || {};
  const cloudflare = coverage.cloudflare_request_logs || {};
  const tollbit = coverage.tollbit || {};
  const targets = report?.targets || {};
  const definitions = report?.definitions;
  const exclusions = Object.entries(inventory.exclusions_by_reason || {});
  const breakdown = report?.subject_language_breakdown || [];

  const goToPage = (next) => {
    if (next >= 1 && next <= totalPages && next !== page) {
      setExpanded(null);
      load(next);
    }
  };

  return (
    <div className="space-y-4 text-gray-700" data-testid="topic-traffic-tab">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-[#dce5df] pb-3">
        <div className="max-w-3xl">
          <div className="flex items-center gap-2">
            <span className="h-5 w-1 rounded-full bg-[#b46b42]" />
            <h3 className="text-base font-bold tracking-tight text-[#183c35]">Topic traffic evidence</h3>
            {report?.schema_version && (
              <span className="rounded border border-[#e0e7e2] px-1.5 py-0.5 font-mono text-[9px] text-gray-400" data-testid="topic-traffic-schema-version">
                {report.schema_version}
              </span>
            )}
          </div>
          <p className="mt-1 text-xs leading-5 text-gray-500">
            Coverage and measured discovery for canonical topic URLs. Keep each source distinct; unavailable measurements are shown as N/A, not zero.
          </p>
        </div>
        <button
          type="button"
          onClick={() => load(page, true)}
          disabled={loading || refreshing}
          className="inline-flex items-center gap-1.5 rounded-md border border-[#d6e0da] bg-white px-2.5 py-1.5 text-[11px] font-semibold text-[#35584e] transition-colors hover:bg-[#f2f6f3] disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="topic-traffic-refresh"
        >
          <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
          {refreshing ? 'Refreshing' : 'Refresh report'}
        </button>
      </header>

      {error && (
        <div className="flex items-start justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5" role="alert" data-testid="topic-traffic-error">
          <div className="flex gap-2 text-xs text-rose-800">
            <AlertCircle size={15} className="mt-0.5 shrink-0" />
            <div><strong>Report unavailable.</strong> {error}</div>
          </div>
          <button type="button" onClick={() => load(page)} className="shrink-0 rounded border border-rose-200 bg-white px-2 py-1 text-[10px] font-semibold text-rose-700 hover:bg-rose-100" data-testid="topic-traffic-retry">Retry</button>
        </div>
      )}

      {loading && !report ? (
        <div className="space-y-3" aria-label="Loading topic traffic report" data-testid="topic-traffic-loading">
          <div className="grid grid-cols-2 gap-3 rounded-lg border border-[#e0e7e2] bg-white p-4 sm:grid-cols-4">
            {[0, 1, 2, 3].map((item) => <div key={item} className="h-14 animate-pulse rounded bg-[#edf2ee]" />)}
          </div>
          <div className="h-28 animate-pulse rounded-lg border border-[#e0e7e2] bg-[#f5f8f5]" />
          <div className="h-64 animate-pulse rounded-lg border border-[#e0e7e2] bg-[#f5f8f5]" />
        </div>
      ) : report ? (
        <>
          <section className="overflow-hidden rounded-lg border border-[#dce5df] bg-white" data-testid="topic-traffic-inventory">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#e8eeea] bg-[#f6f8f5] px-3 py-2">
              <div className="text-[10px] font-bold uppercase tracking-[0.13em] text-[#62746b]">URL inventory · {inventory.version || 'version unavailable'}</div>
              <div className="text-[10px] text-gray-500" data-testid="topic-traffic-report-meta">
                Generated {timeLabel(report.generated_at)} · {report.timezone || 'timezone N/A'}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-y-4 px-3 py-4 sm:grid-cols-4">
              <Metric label="Candidates" value={fmt(inventory.candidate_url_count)} testId="topic-traffic-candidate-count" />
              <Metric label="Eligible URLs" value={fmt(inventory.eligible_url_count)} hint="Denominator for aspirational targets" testId="topic-traffic-eligible-count" />
              <Metric label="Excluded URLs" value={fmt(inventory.excluded_url_count)} hint="Reasons listed below" testId="topic-traffic-excluded-count" />
              <Metric label="Inventory page" value={`${pages.page ?? page} / ${pages.total_pages ?? 'N/A'}`} hint={`${fmt(pages.page_size)} URLs per page`} testId="topic-traffic-inventory-page" />
            </div>
            <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-[#edf1ee] px-3 py-2 text-[10px] text-gray-500" data-testid="topic-traffic-window">
               <span>UTC window: <strong className="font-medium text-gray-700">{dateLabel(report.window?.daily_window_start || report.window?.utc_start)} – {dateLabel(report.window?.daily_window_end || report.window?.utc_end)}</strong></span>
              <span>Rolling 7-day: <strong className="font-medium text-gray-700">{dateLabel(report.window?.rolling_7d_start)} – {dateLabel(report.window?.rolling_7d_end)}</strong></span>
            </div>
          </section>

          <section className="rounded-lg border border-[#dce5df] bg-white p-3" data-testid="topic-traffic-targets">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h4 className="text-xs font-bold text-[#29453e]">Aspirational targets, not verified outcomes</h4>
                <p className="mt-0.5 text-[10px] text-gray-500">Human counts are a consented browser-session proxy, not independently human-verified.</p>
              </div>
              <span className="inline-flex items-center gap-1 rounded bg-amber-50 px-2 py-1 text-[9px] font-semibold uppercase tracking-wide text-amber-800">
                <Info size={11} /> Current day is partial
              </span>
            </div>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-md bg-[#f5f8f5] px-3 py-2.5">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">Human discovery · per eligible URL</div>
                 <div className="mt-1 font-mono text-lg font-semibold text-[#183c35]">{fmt(targets.human?.per_eligible_url_per_day ?? targets.human?.per_url_per_day ?? targets.human?.daily_per_eligible_url ?? targets.human_discovery_visits_per_eligible_url_per_day ?? targets.human_per_eligible_url_per_day ?? targets.human_per_url_per_day ?? 10)} <span className="text-xs font-sans font-medium text-gray-500">visits / day</span></div>
              </div>
              <div className="rounded-md bg-[#f5f8f5] px-3 py-2.5">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">Verified crawler · per eligible URL</div>
                  <div className="mt-1 font-mono text-lg font-semibold text-[#183c35]">{fmt(targets.verified_crawler?.per_eligible_url_rolling_7d ?? targets.verified_crawler?.rolling_7d_per_eligible_url ?? targets.verified_crawler?.per_url_rolling_7d ?? (targets.verified_crawler_fetches_per_eligible_url_per_day == null ? null : targets.verified_crawler_fetches_per_eligible_url_per_day * 7) ?? targets.verified_crawler_per_eligible_url_rolling_7d ?? targets.verified_crawler_per_url_7d ?? 70)} <span className="text-xs font-sans font-medium text-gray-500">fetches / rolling 7d</span></div>
              </div>
            </div>
            <p className="mt-2 text-[10px] leading-4 text-gray-500">
              Unknown identity, missing telemetry, blocked, errored, and non-HTML responses do not count as successful verified crawler fetches.
            </p>
          </section>

          <section className="rounded-lg border border-[#dce5df] bg-white p-3" data-testid="topic-traffic-source-coverage">
            <div className="mb-2 flex items-end justify-between gap-2">
              <div>
                <h4 className="text-xs font-bold text-[#29453e]">Source coverage and setup gaps</h4>
                <p className="mt-0.5 text-[10px] text-gray-500">These channels describe different events and must not be added together.</p>
              </div>
              <ShieldAlert size={15} className="text-[#b46b42]" />
            </div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              <SourceCard title="First-party analytics" status={analytics.status} note={analytics.note} testId="topic-traffic-source-analytics">
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px] text-gray-500">
                   <span>Coverage start</span><strong className="font-medium text-gray-700">{analytics.coverage_start_verified ? dateLabel(analytics.coverage_start) : 'Unverified'}</strong>
                   <span>Earliest event</span><strong className="font-medium text-gray-700">{dateLabel(analytics.earliest_observed_event_at)}</strong>
                  <span>Latest event</span><strong className="font-medium text-gray-700">{timeLabel(analytics.latest_event_at)}</strong>
                  <span>Rolling 7d complete</span><strong className="font-medium text-gray-700">{analytics.rolling_7d_complete == null ? 'N/A' : analytics.rolling_7d_complete ? 'Yes' : 'No'}</strong>
                  <span>Freshness</span><strong className="font-medium text-gray-700">{analytics.freshness || 'N/A'}</strong>
                </div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {['Consented sessions', 'Browser page-view events', 'Organic-search landings', 'AI-referral landings'].map((name) => (
                    <span key={name} className="rounded border border-[#e2e9e4] bg-white px-1.5 py-0.5 text-[9px] text-gray-500">{name}</span>
                  ))}
                </div>
              </SourceCard>
              <SourceCard title="Search Console" status={searchConsole.status} note={searchConsole.note} testId="topic-traffic-source-search-console">
                <div className="grid grid-cols-2 gap-x-3 text-[10px] text-gray-500">
                  <span>Clicks</span><strong className="font-medium text-gray-700">{showValue(searchConsole.clicks)}</strong>
                  <span>Impressions</span><strong className="font-medium text-gray-700">{showValue(searchConsole.impressions)}</strong>
                  <span>Latest data</span><strong className="font-medium text-gray-700">{dateLabel(searchConsole.latest_data_at)}</strong>
                  <span>Freshness</span><strong className="font-medium text-gray-700">{humanize(searchConsole.freshness || 'unknown')}</strong>
                </div>
                <p className="mt-2 text-[9px] text-gray-400">Search Console measurements are separate from first-party landing events.</p>
              </SourceCard>
              <SourceCard title="Cloudflare request logs" status={cloudflare.status} note={cloudflare.setup_note || cloudflare.note} testId="topic-traffic-source-cloudflare">
                {cloudflare.response_counts && typeof cloudflare.response_counts === 'object' ? (
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px] text-gray-500">
                    {Object.entries(cloudflare.response_counts).map(([key, value]) => <div key={key} className="contents"><span>{humanize(key)}</span><strong className="font-medium text-gray-700">{showValue(value)}</strong></div>)}
                  </div>
                ) : (
                  <div className="grid grid-cols-2 gap-x-3 text-[10px] text-gray-500">
                    <span>HTTP page requests</span><strong className="font-medium text-gray-700">{showValue(cloudflare.page_requests ?? cloudflare.response_count ?? cloudflare.total_requests)}</strong>
                    <span>Successful HTML</span><strong className="font-medium text-gray-700">{showValue(cloudflare.successful_html)}</strong>
                    <span>HTTP status counts</span><strong className="font-medium text-gray-700">{showValue(cloudflare.response_status_counts)}</strong>
                    <span>Latest data</span><strong className="font-medium text-gray-700">{dateLabel(cloudflare.latest_event_at)}</strong>
                    <span>Freshness</span><strong className="font-medium text-gray-700">{humanize(cloudflare.freshness || 'unknown')}</strong>
                  </div>
                )}
              </SourceCard>
              <SourceCard title="Tollbit attribution" status={tollbit.status || 'unverified'} note={tollbit.no_claim_note || tollbit.note} testId="topic-traffic-source-tollbit">
                <div className="grid grid-cols-2 gap-x-3 text-[10px] text-gray-500">
                   <span>Attributed requests</span><strong className="font-medium text-gray-700">{showValue(tollbit.attributed_requests ?? tollbit.attributed_visits)}</strong>
                  <span>Revenue</span><strong className="font-medium text-gray-700">{showValue(tollbit.revenue)}</strong>
                </div>
                <p className="mt-2 text-[9px] font-semibold text-amber-800">No paid traffic or revenue claim is made.</p>
              </SourceCard>
            </div>
          </section>

          {exclusions.length > 0 && (
            <section className="rounded-lg border border-amber-200 bg-[#fffaf1] p-3" data-testid="topic-traffic-exclusions">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h4 className="text-xs font-bold text-amber-950">Eligibility exclusions</h4>
                  <p className="mt-0.5 text-[10px] text-amber-900/70">Candidates excluded from the eligible URL denominator, grouped by reason.</p>
                </div>
                <span className="rounded bg-white/80 px-2 py-1 font-mono text-[10px] font-semibold text-amber-900">{fmt(inventory.excluded_url_count)} total</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {exclusions.map(([reason, count]) => (
                  <span key={reason} className="inline-flex items-center gap-1.5 rounded border border-amber-200/80 bg-white px-2 py-1 text-[10px] text-amber-950" data-testid={`topic-traffic-exclusion-${reason}`}>
                    <span>{humanize(reason)}</span><strong className="font-mono">{fmt(count)}</strong>
                  </span>
                ))}
              </div>
            </section>
          )}

          {breakdown.length > 0 && (
            <section className="overflow-hidden rounded-lg border border-[#dce5df] bg-white" data-testid="topic-traffic-subject-breakdown">
              <div className="border-b border-[#e8eeea] px-3 py-2">
                <h4 className="text-xs font-bold text-[#29453e]">Subject and language coverage</h4>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[680px] text-left text-[10px]">
                  <thead className="bg-[#f6f8f5] text-[9px] uppercase tracking-wide text-gray-400">
                    <tr>
                      <th className="px-3 py-2">Subject / language</th><th className="px-2 py-2 text-right">Candidates</th><th className="px-2 py-2 text-right">Eligible</th><th className="px-2 py-2 text-right">Excluded</th><th className="px-3 py-2 text-right">Human discovery visits · 7d</th><th className="px-3 py-2 text-right">Verified crawler fetches · 7d</th>
                    </tr>
                  </thead>
                  <tbody>
                    {breakdown.map((row, index) => (
                      <tr key={`${row.subject_id || row.subject_name}-${row.language}-${index}`} className="border-t border-[#edf1ee]" data-testid={`topic-traffic-subject-row-${row.subject_id || index}-${row.language || 'unknown'}`}>
                        <td className="px-3 py-2"><div className="font-semibold text-gray-700">{row.subject_name || `Subject ${row.subject_id || 'N/A'}`}</div><div className="mt-0.5 text-gray-400">{row.language || 'Language N/A'}</div></td>
                        <td className="px-2 py-2 text-right"><Count value={row.candidate_urls ?? row.candidate_url_count} /></td>
                        <td className="px-2 py-2 text-right"><Count value={row.eligible_urls ?? row.eligible_url_count} /></td>
                        <td className="px-2 py-2 text-right"><Count value={row.excluded_urls ?? row.excluded_url_count} /></td>
                        <td className="px-3 py-2 text-right"><Count value={row.human_discovery_visits_7d} /></td>
                        <td className="px-3 py-2 text-right"><Count value={row.verified_crawler_fetches_7d} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <section className="overflow-hidden rounded-lg border border-[#dce5df] bg-white" data-testid="topic-traffic-topic-list">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#e8eeea] px-3 py-2.5">
              <div>
                <h4 className="text-xs font-bold text-[#29453e]">Topic URL evidence</h4>
                <p className="mt-0.5 text-[10px] text-gray-400">Distinct measurements below remain independent; expand a row for daily events and diagnostics.</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <label className="relative">
                  <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Find topic, chapter, URL…"
                    className="h-7 w-52 rounded border border-[#dce5df] bg-[#fbfcfa] pl-7 pr-2 text-[10px] outline-none focus:border-[#759788]"
                    data-testid="topic-traffic-search"
                  />
                </label>
                <select
                  value={eligibility}
                  onChange={(event) => setEligibility(event.target.value)}
                  className="h-7 rounded border border-[#dce5df] bg-white px-2 text-[10px] text-gray-600 outline-none focus:border-[#759788]"
                  data-testid="topic-traffic-eligibility-filter"
                  aria-label="Filter by eligibility"
                >
                  <option value="all">All candidates</option>
                  <option value="eligible">Eligible</option>
                  <option value="excluded">Excluded</option>
                </select>
              </div>
            </div>

            {topics.length === 0 ? (
              <div className="px-4 py-10 text-center" data-testid="topic-traffic-empty">
                <div className="mx-auto flex h-9 w-9 items-center justify-center rounded-full bg-[#f0f5f1] text-[#628173]"><Info size={16} /></div>
                <p className="mt-2 text-xs font-semibold text-gray-700">No topic rows in this report</p>
                <p className="mt-1 text-[10px] text-gray-400">The inventory may be empty, or no rows were returned for this page.</p>
              </div>
            ) : filteredTopics.length === 0 ? (
              <div className="px-4 py-8 text-center text-xs text-gray-500" data-testid="topic-traffic-filter-empty">No topic URLs match these filters.</div>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[1040px] text-left text-[10px]">
                    <thead className="bg-[#f6f8f5] text-[9px] uppercase tracking-wide text-gray-400">
                      <tr>
                        <th className="px-3 py-2">Topic / canonical URL</th>
                        <th className="px-2 py-2">Eligibility</th>
                        <th className="px-2 py-2 text-right">Observed human proxy · 7d</th>
                        <th className="px-2 py-2 text-right">Organic landings · 7d</th>
                        <th className="px-2 py-2 text-right">AI referrals · 7d</th>
                        <th className="px-2 py-2 text-right">Browser page views · 7d</th>
                        <th className="px-2 py-2 text-right">HTTP requests · 7d</th>
                        <th className="px-2 py-2 text-right">GSC clicks / impr.</th>
                        <th className="px-2 py-2 text-right">Verified crawler · 7d</th>
                        <th className="px-2 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredTopics.map((topic, index) => {
                        const metrics = topic.metrics || {};
                        const rolling = metrics.rolling_7d || {};
                        const rowKey = `${topic.topic_group_id || topic.topic_id || topic.chapter_id}-${topic.language || 'unknown'}-${index}`;
                        const isExpanded = expanded === rowKey;
                        const clicks = metrics.search_console?.clicks;
                        const impressions = metrics.search_console?.impressions;
                        return (
                          <Fragment key={rowKey}>
                            <tr className="border-t border-[#edf1ee] align-top hover:bg-[#fbfcfa]" data-testid={`topic-traffic-topic-row-${rowKey}`}>
                              <td className="max-w-[300px] px-3 py-2">
                                <div className="font-semibold text-gray-700">{topic.topic_title || topic.topic_name || 'Untitled topic'}</div>
                                <div className="mt-0.5 text-gray-400">{topic.subject_name || 'Subject N/A'} · {topic.chapter_title || 'Chapter N/A'} · {topic.language || 'Language N/A'}</div>
                                {topic.canonical_url ? (
                                  <a href={topic.canonical_url} target="_blank" rel="noreferrer" className="mt-1 inline-flex max-w-full items-center gap-1 truncate text-[#426b5c] hover:underline" data-testid={`topic-traffic-canonical-url-${rowKey}`}>
                                    <span className="truncate">{topic.canonical_url}</span><ExternalLink size={10} className="shrink-0" />
                                  </a>
                                ) : <span className="mt-1 block text-gray-400">Canonical URL unavailable</span>}
                                {!topic.eligible && topic.exclusion_reason && <div className="mt-1 text-[9px] font-medium text-amber-800">Excluded: {humanize(topic.exclusion_reason)}</div>}
                              </td>
                              <td className="px-2 py-2">{topic.eligible ? <span className="rounded bg-emerald-50 px-1.5 py-0.5 font-semibold text-emerald-700">Eligible</span> : <span className="rounded bg-amber-50 px-1.5 py-0.5 font-semibold text-amber-800">Excluded</span>}</td>
                              <td className="px-2 py-2 text-right"><Count value={rolling.human_discovery_visits} /></td>
                              <td className="px-2 py-2 text-right"><Count value={rolling.organic_search_landings} /></td>
                              <td className="px-2 py-2 text-right"><Count value={rolling.ai_referral_landings} /></td>
                              <td className="px-2 py-2 text-right"><Count value={rolling.page_view_events} /></td>
                              <td className="px-2 py-2 text-right"><Count value={rolling.server_page_requests} /></td>
                              <td className="px-2 py-2 text-right"><span className="font-mono">{showValue(clicks)} / {showValue(impressions)}</span></td>
                              <td className="px-2 py-2 text-right"><Count value={rolling.verified_crawler_fetches} /></td>
                              <td className="px-2 py-2 text-right">
                                <button type="button" onClick={() => setExpanded(isExpanded ? null : rowKey)} aria-expanded={isExpanded} aria-label={`${isExpanded ? 'Hide' : 'Show'} details for ${topic.topic_title || 'topic'}`} className="rounded p-1 text-gray-400 hover:bg-[#eef4ef] hover:text-[#35584e]" data-testid={`topic-traffic-expand-${rowKey}`}>
                                  <ChevronDown size={13} className={isExpanded ? 'rotate-180 transition-transform' : 'transition-transform'} />
                                </button>
                              </td>
                            </tr>
                            {isExpanded && (
                              <tr className="bg-[#f9fbf9]" data-testid={`topic-traffic-topic-details-${rowKey}`}>
                                <td colSpan="10" className="px-3 py-3">
                                  <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_280px]">
                                    <div>
                                      <div className="mb-2 flex flex-wrap items-center gap-2">
                                        <span className="text-[10px] font-bold uppercase tracking-wide text-gray-500">Rolling 7-day target status</span>
                                        <StatusPill status={rolling.human_target_status} />
                                        <span className="text-[9px] text-gray-400">Human target {fmt(rolling.human_target)}</span>
                                        <StatusPill status={rolling.crawler_target_status} />
                                        <span className="text-[9px] text-gray-400">Crawler target {fmt(rolling.crawler_target)}</span>
                                      </div>
                                      <div className="overflow-x-auto rounded border border-[#e4ebe6] bg-white">
                                        <table className="w-full min-w-[800px] text-left text-[9px]">
                                          <thead className="bg-[#f4f7f4] text-[8px] uppercase tracking-wide text-gray-400">
                                            <tr><th className="px-2 py-1.5">UTC date</th><th className="px-2 py-1.5 text-right">Page-view events</th><th className="px-2 py-1.5 text-right">Site sessions</th><th className="px-2 py-1.5 text-right">Organic landings</th><th className="px-2 py-1.5 text-right">AI landings</th><th className="px-2 py-1.5 text-right">Human proxy</th><th className="px-2 py-1.5 text-right">Human target/status</th><th className="px-2 py-1.5 text-right">HTTP requests</th><th className="px-2 py-1.5 text-right">Verified fetches</th><th className="px-2 py-1.5">Crawler status</th></tr>
                                          </thead>
                                          <tbody>
                                            {(metrics.daily || []).map((day, dayIndex) => (
                                              <tr key={`${day.date}-${dayIndex}`} className="border-t border-[#edf1ee]" data-testid={`topic-traffic-daily-${rowKey}-${day.date}`}>
                                                <td className="px-2 py-1.5 font-mono text-gray-600">{day.date}{day.partial_day ? <span className="ml-1 rounded bg-amber-50 px-1 text-amber-800">partial</span> : null}</td>
                                                <td className="px-2 py-1.5 text-right"><Count value={day.page_view_events} /></td>
                                                <td className="px-2 py-1.5 text-right"><Count value={day.site_sessions} /></td>
                                                <td className="px-2 py-1.5 text-right"><Count value={day.organic_search_landings} /></td>
                                                <td className="px-2 py-1.5 text-right"><Count value={day.ai_referral_landings} /></td>
                                                <td className="px-2 py-1.5 text-right"><Count value={day.human_discovery_visits} /></td>
                                                <td className="px-2 py-1.5 text-right"><div><Count value={day.human_target_per_day} /></div><StatusPill status={day.human_target_status} /></td>
                                                <td className="px-2 py-1.5 text-right"><Count value={day.server_page_requests} /></td>
                                                <td className="px-2 py-1.5 text-right"><Count value={day.verified_crawler_fetches} /></td>
                                                <td className="px-2 py-1.5"><div className="text-gray-500"><Count value={day.crawler_target_per_day} /> target</div><StatusPill status={day.crawler_target_status} /></td>
                                              </tr>
                                            ))}
                                            {(!metrics.daily || metrics.daily.length === 0) && <tr><td colSpan="10" className="px-2 py-4 text-center text-gray-400">Daily telemetry is not available.</td></tr>}
                                          </tbody>
                                        </table>
                                      </div>
                                    </div>
                                    <div className="space-y-2">
                                      <div className="rounded border border-[#e4ebe6] bg-white p-2.5">
                                        <div className="text-[9px] font-bold uppercase tracking-wide text-gray-500">Search Console · separate source</div>
                                        <div className="mt-1 grid grid-cols-2 text-[10px]"><span className="text-gray-500">Clicks</span><strong className="text-right">{showValue(clicks)}</strong><span className="text-gray-500">Impressions</span><strong className="text-right">{showValue(impressions)}</strong></div>
                                      </div>
                                      <div className="rounded border border-[#e4ebe6] bg-white p-2.5">
                                        <div className="text-[9px] font-bold uppercase tracking-wide text-gray-500">Crawler response outcomes</div>
                                        <div className="mt-1 grid grid-cols-2 gap-y-1 text-[10px] text-gray-500">
                                          {Object.entries(metrics.crawler_response_outcomes || {}).map(([key, value]) => <div key={key} className="contents"><span>{humanize(key)}</span><strong className="text-right font-mono font-medium text-gray-700">{showValue(value)}</strong></div>)}
                                          {Object.keys(metrics.crawler_response_outcomes || {}).length === 0 && <span className="col-span-2">N/A · response telemetry not ingested</span>}
                                        </div>
                                        <p className="mt-2 text-[9px] leading-4 text-gray-400">Only successful, identified crawler fetches qualify; unknown, blocked, failed, or non-HTML outcomes do not.</p>
                                      </div>
                                      {topic.diagnostics?.length > 0 && (
                                        <div className="rounded border border-amber-200 bg-amber-50 p-2.5" data-testid={`topic-traffic-diagnostics-${rowKey}`}>
                                          <div className="mb-1 text-[9px] font-bold uppercase tracking-wide text-amber-900">Diagnostics</div>
                                          {topic.diagnostics.map((diagnostic, diagnosticIndex) => <p key={`${diagnostic.code}-${diagnosticIndex}`} className="mt-1 text-[9px] leading-4 text-amber-900"><strong>{humanize(diagnostic.code)}:</strong> {diagnostic.message}</p>)}
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#e8eeea] px-3 py-2 text-[10px] text-gray-500" data-testid="topic-traffic-pagination">
                  <span>Showing {filteredTopics.length} of {topics.length} rows on page {page}</span>
                  <div className="flex items-center gap-2">
                    <button type="button" onClick={() => goToPage(page - 1)} disabled={page <= 1 || loading || refreshing} className="inline-flex items-center gap-1 rounded border border-[#dce5df] px-2 py-1 disabled:opacity-40" data-testid="topic-traffic-page-prev"><ChevronLeft size={12} />Previous</button>
                    <span className="font-mono" data-testid="topic-traffic-page-label">{page} / {totalPages}</span>
                    <button type="button" onClick={() => goToPage(page + 1)} disabled={page >= totalPages || loading || refreshing} className="inline-flex items-center gap-1 rounded border border-[#dce5df] px-2 py-1 disabled:opacity-40" data-testid="topic-traffic-page-next">Next<ChevronRight size={12} /></button>
                  </div>
                </div>
              </>
            )}
          </section>

          {definitions && (
            <details className="rounded-lg border border-[#dce5df] bg-[#fbfcfa] px-3 py-2" data-testid="topic-traffic-definitions">
              <summary className="cursor-pointer text-[10px] font-semibold text-[#48665b]">Metric definitions</summary>
              <div className="mt-2 space-y-1 border-t border-[#e6ece8] pt-2 text-[10px] leading-4 text-gray-500">
                {Array.isArray(definitions)
                  ? definitions.map((definition, index) => <p key={index}>{typeof definition === 'string' ? definition : `${definition.name || definition.metric || 'Definition'}: ${definition.description || definition.definition || ''}`}</p>)
                  : Object.entries(definitions).map(([key, value]) => <p key={key}><strong className="font-semibold text-gray-700">{humanize(key)}:</strong> {typeof value === 'string' ? value : JSON.stringify(value)}</p>)}
              </div>
            </details>
          )}
        </>
      ) : !error ? (
        <div className="rounded-lg border border-[#dce5df] bg-white px-4 py-10 text-center text-xs text-gray-500" data-testid="topic-traffic-empty">No report data is available.</div>
      ) : null}
    </div>
  );
}
