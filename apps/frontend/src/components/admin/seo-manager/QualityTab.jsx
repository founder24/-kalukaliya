import { useEffect, useState, useCallback } from 'react';
import { toast } from 'sonner';
import { Loader2, Play, RefreshCw, ShieldAlert, Copy, CheckCircle2, BarChart3 } from 'lucide-react';
import {
  adminSeoQualitySummary,
  adminSeoQualityAudit,
  adminSeoDuplicateScan,
  adminSeoDuplicatePairs,
  adminSeoResolveDuplicate,
} from '@/utils/api';

const BUCKET_KEYS = [
  '0-9','10-19','20-29','30-39','40-49',
  '50-59','60-69','70-79','80-89','90-99','100',
];

function bucketColor(key) {
  const lo = key === '100' ? 100 : parseInt(key.split('-')[0], 10);
  if (lo >= 90) return '#10b981';
  if (lo >= 70) return '#84cc16';
  if (lo >= 50) return '#f59e0b';
  return '#ef4444';
}

function Histogram({ histogram }) {
  if (!histogram) return null;
  const max = Math.max(1, ...BUCKET_KEYS.map(k => histogram[k] || 0));
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="text-xs font-semibold text-gray-500 mb-3 flex items-center gap-2">
        <BarChart3 size={13} /> Quality score distribution (published pages)
      </div>
      <div className="flex items-end gap-1.5 h-32">
        {BUCKET_KEYS.map(k => {
          const count = histogram[k] || 0;
          const pct = (count / max) * 100;
          return (
            <div key={k} className="flex-1 flex flex-col items-center justify-end">
              <div className="text-[10px] font-mono text-gray-500">{count}</div>
              <div
                className="w-full rounded-t"
                style={{ height: `${pct}%`, background: bucketColor(k), minHeight: count ? 2 : 0 }}
                title={`${k}: ${count} pages`}
              />
            </div>
          );
        })}
      </div>
      <div className="flex gap-1.5 mt-1">
        {BUCKET_KEYS.map(k => (
          <div key={k} className="flex-1 text-center text-[9px] text-gray-400 font-mono">{k}</div>
        ))}
      </div>
    </div>
  );
}

function StatCard({ label, value, hint, color = '#374151' }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-3">
      <div className="text-[10px] uppercase tracking-wider text-gray-400">{label}</div>
      <div className="text-xl font-bold mt-1" style={{ color }}>{value}</div>
      {hint && <div className="text-[10px] text-gray-400 mt-0.5">{hint}</div>}
    </div>
  );
}

export default function QualityTab({ adminToken }) {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [auditing, setAuditing] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [pairs, setPairs] = useState([]);
  const [pairsLoading, setPairsLoading] = useState(false);
  const [resolvingId, setResolvingId] = useState(null);
  const [threshold, setThreshold] = useState(90);
  const [dupThreshold, setDupThreshold] = useState(0.8);
  const [dryRun, setDryRun] = useState(false);

  const loadSummary = useCallback(async () => {
    setLoading(true);
    try {
      const res = await adminSeoQualitySummary(adminToken);
      setSummary(res.data);
    } catch {
      toast.error('Failed to load quality summary');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  const loadPairs = useCallback(async () => {
    setPairsLoading(true);
    try {
      const res = await adminSeoDuplicatePairs(adminToken, 'open', 100);
      setPairs(res.data?.pairs || []);
    } catch {
      toast.error('Failed to load duplicate pairs');
    } finally {
      setPairsLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { loadSummary(); loadPairs(); }, [loadSummary, loadPairs]);

  const runAudit = async () => {
    if (!confirm(
      dryRun
        ? `Run a dry-run quality audit (rescore only, no unpublishing) at threshold ${threshold}?`
        : `Run the content quality audit? Pages scoring below ${threshold} will be unpublished.`
    )) return;
    setAuditing(true);
    try {
      const res = await adminSeoQualityAudit(adminToken, { unpublishBelow: threshold, dryRun });
      const d = res.data || {};
      toast.success(`Rescored ${d.rescored || 0} pages · ${d.unpublished || 0} ${dryRun ? 'would be' : ''} unpublished`);
      loadSummary();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Quality audit failed');
    } finally {
      setAuditing(false);
    }
  };

  const runScan = async () => {
    if (!confirm(`Scan all published pages for near-duplicates ≥ ${(dupThreshold * 100).toFixed(0)}% similarity? This may take a while on large catalogs.`)) return;
    setScanning(true);
    try {
      const res = await adminSeoDuplicateScan(adminToken, { similarityThreshold: dupThreshold, scope: 'subject' });
      const d = res.data || {};
      toast.success(`Scanned ${d.pages_scanned || 0} pages · ${d.pairs_found || 0} duplicate pairs flagged`);
      loadSummary();
      loadPairs();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Duplicate scan failed');
    } finally {
      setScanning(false);
    }
  };

  const resolvePair = async (pair, action) => {
    setResolvingId(pair.id);
    try {
      await adminSeoResolveDuplicate(adminToken, pair.id, action);
      setPairs(prev => prev.filter(p => p.id !== pair.id));
      toast.success(`Pair resolved (${action})`);
      loadSummary();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Resolve failed');
    } finally {
      setResolvingId(null);
    }
  };

  const lastAudit = summary?.last_audit;
  const lastScan = summary?.last_duplicate_scan;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Published" value={summary?.total_published ?? '—'} color="#10b981" />
        <StatCard label="Drafts" value={summary?.total_drafts ?? '—'} color="#f59e0b" />
        <StatCard
          label={`Below ${summary?.threshold ?? 90}`}
          value={summary?.below_threshold ?? '—'}
          color={(summary?.below_threshold || 0) > 0 ? '#ef4444' : '#10b981'}
          hint="Live published pages under quality bar"
        />
        <StatCard
          label="Open Duplicate Pairs"
          value={summary?.duplicate_pairs_open ?? '—'}
          color={(summary?.duplicate_pairs_open || 0) > 0 ? '#ef4444' : '#10b981'}
        />
      </div>

      <Histogram histogram={summary?.histogram} />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="rounded-xl border border-gray-200 bg-white p-4">
          <div className="flex items-center gap-2 mb-2">
            <ShieldAlert size={14} className="text-violet-500" />
            <h3 className="text-sm font-bold text-gray-900">Run Content Quality Audit</h3>
          </div>
          <p className="text-xs text-gray-500 mb-3">
            Re-scores every published SEO page against the current quality model.
            Pages below the threshold are moved to draft and removed from the sitemap.
          </p>
          <div className="flex items-center gap-2 mb-3">
            <label className="text-xs text-gray-500">Threshold</label>
            <input
              type="number" min={50} max={100} value={threshold}
              onChange={e => setThreshold(Math.max(50, Math.min(100, parseInt(e.target.value || '90', 10))))}
              className="w-20 h-8 px-2 rounded-lg border border-gray-200 text-xs"
            />
            <label className="ml-2 flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer">
              <input type="checkbox" checked={dryRun} onChange={e => setDryRun(e.target.checked)} />
              Dry run
            </label>
          </div>
          <button
            onClick={runAudit} disabled={auditing}
            className="w-full h-9 rounded-lg bg-violet-600 hover:bg-violet-500 disabled:opacity-60 text-white text-xs font-semibold flex items-center justify-center gap-2"
          >
            {auditing ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
            {auditing ? 'Auditing…' : 'Run Quality Audit'}
          </button>
          {lastAudit && (
            <div className="mt-3 text-[11px] text-gray-500">
              Last audit · {new Date(lastAudit.run_at).toLocaleString()} ·{' '}
              {lastAudit.rescored} rescored · {lastAudit.unpublished} unpublished
              {lastAudit.dry_run ? ' (dry-run)' : ''}
            </div>
          )}
        </div>

        <div className="rounded-xl border border-gray-200 bg-white p-4">
          <div className="flex items-center gap-2 mb-2">
            <Copy size={14} className="text-amber-500" />
            <h3 className="text-sm font-bold text-gray-900">Near-Duplicate Detection</h3>
          </div>
          <p className="text-xs text-gray-500 mb-3">
            Builds a MinHash fingerprint for every published page and flags pairs
            with similarity above the threshold (compared within each subject).
          </p>
          <div className="flex items-center gap-2 mb-3">
            <label className="text-xs text-gray-500">Similarity ≥</label>
            <input
              type="number" min={0.5} max={1} step={0.05} value={dupThreshold}
              onChange={e => setDupThreshold(Math.max(0.5, Math.min(1, parseFloat(e.target.value || '0.8'))))}
              className="w-20 h-8 px-2 rounded-lg border border-gray-200 text-xs"
            />
            <span className="text-xs text-gray-400">({(dupThreshold * 100).toFixed(0)}%)</span>
          </div>
          <button
            onClick={runScan} disabled={scanning}
            className="w-full h-9 rounded-lg bg-amber-500 hover:bg-amber-400 disabled:opacity-60 text-white text-xs font-semibold flex items-center justify-center gap-2"
          >
            {scanning ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
            {scanning ? 'Scanning…' : 'Run Duplicate Scan'}
          </button>
          {lastScan && (
            <div className="mt-3 text-[11px] text-gray-500">
              Last scan · {new Date(lastScan.run_at).toLocaleString()} ·{' '}
              {lastScan.pages_scanned} pages · {lastScan.pairs_found} pairs flagged
            </div>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-bold text-gray-900">Duplicate Pairs Needing Review</h3>
            <span className="text-[11px] text-gray-400">{pairs.length} open</span>
          </div>
          <button onClick={() => { loadSummary(); loadPairs(); }}
            className="p-1.5 rounded-lg border border-gray-200 hover:bg-gray-50 text-gray-400">
            <RefreshCw size={13} className={loading || pairsLoading ? 'animate-spin' : ''} />
          </button>
        </div>
        {pairsLoading ? (
          <div className="p-6 text-center text-xs text-gray-400"><Loader2 size={14} className="animate-spin inline mr-1" /> Loading pairs…</div>
        ) : pairs.length === 0 ? (
          <div className="p-6 text-center text-xs text-gray-400 flex flex-col items-center gap-1.5">
            <CheckCircle2 size={16} className="text-emerald-500" />
            No open duplicate pairs. Run a scan to refresh.
          </div>
        ) : (
          <div className="divide-y divide-gray-100">
            {pairs.map(p => (
              <div key={p.id} className="p-3 flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded"
                      style={{ background: '#fef3c7', color: '#b45309' }}>
                      {(p.similarity * 100).toFixed(1)}% match
                    </span>
                    <span className="text-[10px] text-gray-400">{p.subject_name}</span>
                  </div>
                  <div className="text-xs text-gray-700 truncate">
                    <span className="font-semibold">A:</span> {p.page_a_title} · <em>{p.page_a_type}</em>
                  </div>
                  <div className="text-xs text-gray-700 truncate">
                    <span className="font-semibold">B:</span> {p.page_b_title} · <em>{p.page_b_type}</em>
                  </div>
                </div>
                <div className="flex flex-col gap-1.5">
                  <button
                    disabled={resolvingId === p.id}
                    onClick={() => resolvePair(p, 'unpublish_a')}
                    className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-gray-50 text-gray-600">
                    Unpublish A
                  </button>
                  <button
                    disabled={resolvingId === p.id}
                    onClick={() => resolvePair(p, 'unpublish_b')}
                    className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-gray-50 text-gray-600">
                    Unpublish B
                  </button>
                  <button
                    disabled={resolvingId === p.id}
                    onClick={() => resolvePair(p, 'ignore')}
                    className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-gray-50 text-gray-400">
                    Ignore
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
