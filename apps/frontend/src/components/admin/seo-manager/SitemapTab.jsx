import { useEffect, useState, useCallback, useRef } from 'react';
import { Loader2, RefreshCw, Map, Sparkles, CheckCircle2, AlertTriangle, Send, Rocket, Bell, ClipboardList, Activity, XCircle } from 'lucide-react';
import {
  adminSeoGoogleIndexingStats,
  adminIndexNowBackfillStart,
  adminIndexNowBackfillProgress,
  adminIndexNowSubmitUrls,
  adminIndexNowHistory,
  adminSeoGoogleSitemapPing,
  adminSeoIndexNowSmoke,
  adminSeoIndexNowSmokeHistory,
} from '@/utils/api';

const INDEXING_FIELDS = [
  { key: 'sent',               label: 'Submitted' },
  { key: 'status_2xx',         label: 'Accepted (2xx)' },
  { key: 'status_4xx',         label: 'Client errors (4xx)' },
  { key: 'status_5xx',         label: 'Server errors (5xx)' },
  { key: 'quota_blocks',       label: 'Quota blocks' },
  { key: 'sitemap_ping_sent',  label: 'Sitemap pings' },
];

function IndexingStatsCard({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    if (!adminToken) return;
    setLoading(true);
    setError(null);
    try {
      const r = await adminSeoGoogleIndexingStats(adminToken);
      setData(r.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to load Google indexing stats');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  const today = data || null;
  const yesterday = data?.yesterday ?? null;
  const dailyLimit = today?.daily_limit ?? 200;
  const remaining = today?.quota_remaining;

  return (
    <div className="rounded-xl border p-5 space-y-4" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <p className="text-sm font-semibold text-gray-900 flex items-center gap-1.5">
            <Send size={14} className="text-violet-500" />
            Google Indexing API — Daily Usage
          </p>
          <p className="text-xs mt-0.5" style={{ color: '#9ca3af' }}>
            Submissions to Google's Indexing API + sitemap pings, persisted across restarts. Cap is {dailyLimit} URLs/day.
          </p>
        </div>
        <button onClick={load} disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border disabled:opacity-40"
          style={{ borderColor: '#e5e7eb', color: '#4b5563', background: '#fff' }}>
          {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          Refresh
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-2 p-2.5 rounded-lg" style={{ background: 'rgba(239,68,68,0.06)' }}>
          <AlertTriangle size={12} className="text-red-400 flex-shrink-0 mt-0.5" />
          <span className="text-xs" style={{ color: '#dc2626' }}>{error}</span>
        </div>
      )}

      {today?.enabled === false && !error && (
        <div className="p-3 rounded-lg text-xs" style={{ background: 'rgba(245,158,11,0.08)', color: '#92400e' }}>
          Google Indexing API integration is disabled
          {today?.error ? ` — ${today.error}` : ' (set GOOGLE_INDEXING_ENABLED=true and provide GOOGLE_INDEXING_SERVICE_ACCOUNT)'}.
        </div>
      )}

      {today && today.enabled !== false && (
        <>
          {typeof remaining === 'number' && (
            <div className="flex items-center justify-between text-xs" style={{ color: '#6b7280' }}>
              <span>
                <span className="font-semibold text-gray-900">{remaining}</span> of {dailyLimit} submissions remaining today
              </span>
              <span style={{ color: '#9ca3af' }}>UTC day {today.day}</span>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <DayColumn title="Today" tone="violet" day={today} />
            <DayColumn title="Yesterday" tone="gray" day={yesterday} />
          </div>
        </>
      )}
    </div>
  );
}

function DayColumn({ title, tone, day }) {
  const tones = {
    violet: { bg: 'rgba(124,58,237,0.06)', border: 'rgba(124,58,237,0.20)', label: '#7c3aed' },
    gray:   { bg: '#ffffff',                border: '#e5e7eb',               label: '#6b7280' },
  };
  const t = tones[tone] || tones.gray;
  const empty = !day;

  return (
    <div className="rounded-lg border p-3.5" style={{ background: t.bg, borderColor: t.border }}>
      <div className="flex items-center justify-between mb-2.5">
        <p className="text-[11px] font-bold uppercase tracking-wider" style={{ color: t.label }}>
          {title}
        </p>
        {!empty && day.day && (
          <p className="text-[10px] font-mono" style={{ color: '#9ca3af' }}>{day.day}</p>
        )}
      </div>
      {empty ? (
        <p className="text-xs italic py-3 text-center" style={{ color: '#9ca3af' }}>
          No prior-day data
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-x-3 gap-y-2">
          {INDEXING_FIELDS.map(f => (
            <div key={f.key} className="flex flex-col">
              <span className="text-base font-bold text-gray-900 leading-none">
                {Number(day[f.key] ?? 0).toLocaleString()}
              </span>
              <span className="text-[10px] mt-0.5" style={{ color: '#9ca3af' }}>{f.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function IndexNowBackfillCard({ adminToken }) {
  const [progress, setProgress] = useState(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  const fetchProgress = useCallback(async () => {
    if (!adminToken) return null;
    try {
      const r = await adminIndexNowBackfillProgress(adminToken);
      const p = r.data?.progress || null;
      setProgress(p);
      return p;
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to load backfill progress');
      return null;
    }
  }, [adminToken]);

  useEffect(() => { fetchProgress(); }, [fetchProgress]);

  useEffect(() => {
    if (progress?.status === 'running') {
      if (pollRef.current) return;
      pollRef.current = setInterval(async () => {
        const p = await fetchProgress();
        if (p && p.status !== 'running') {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }, 2000);
    } else if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [progress?.status, fetchProgress]);

  const handleStart = async () => {
    setStarting(true);
    setError(null);
    try {
      const r = await adminIndexNowBackfillStart(adminToken);
      setProgress(r.data?.progress || null);
    } catch (e) {
      if (e?.response?.status === 409) {
        await fetchProgress();
      } else {
        setError(e?.response?.data?.detail || e?.message || 'Failed to start backfill');
      }
    } finally {
      setStarting(false);
    }
  };

  const status = progress?.status || 'idle';
  const running = status === 'running';
  const done = status === 'done';
  const errored = status === 'error';
  const pct = progress?.chunks_total
    ? Math.round((progress.chunks_done / progress.chunks_total) * 100)
    : 0;
  const epStatus = progress?.endpoint_status || {};
  const skipReasons = progress?.skip_reasons || {};

  return (
    <div className="rounded-xl border p-5 space-y-4" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <p className="text-sm font-semibold text-gray-900 flex items-center gap-1.5">
            <Rocket size={14} className="text-violet-500" />
            Full IndexNow Backfill
          </p>
          <p className="text-xs mt-0.5" style={{ color: '#9ca3af' }}>
            Push every public URL on syrabit.ai to Bing/Yandex/IndexNow in chunks of 10,000. Use this once to backfill older pages that were never submitted.
          </p>
        </div>
        <button onClick={handleStart} disabled={starting || running || !adminToken}
          className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold disabled:opacity-40"
          style={{ background: '#7c3aed', color: '#fff' }}>
          {(starting || running) ? <Loader2 size={14} className="animate-spin" /> : <Rocket size={14} />}
          {running ? 'Backfill running…' : (starting ? 'Starting…' : 'Run Full Backfill to Bing')}
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-2 p-2.5 rounded-lg" style={{ background: 'rgba(239,68,68,0.06)' }}>
          <AlertTriangle size={12} className="text-red-400 flex-shrink-0 mt-0.5" />
          <span className="text-xs" style={{ color: '#dc2626' }}>{error}</span>
        </div>
      )}

      {progress && status !== 'idle' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between text-xs" style={{ color: '#6b7280' }}>
            <span>
              Chunk <span className="font-semibold text-gray-900">{progress.chunks_done}</span>
              {' / '}
              <span className="font-semibold text-gray-900">{progress.chunks_total || '—'}</span>
              {progress.chunks_total > 0 && <span className="ml-2" style={{ color: '#9ca3af' }}>({pct}%)</span>}
            </span>
            <span style={{ color: errored ? '#dc2626' : (done ? '#16a34a' : '#7c3aed') }}>
              {errored ? 'Error' : (done ? 'Complete' : (running ? 'Running…' : 'Idle'))}
            </span>
          </div>
          {progress.chunks_total > 0 && (
            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: '#e5e7eb' }}>
              <div className="h-full transition-all" style={{
                width: `${pct}%`,
                background: errored ? '#dc2626' : (done ? '#16a34a' : '#7c3aed'),
              }} />
            </div>
          )}

          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {[
              { label: 'Discovered', val: progress.discovered, color: '#374151' },
              { label: 'Queued', val: progress.queued ?? Math.max(Number(progress.discovered ?? 0) - Number(progress.submitted ?? 0), 0), color: '#f59e0b' },
              { label: 'Submitted', val: progress.submitted, color: '#7c3aed' },
              { label: 'Succeeded', val: progress.succeeded, color: '#16a34a' },
              { label: 'Failed', val: progress.failed, color: '#dc2626' },
            ].map(s => (
              <div key={s.label} className="rounded-lg p-3 text-center border"
                style={{ background: '#ffffff', borderColor: '#e5e7eb' }}>
                <p className="text-xl font-bold" style={{ color: s.color }}>
                  {Number(s.val ?? 0).toLocaleString()}
                </p>
                <p className="text-[11px] mt-0.5" style={{ color: '#9ca3af' }}>{s.label}</p>
              </div>
            ))}
          </div>

          {Object.keys(epStatus).length > 0 && (
            <div>
              <p className="text-[10px] font-bold uppercase tracking-wider mb-1.5" style={{ color: '#9ca3af' }}>
                Per endpoint (chunks)
              </p>
              <div className="space-y-1">
                {Object.entries(epStatus).map(([ep, s]) => (
                  <div key={ep} className="flex items-center justify-between text-xs px-2 py-1 rounded" style={{ background: '#fff', border: '1px solid #e5e7eb' }}>
                    <span className="font-mono truncate" style={{ color: '#6b7280' }}>{ep}</span>
                    <span className="flex items-center gap-3 flex-shrink-0">
                      <span style={{ color: '#16a34a' }}>✓ {s.success_chunks}</span>
                      <span style={{ color: s.failed_chunks > 0 ? '#dc2626' : '#9ca3af' }}>✗ {s.failed_chunks}</span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {progress.skipped > 0 && (
            <div className="text-xs px-2.5 py-2 rounded" style={{ background: 'rgba(245,158,11,0.06)', color: '#92400e' }}>
              Skipped <span className="font-semibold">{progress.skipped}</span> URL(s):{' '}
              {Object.entries(skipReasons).map(([k, v]) => `${k}=${v}`).join(', ')}
            </div>
          )}

          {progress.error && (
            <div className="text-xs px-2.5 py-2 rounded" style={{ background: 'rgba(239,68,68,0.06)', color: '#dc2626' }}>
              {progress.error}
            </div>
          )}

          <p className="text-[10px]" style={{ color: '#9ca3af' }}>
            Started {progress.started_at || '—'}{progress.finished_at ? ` · Finished ${progress.finished_at}` : ''}
            {progress.run_id ? ` · run ${progress.run_id}` : ''}
          </p>
        </div>
      )}
    </div>
  );
}

// Task #564: 14-day pass/fail strip for the publish→IndexNow smoke test.
// Buckets every persisted run by UTC day, then renders one cell per day —
// green if every run that day passed, red if any failed, gray if none ran.
// A regression that's intermittent across a week is invisible from a single
// green/red snapshot but obvious here.
function SmokeHistoryStrip({ runs }) {
  const DAYS = 14;
  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);

  const windowStart = new Date(today);
  windowStart.setUTCDate(today.getUTCDate() - (DAYS - 1));

  const byDay = new Map();
  let totalRuns = 0;
  let totalFails = 0;
  for (const r of runs || []) {
    if (!r?.ran_at) continue;
    const d = new Date(r.ran_at);
    if (isNaN(d.getTime())) continue;
    const dayStart = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    if (dayStart < windowStart || dayStart > today) continue;
    const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    const slot = byDay.get(key) || { pass: 0, fail: 0 };
    if (r.ok) slot.pass += 1; else { slot.fail += 1; totalFails += 1; }
    totalRuns += 1;
    byDay.set(key, slot);
  }

  const cells = [];
  for (let i = DAYS - 1; i >= 0; i -= 1) {
    const d = new Date(today);
    d.setUTCDate(today.getUTCDate() - i);
    const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    const slot = byDay.get(key);
    let color = '#e5e7eb';
    let state = 'no runs';
    if (slot) {
      if (slot.fail > 0 && slot.pass === 0) { color = '#dc2626'; state = `${slot.fail} failed`; }
      else if (slot.fail > 0) { color = '#f59e0b'; state = `${slot.pass} passed, ${slot.fail} failed`; }
      else { color = '#16a34a'; state = `${slot.pass} passed`; }
    }
    cells.push({ key, color, state, label: key.slice(5) });
  }

  return (
    <div className="rounded-lg border p-3 space-y-2"
      style={{ background: '#fff', borderColor: '#e5e7eb' }}>
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-bold uppercase tracking-wider" style={{ color: '#9ca3af' }}>
          Smoke trend · last {DAYS} days
        </p>
        <p className="text-[11px]" style={{ color: '#9ca3af' }}>
          {totalRuns === 0
            ? 'No runs logged yet'
            : `${totalRuns} run${totalRuns === 1 ? '' : 's'} · ${totalFails} failed`}
        </p>
      </div>
      <div className="flex items-center gap-1">
        {cells.map(c => (
          <div key={c.key}
            title={`${c.key} — ${c.state}`}
            className="flex-1 h-5 rounded"
            style={{ background: c.color, minWidth: 8 }} />
        ))}
      </div>
      <div className="flex items-center justify-between text-[10px]" style={{ color: '#9ca3af' }}>
        <span>{cells[0]?.label}</span>
        <span className="flex items-center gap-3">
          <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded" style={{ background: '#16a34a' }} /> pass</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded" style={{ background: '#f59e0b' }} /> mixed</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded" style={{ background: '#dc2626' }} /> fail</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded" style={{ background: '#e5e7eb' }} /> no run</span>
        </span>
        <span>{cells[cells.length - 1]?.label}</span>
      </div>
    </div>
  );
}

// Task #560: Submit & Monitor — manual sitemap ping + URL batch submission
// + recent submission log. Lets admins push a one-off URL list to IndexNow
// and verify it actually went out, without waiting for the nightly diff.
function SubmitMonitorCard({ adminToken }) {
  const [urlsText, setUrlsText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitResult, setSubmitResult] = useState(null);
  const [pinging, setPinging] = useState(false);
  const [pingResult, setPingResult] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState(null);
  // Task #563: publish → sub-sitemap → IndexNow → push-log smoke
  const [smokeRunning, setSmokeRunning] = useState(false);
  const [smokeResult, setSmokeResult] = useState(null);
  // Task #564: persisted smoke runs (manual + cron) for the trend strip
  const [smokeHistory, setSmokeHistory] = useState([]);

  const loadSmokeHistory = useCallback(async () => {
    if (!adminToken) return;
    try {
      const r = await adminSeoIndexNowSmokeHistory(adminToken, 100);
      setSmokeHistory(r.data?.runs || []);
    } catch {
      // Trend strip is supplementary — never block the panel on a load failure.
    }
  }, [adminToken]);

  useEffect(() => { loadSmokeHistory(); }, [loadSmokeHistory]);

  const loadHistory = useCallback(async () => {
    if (!adminToken) return;
    setHistoryLoading(true);
    try {
      const r = await adminIndexNowHistory(adminToken, 20);
      setHistory(r.data?.pushes || []);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to load submission history');
    } finally {
      setHistoryLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  const handleSubmit = async () => {
    setError(null);
    setSubmitResult(null);
    const lines = urlsText
      .split(/\r?\n/)
      .map(l => l.trim())
      .filter(Boolean);
    if (lines.length === 0) {
      setError('Enter at least one URL (one per line).');
      return;
    }
    setSubmitting(true);
    try {
      const r = await adminIndexNowSubmitUrls(adminToken, lines);
      setSubmitResult(r.data);
      await loadHistory();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Submission failed');
    } finally {
      setSubmitting(false);
    }
  };

  const handleSmoke = async () => {
    setError(null);
    setSmokeResult(null);
    setSmokeRunning(true);
    try {
      const r = await adminSeoIndexNowSmoke(adminToken);
      setSmokeResult(r.data);
      await loadHistory();
      await loadSmokeHistory();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Smoke test failed');
    } finally {
      setSmokeRunning(false);
    }
  };

  const handlePing = async () => {
    setError(null);
    setPingResult(null);
    setPinging(true);
    try {
      const r = await adminSeoGoogleSitemapPing(adminToken);
      setPingResult(r.data);
      await loadHistory();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Ping failed');
    } finally {
      setPinging(false);
    }
  };

  return (
    <div className="rounded-xl border p-5 space-y-4" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <p className="text-sm font-semibold text-gray-900 flex items-center gap-1.5">
            <ClipboardList size={14} className="text-violet-500" />
            Submit &amp; Monitor
          </p>
          <p className="text-xs mt-0.5" style={{ color: '#9ca3af' }}>
            Manually submit one or more URLs to IndexNow (Bing/Yandex), ping Google's sitemap endpoint, and watch recent submission attempts.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={handleSmoke} disabled={smokeRunning || !adminToken}
            title="Verify the publish → sub-sitemap → IndexNow → push-log chain end-to-end"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-40"
            style={{ background: '#fff', border: '1px solid #e5e7eb', color: '#4b5563' }}>
            {smokeRunning ? <Loader2 size={12} className="animate-spin" /> : <Activity size={12} />}
            Run smoke test
          </button>
          <button onClick={handlePing} disabled={pinging || !adminToken}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-40"
            style={{ background: '#fff', border: '1px solid #e5e7eb', color: '#4b5563' }}>
            {pinging ? <Loader2 size={12} className="animate-spin" /> : <Bell size={12} />}
            Ping Google sitemap
          </button>
          <button onClick={loadHistory} disabled={historyLoading || !adminToken}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-40"
            style={{ background: '#fff', border: '1px solid #e5e7eb', color: '#4b5563' }}>
            {historyLoading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Refresh log
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 p-2.5 rounded-lg" style={{ background: 'rgba(239,68,68,0.06)' }}>
          <AlertTriangle size={12} className="text-red-400 flex-shrink-0 mt-0.5" />
          <span className="text-xs" style={{ color: '#dc2626' }}>{error}</span>
        </div>
      )}

      {pingResult && (
        <div className="text-xs px-2.5 py-2 rounded" style={{ background: 'rgba(22,163,74,0.06)', color: '#166534' }}>
          Google sitemap ping: <span className="font-mono">{pingResult.status || 'sent'}</span>
          {pingResult.http_status ? ` · HTTP ${pingResult.http_status}` : ''}
        </div>
      )}

      {smokeResult && (
        <div className="rounded-lg p-3 text-xs space-y-2"
          style={{
            background: smokeResult.ok ? 'rgba(22,163,74,0.06)' : 'rgba(239,68,68,0.06)',
            border: `1px solid ${smokeResult.ok ? 'rgba(22,163,74,0.25)' : 'rgba(239,68,68,0.25)'}`,
          }}>
          <div className="flex items-center justify-between">
            <p className="font-semibold flex items-center gap-1.5"
              style={{ color: smokeResult.ok ? '#166534' : '#b91c1c' }}>
              {smokeResult.ok
                ? <><CheckCircle2 size={12} /> Publish → Google chain healthy</>
                : <><XCircle size={12} /> Publish → Google chain broken</>}
            </p>
            <span className="font-mono" style={{ color: '#6b7280' }}>{smokeResult.today}</span>
          </div>
          {smokeResult.url && (
            <p className="font-mono truncate" style={{ color: '#374151' }} title={smokeResult.url}>
              {smokeResult.url}
            </p>
          )}
          <div className="grid grid-cols-3 gap-2">
            {[
              { key: 'in_sitemap', label: 'In sub-sitemap' },
              { key: 'lastmod_fresh', label: "Today's lastmod" },
              { key: 'push_log_written', label: 'IndexNow logged' },
            ].map(step => {
              const passed = !!smokeResult[step.key];
              return (
                <div key={step.key}
                  className="flex items-center gap-1.5 px-2 py-1.5 rounded"
                  style={{ background: '#fff', border: '1px solid #e5e7eb' }}>
                  {passed
                    ? <CheckCircle2 size={12} className="text-green-600 flex-shrink-0" />
                    : <XCircle size={12} className="text-red-500 flex-shrink-0" />}
                  <span style={{ color: passed ? '#166534' : '#b91c1c' }}>{step.label}</span>
                </div>
              );
            })}
          </div>
          {smokeResult.error && (
            <p className="font-mono" style={{ color: '#b91c1c' }}>
              error: {smokeResult.error}
            </p>
          )}
        </div>
      )}

      <SmokeHistoryStrip runs={smokeHistory} />

      <div className="space-y-2">
        <label className="text-[11px] font-bold uppercase tracking-wider" style={{ color: '#9ca3af' }}>
          URLs to submit (one per line, max 1000)
        </label>
        <textarea
          value={urlsText}
          onChange={e => setUrlsText(e.target.value)}
          rows={5}
          placeholder="https://syrabit.ai/assamboard/class-12/physics/electric-charges-and-fields"
          className="w-full px-3 py-2 rounded-lg border text-xs font-mono"
          style={{ background: '#fff', borderColor: '#e5e7eb', color: '#374151' }}
        />
        <div className="flex items-center justify-between">
          <p className="text-[11px]" style={{ color: '#9ca3af' }}>
            Only absolute https://syrabit.ai URLs are accepted; off-host or malformed entries are reported as skipped.
          </p>
          <button onClick={handleSubmit} disabled={submitting || !adminToken || !urlsText.trim()}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold disabled:opacity-40"
            style={{ background: '#7c3aed', color: '#fff' }}>
            {submitting ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            {submitting ? 'Submitting…' : 'Submit to IndexNow'}
          </button>
        </div>
      </div>

      {submitResult && (
        <div className="rounded-lg p-3 text-xs space-y-1.5" style={{ background: 'rgba(124,58,237,0.06)', border: '1px solid rgba(124,58,237,0.20)' }}>
          <p className="font-semibold text-gray-900">
            Submitted <span style={{ color: '#7c3aed' }}>{submitResult.submitted}</span> URL(s)
            {Array.isArray(submitResult.skipped) && submitResult.skipped.length > 0 && (
              <> · skipped <span style={{ color: '#dc2626' }}>{submitResult.skipped.length}</span></>
            )}
          </p>
          {submitResult.endpoint_results && (
            <div className="space-y-0.5">
              {Object.entries(submitResult.endpoint_results).map(([ep, info]) => (
                <div key={ep} className="flex items-center justify-between font-mono">
                  <span style={{ color: '#6b7280' }}>{ep}</span>
                  <span style={{ color: (info?.status >= 200 && info?.status < 300) ? '#16a34a' : '#dc2626' }}>
                    {info?.status ?? 'n/a'}
                  </span>
                </div>
              ))}
            </div>
          )}
          {Array.isArray(submitResult.skipped) && submitResult.skipped.length > 0 && (
            <details className="mt-1">
              <summary className="cursor-pointer" style={{ color: '#92400e' }}>Show skipped</summary>
              <div className="mt-1.5 max-h-32 overflow-y-auto space-y-0.5">
                {submitResult.skipped.slice(0, 50).map((s, i) => (
                  <div key={i} className="flex items-center justify-between font-mono">
                    <span className="truncate" style={{ color: '#6b7280' }}>{s.url}</span>
                    <span className="ml-2 flex-shrink-0" style={{ color: '#92400e' }}>{s.reason}</span>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      <div>
        <p className="text-[11px] font-bold uppercase tracking-wider mb-1.5" style={{ color: '#9ca3af' }}>
          Recent submissions ({history.length})
        </p>
        {history.length === 0 ? (
          <p className="text-xs italic py-3 text-center" style={{ color: '#9ca3af' }}>
            {historyLoading ? 'Loading…' : 'No submissions logged yet.'}
          </p>
        ) : (
          <div className="space-y-1 max-h-64 overflow-y-auto pr-1">
            {history.map((h, i) => (
              <div key={i} className="flex items-center justify-between text-xs px-2 py-1.5 rounded font-mono"
                style={{ background: '#fff', border: '1px solid #e5e7eb' }}>
                <span className="truncate" style={{ color: '#374151' }}>
                  {h.source || 'unknown'}
                </span>
                <span className="flex items-center gap-3 flex-shrink-0">
                  <span style={{ color: '#7c3aed' }}>{h.url_count ?? 0} URLs</span>
                  <span style={{ color: '#9ca3af' }}>
                    {h.pushed_at ? new Date(h.pushed_at).toLocaleString() : '—'}
                  </span>
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function SitemapTab({
  sitemapData, sitemapValidating, handleSitemapValidate,
  refreshingMeta, handleRefreshMeta,
  sitemap, handleRegenerateSitemap,
  adminToken,
}) {
  return (
    <div className="space-y-5">
      <IndexingStatsCard adminToken={adminToken} />
      <SubmitMonitorCard adminToken={adminToken} />
      <IndexNowBackfillCard adminToken={adminToken} />

      <div className="rounded-xl border p-5 space-y-4" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-gray-900">Refresh Meta Descriptions</p>
            <p className="text-xs mt-0.5" style={{ color: '#9ca3af' }}>Re-extract meta descriptions from content, diversify titles, and recompute quality scores (no LLM cost)</p>
          </div>
          <button onClick={handleRefreshMeta} disabled={refreshingMeta}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold disabled:opacity-40"
            style={{ background: '#7c3aed', color: '#fff' }}>
            {refreshingMeta ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {refreshingMeta ? 'Refreshing…' : 'Refresh All Meta'}
          </button>
        </div>
      </div>

      <div className="rounded-xl border p-5 space-y-4" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-gray-900">Sitemap Validator</p>
            <p className="text-xs mt-0.5" style={{ color: '#9ca3af' }}>Validates your sitemap.xml coverage and detects missing or stale URLs</p>
          </div>
          <button onClick={handleSitemapValidate} disabled={sitemapValidating}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold disabled:opacity-40"
            style={{ background: '#16a34a', color: '#fff' }}>
            {sitemapValidating ? <Loader2 size={14} className="animate-spin" /> : <Map size={14} />}
            {sitemapValidating ? 'Validating…' : 'Validate Sitemap'}
          </button>
        </div>
        {sitemapData && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { label: 'Total URLs', val: sitemapData.total_urls },
                { label: 'In Sitemap', val: sitemapData.in_sitemap },
                { label: 'Missing', val: sitemapData.missing },
                { label: 'Coverage %', val: sitemapData.coverage_pct != null ? `${sitemapData.coverage_pct}%` : '—' },
              ].map(s => (
                <div key={s.label} className="rounded-lg p-3 text-center border" style={{ background: 'rgba(22,163,74,0.08)', borderColor: 'rgba(22,163,74,0.20)' }}>
                  <p className="text-xl font-bold text-gray-900">{s.val ?? '—'}</p>
                  <p className="text-[11px] mt-0.5" style={{ color: '#6b7280' }}>{s.label}</p>
                </div>
              ))}
            </div>
            {sitemapData.issues?.length > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider mb-2" style={{ color: '#9ca3af' }}>Issues Detected</p>
                <div className="space-y-1.5 max-h-52 overflow-y-auto pr-1">
                  {sitemapData.issues.map((issue, i) => (
                    <div key={i} className="flex items-start gap-2 p-2 rounded-lg" style={{ background: 'rgba(239,68,68,0.06)' }}>
                      <AlertTriangle size={12} className="text-red-400 flex-shrink-0 mt-0.5" />
                      <span className="text-xs font-mono" style={{ color: '#6b7280' }}>{issue}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {sitemapData.ok && !sitemapData.issues?.length && (
              <div className="flex items-center gap-2 p-3 rounded-xl" style={{ background: 'rgba(22,163,74,0.08)', border: '1px solid rgba(22,163,74,0.20)' }}>
                <CheckCircle2 size={16} className="text-emerald-400" />
                <p className="text-sm font-medium text-emerald-400">Sitemap is valid — {sitemapData.coverage_pct}% coverage</p>
              </div>
            )}
          </div>
        )}
        {!sitemapData && !sitemapValidating && (
          <p className="text-sm text-center py-4" style={{ color: '#d1d5db' }}>Click "Validate Sitemap" to run a coverage check</p>
        )}
      </div>
      <div className="rounded-xl border p-4" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
        <p className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: '#9ca3af' }}>Sitemap Actions</p>
        <button onClick={handleRegenerateSitemap} disabled={sitemap}
          className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold disabled:opacity-40"
          style={{ background: '#e5e7eb', border: '1px solid #e5e7eb', color: '#4b5563' }}>
          {sitemap ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          Regenerate sitemap.xml
        </button>
      </div>
    </div>
  );
}
