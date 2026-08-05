import { useState } from 'react';
import axios from 'axios';
import { Loader2, Sparkles, CheckCircle2, AlertTriangle, Zap } from 'lucide-react';
import { API_BASE } from '@/utils/api';

/**
 * RagMirrorPanel — two-step workflow:
 *
 * Step 1 — Mirror: splits notes_en by ## headings → rag_sections_en in MongoDB.
 *   POST /api/v1/admin/cron/bulk-mirror-rag
 *
 * Step 2 — Reindex: pushes rag_sections_en to Cloudflare Vectorize as
 *   individual topic-section chunks.
 *   POST /api/v1/admin/cron/bulk-reindex
 *
 * Auth: Bearer TRANSLATE_CRON_SECRET for both endpoints.
 */

function OptionPanel({ children }) {
  return (
    <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 space-y-3">
      <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Options</p>
      {children}
    </div>
  );
}

function ResultCard({ result, color = 'emerald', children }) {
  const c = color === 'emerald'
    ? { bg: 'bg-emerald-50', border: 'border-emerald-200', title: 'text-emerald-800', body: 'text-emerald-700' }
    : { bg: 'bg-violet-50', border: 'border-violet-200', title: 'text-violet-800', body: 'text-violet-700' };
  return (
    <div className={`${c.bg} border ${c.border} rounded-xl px-4 py-4 space-y-3`}>
      <div className={`flex items-center gap-2 text-sm font-semibold ${c.title}`}>
        <CheckCircle2 size={15} /> Run complete
      </div>
      {children}
    </div>
  );
}

function ErrorBox({ error }) {
  if (!error) return null;
  return (
    <div className="flex items-start gap-2 bg-rose-50 border border-rose-200 rounded-xl px-4 py-3 text-xs text-rose-700">
      <AlertTriangle size={14} className="shrink-0 mt-0.5" />
      <span>{error}</span>
    </div>
  );
}

export default function RagMirrorPanel({ adminToken }) {
  // ── Step 1: Mirror ──────────────────────────────────────────────────────────
  const [mirrorRunning, setMirrorRunning] = useState(false);
  const [mirrorResult,  setMirrorResult]  = useState(null);
  const [mirrorError,   setMirrorError]   = useState(null);
  const [force,         setForce]         = useState(false);
  const [subjectId,     setSubjectId]     = useState('');
  const [limit,         setLimit]         = useState('');

  // ── Step 2: Reindex ─────────────────────────────────────────────────────────
  const [reindexRunning, setReindexRunning] = useState(false);
  const [reindexResult,  setReindexResult]  = useState(null);
  const [reindexError,   setReindexError]   = useState(null);
  const [reindexForce,   setReindexForce]   = useState(false);
  const [reindexConcurrency, setReindexConcurrency] = useState('3');

  const runMirror = async () => {
    if (mirrorRunning) return;
    setMirrorRunning(true);
    setMirrorResult(null);
    setMirrorError(null);
    try {
      const params = new URLSearchParams();
      if (force)            params.set('force', 'true');
      if (limit.trim())     params.set('limit', limit.trim());
      if (subjectId.trim()) params.set('subject_id', subjectId.trim());
      const { data } = await axios.post(
        `${API_BASE}/admin/cron/bulk-mirror-rag?${params}`,
        {},
        { headers: { Authorization: `Bearer ${adminToken}` } },
      );
      setMirrorResult(data);
    } catch (err) {
      setMirrorError(err?.response?.data?.detail || err.message || 'Request failed');
    } finally {
      setMirrorRunning(false);
    }
  };

  const runReindex = async () => {
    if (reindexRunning) return;
    setReindexRunning(true);
    setReindexResult(null);
    setReindexError(null);
    try {
      const params = new URLSearchParams();
      if (reindexForce)              params.set('force', 'true');
      if (subjectId.trim())          params.set('subject_id', subjectId.trim());
      if (limit.trim())              params.set('limit', limit.trim());
      if (reindexConcurrency.trim()) params.set('concurrency', reindexConcurrency.trim());
      const { data } = await axios.post(
        `${API_BASE}/admin/cron/bulk-reindex?${params}`,
        {},
        { headers: { Authorization: `Bearer ${adminToken}` } },
      );
      setReindexResult(data);
    } catch (err) {
      setReindexError(err?.response?.data?.detail || err.message || 'Request failed');
    } finally {
      setReindexRunning(false);
    }
  };

  return (
    <div className="max-w-xl mx-auto space-y-8 py-6 px-4">

      {/* ── Step 1: Mirror ─────────────────────────────────────────────────── */}
      <section className="space-y-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-emerald-100 text-emerald-700 text-[11px] font-bold">1</span>
            <h2 className="text-sm font-semibold text-gray-900">Mirror notes → RAG sections</h2>
          </div>
          <p className="text-xs text-gray-500 leading-relaxed pl-7">
            Splits <code className="font-mono bg-gray-100 px-1 rounded">notes_en</code> by{' '}
            <code className="font-mono bg-gray-100 px-1 rounded">##</code> headings into
            plain-text <code className="font-mono bg-gray-100 px-1 rounded">rag_sections_en</code>{' '}
            in MongoDB. Skips chapters that already have sections unless Force is checked.
          </p>
        </div>

        <OptionPanel>
          <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer select-none">
            <input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)}
              className="rounded border-gray-300 text-emerald-600 focus:ring-emerald-500" />
            <span>
              <span className="font-medium">Force overwrite</span>
              <span className="text-gray-400 ml-1">— overwrite existing sections</span>
            </span>
          </label>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                Subject ID <span className="text-gray-400 font-normal">(optional)</span>
              </label>
              <input type="text" value={subjectId} onChange={e => setSubjectId(e.target.value)}
                placeholder="MongoDB ObjectId…"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-xs font-mono focus:outline-none focus:ring-2 focus:ring-emerald-400" />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">
                Max chapters <span className="text-gray-400 font-normal">(optional)</span>
              </label>
              <input type="number" value={limit} onChange={e => setLimit(e.target.value)}
                placeholder="All" min="1"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-emerald-400" />
            </div>
          </div>
        </OptionPanel>

        <button onClick={runMirror} disabled={mirrorRunning}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-emerald-600 hover:bg-emerald-700 disabled:bg-emerald-300 text-white transition-colors">
          {mirrorRunning
            ? <><Loader2 size={14} className="animate-spin" /> Mirroring…</>
            : <><Sparkles size={14} /> Run Bulk Mirror</>}
        </button>

        <ErrorBox error={mirrorError} />

        {mirrorResult && (
          <ResultCard result={mirrorResult} color="emerald">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
              <div><dt className="text-gray-500">Sections written</dt>
                <dd className="font-semibold text-gray-900">{mirrorResult.processed ?? 0} chapters</dd></div>
              <div><dt className="text-gray-500">Skipped (no notes)</dt>
                <dd className="font-semibold text-gray-900">{mirrorResult.skipped ?? 0}</dd></div>
              <div><dt className="text-gray-500">No headings found</dt>
                <dd className="font-semibold text-gray-900">{mirrorResult.no_headings ?? 0}</dd></div>
              <div><dt className="text-gray-500">Errors</dt>
                <dd className="font-semibold text-rose-600">{mirrorResult.errors?.length ?? 0}</dd></div>
            </dl>
            {mirrorResult.errors?.length > 0 && (
              <details>
                <summary className="text-xs text-rose-500 cursor-pointer hover:text-rose-700">
                  Show errors ({mirrorResult.errors.length})
                </summary>
                <pre className="mt-2 text-[11px] font-mono text-rose-700 bg-rose-50 rounded-lg p-2 overflow-x-auto whitespace-pre-wrap">
                  {mirrorResult.errors.join('\n')}
                </pre>
              </details>
            )}
            <p className="text-xs text-emerald-700">
              ✓ Sections written to MongoDB. Run <strong>Step 2</strong> below to push them to Vectorize.
            </p>
          </ResultCard>
        )}
      </section>

      {/* ── Divider ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3">
        <div className="flex-1 h-px bg-gray-200" />
        <span className="text-xs text-gray-400 font-medium">then</span>
        <div className="flex-1 h-px bg-gray-200" />
      </div>

      {/* ── Step 2: Reindex ────────────────────────────────────────────────── */}
      <section className="space-y-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-violet-100 text-violet-700 text-[11px] font-bold">2</span>
            <h2 className="text-sm font-semibold text-gray-900">Push sections → Vectorize</h2>
          </div>
          <p className="text-xs text-gray-500 leading-relaxed pl-7">
            Embeds every <code className="font-mono bg-gray-100 px-1 rounded">rag_sections_en</code> entry
            and upserts it to Cloudflare Vectorize as an individual topic-section chunk.
            Chapters already indexed are skipped unless Force is checked.
          </p>
        </div>

        <OptionPanel>
          <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer select-none">
            <input type="checkbox" checked={reindexForce} onChange={e => setReindexForce(e.target.checked)}
              className="rounded border-gray-300 text-violet-600 focus:ring-violet-500" />
            <span>
              <span className="font-medium">Force re-index</span>
              <span className="text-gray-400 ml-1">— push even already-indexed chapters</span>
            </span>
          </label>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Concurrency <span className="text-gray-400 font-normal">(1–6, default 3)</span>
            </label>
            <input type="number" value={reindexConcurrency}
              onChange={e => setReindexConcurrency(e.target.value)}
              min="1" max="6" placeholder="3"
              className="w-28 px-3 py-2 border border-gray-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-violet-400" />
          </div>
        </OptionPanel>

        <button onClick={runReindex} disabled={reindexRunning}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-violet-600 hover:bg-violet-700 disabled:bg-violet-300 text-white transition-colors">
          {reindexRunning
            ? <><Loader2 size={14} className="animate-spin" /> Indexing…</>
            : <><Zap size={14} /> Run Bulk Reindex</>}
        </button>

        <ErrorBox error={reindexError} />

        {reindexResult && (
          <ResultCard result={reindexResult} color="violet">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
              <div><dt className="text-gray-500">Chapters indexed</dt>
                <dd className="font-semibold text-gray-900">{reindexResult.processed ?? 0}</dd></div>
              <div><dt className="text-gray-500">Skipped (already indexed)</dt>
                <dd className="font-semibold text-gray-900">{reindexResult.skipped ?? 0}</dd></div>
              <div><dt className="text-gray-500">Errors</dt>
                <dd className="font-semibold text-rose-600">{reindexResult.errors?.length ?? 0}</dd></div>
            </dl>
            {reindexResult.errors?.length > 0 && (
              <details>
                <summary className="text-xs text-rose-500 cursor-pointer hover:text-rose-700">
                  Show errors ({reindexResult.errors.length})
                </summary>
                <pre className="mt-2 text-[11px] font-mono text-rose-700 bg-rose-50 rounded-lg p-2 overflow-x-auto whitespace-pre-wrap">
                  {reindexResult.errors.join('\n')}
                </pre>
              </details>
            )}
            <p className="text-xs text-violet-700">
              ✓ Sections are live in Vectorize — chat queries will now match topic-level chunks.
            </p>
          </ResultCard>
        )}
      </section>

    </div>
  );
}
