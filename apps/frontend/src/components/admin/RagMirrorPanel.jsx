import { useState } from 'react';
import axios from 'axios';
import { Loader2, Sparkles, CheckCircle2, AlertTriangle } from 'lucide-react';
import { API_BASE } from '@/utils/api';

/**
 * RagMirrorPanel — bulk auto-generate rag_sections_en for every chapter
 * that has English notes but no RAG sections yet.
 *
 * Calls POST /api/v1/admin/cron/bulk-mirror-rag (Bearer TRANSLATE_CRON_SECRET).
 *
 * The backend splits notes_en by ## / ### headings into {title, content}
 * plain-text chunks and writes them to rag_sections_en in MongoDB.
 */
export default function RagMirrorPanel({ adminToken }) {
  const [running,    setRunning]    = useState(false);
  const [result,     setResult]     = useState(null);   // last run stats
  const [error,      setError]      = useState(null);
  const [force,      setForce]      = useState(false);
  const [subjectId,  setSubjectId]  = useState('');
  const [limit,      setLimit]      = useState('');

  const run = async () => {
    if (running) return;
    setRunning(true);
    setResult(null);
    setError(null);

    try {
      const params = new URLSearchParams();
      if (force)         params.set('force', 'true');
      if (limit.trim())  params.set('limit', limit.trim());
      if (subjectId.trim()) params.set('subject_id', subjectId.trim());

      const { data } = await axios.post(
        `${API_BASE}/admin/cron/bulk-mirror-rag?${params}`,
        {},
        { headers: { Authorization: `Bearer ${adminToken}` } },
      );
      setResult(data);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Request failed');
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="max-w-xl mx-auto space-y-5 py-6 px-4">

      {/* Header */}
      <div>
        <h2 className="text-base font-semibold text-gray-900 flex items-center gap-2">
          <Sparkles size={16} className="text-emerald-500" />
          Bulk Mirror RAG from Notes
        </h2>
        <p className="mt-1 text-xs text-gray-500 leading-relaxed">
          Auto-generates <code className="font-mono bg-gray-100 px-1 rounded">rag_sections_en</code> for every
          chapter that has English notes but no RAG sections yet.
          The job splits <code className="font-mono bg-gray-100 px-1 rounded">notes_en</code> by{' '}
          <code className="font-mono bg-gray-100 px-1 rounded">##</code> headings into plain-text chunks
          and saves them to MongoDB. Use <em>Reindex</em> in the chapter editor afterward to push
          them to Vectorize.
        </p>
      </div>

      {/* Options */}
      <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 space-y-3">
        <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Options</p>

        <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={force}
            onChange={e => setForce(e.target.checked)}
            className="rounded border-gray-300 text-emerald-600 focus:ring-emerald-500"
          />
          <span>
            <span className="font-medium">Force overwrite</span>
            <span className="text-gray-400 ml-1">— also overwrite chapters that already have sections</span>
          </span>
        </label>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Subject ID <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={subjectId}
              onChange={e => setSubjectId(e.target.value)}
              placeholder="MongoDB ObjectId…"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-xs font-mono focus:outline-none focus:ring-2 focus:ring-emerald-400"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Max chapters <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <input
              type="number"
              value={limit}
              onChange={e => setLimit(e.target.value)}
              placeholder="All"
              min="1"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-emerald-400"
            />
          </div>
        </div>
      </div>

      {/* Run button */}
      <button
        onClick={run}
        disabled={running}
        className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-emerald-600 hover:bg-emerald-700 disabled:bg-emerald-300 text-white transition-colors"
      >
        {running
          ? <><Loader2 size={14} className="animate-spin" /> Running…</>
          : <><Sparkles size={14} /> Run Bulk Mirror</>}
      </button>

      {/* Error */}
      {error && (
        <div className="flex items-start gap-2 bg-rose-50 border border-rose-200 rounded-xl px-4 py-3 text-xs text-rose-700">
          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-emerald-800">
            <CheckCircle2 size={15} /> Run complete
          </div>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
            <div>
              <dt className="text-gray-500">Sections written</dt>
              <dd className="font-semibold text-gray-900">{result.processed ?? 0} chapters</dd>
            </div>
            <div>
              <dt className="text-gray-500">Skipped (no notes)</dt>
              <dd className="font-semibold text-gray-900">{result.skipped ?? 0}</dd>
            </div>
            <div>
              <dt className="text-gray-500">No headings found</dt>
              <dd className="font-semibold text-gray-900">{result.no_headings ?? 0}</dd>
            </div>
            <div>
              <dt className="text-gray-500">Errors</dt>
              <dd className="font-semibold text-rose-600">{result.errors?.length ?? 0}</dd>
            </div>
          </dl>
          {result.errors?.length > 0 && (
            <details className="mt-1">
              <summary className="text-xs text-rose-500 cursor-pointer hover:text-rose-700">
                Show errors ({result.errors.length})
              </summary>
              <pre className="mt-2 text-[11px] font-mono text-rose-700 bg-rose-50 rounded-lg p-2 overflow-x-auto whitespace-pre-wrap">
                {result.errors.join('\n')}
              </pre>
            </details>
          )}
          <p className="text-xs text-emerald-700">
            Open each chapter in the editor and press <strong>Reindex</strong> to push the new sections to Vectorize.
          </p>
        </div>
      )}
    </div>
  );
}
