import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Check, ChevronDown, Database, Eye, RefreshCw, RotateCcw, Shield, X } from 'lucide-react';
import axios from 'axios';
import { toast } from 'sonner';
import { API_BASE } from '@/utils/api';
import { getToken } from '@/hooks/useTokenManager';

const client = () => axios.create({
  baseURL: API_BASE,
  withCredentials: true,
  headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {},
});

const scopes = [
  { id: 'notes', label: 'Notes' },
  { id: 'qa', label: 'Important questions' },
  { id: 'pyq', label: 'PYQ' },
];

// Backend semantics: null is legacy full staff access; [] explicitly grants
// nothing. Undefined identity data is never treated as a grant.
const can = (user, capability) => user?.role === 'admin' || user?.capabilities === null || (Array.isArray(user?.capabilities) && user.capabilities.includes(capability));
const fmt = (value) => value ? new Date(value * 1000 || value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—';

function CapabilityNotice({ capability }) {
  return <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800" role="status">
    This control is unavailable. Required capability: <strong>{capability}</strong>.
  </div>;
}

function JobRow({ job, onRetry, retrying }) {
  const items = (() => { try { return JSON.parse(job.items || '[]'); } catch { return []; } })();
  const done = items.filter(item => item.status === 'done').length;
  const failed = items.filter(item => item.status === 'failed').length;
  const pct = items.length ? Math.round((done / items.length) * 100) : 0;
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4" data-testid={`rag-job-${job.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
            <span className={`h-2 w-2 rounded-full ${job.status === 'done' ? 'bg-emerald-500' : job.status === 'failed' || job.status === 'partial' ? 'bg-rose-500' : 'bg-amber-500'}`} />
            {job.status}
            <span className="font-mono text-[10px] text-slate-400">{job.id.slice(0, 8)}</span>
          </div>
          <p className="mt-1 text-xs text-slate-500">{items.length} chapters · updated {fmt(job.updated_at)}</p>
        </div>
        {failed > 0 && <button type="button" onClick={() => onRetry(job.id)} disabled={retrying} className="inline-flex items-center gap-1.5 rounded-lg border border-rose-200 px-2.5 py-1.5 text-xs font-semibold text-rose-700 hover:bg-rose-50 disabled:opacity-50" data-testid={`button-retry-failed-${job.id}`}><RotateCcw size={13} /> Retry failed ({failed})</button>}
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-violet-600 transition-[width]" style={{ width: `${pct}%` }} /></div>
      <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-slate-500"><span>{done} complete</span><span>{items.filter(item => item.status === 'running').length} running</span><span className={failed ? 'text-rose-600' : ''}>{failed} failed</span></div>
      {items.filter(item => item.status === 'failed').map(item => <div key={item.chapter_id} className="mt-2 rounded-lg bg-rose-50 px-2.5 py-2 text-xs text-rose-700"><strong>{item.chapter_id.slice(0, 8)}</strong> {item.error || 'Indexing failed'}</div>)}
    </div>
  );
}

export default function StaffOperations({ user, subjects = [], chapters = [], selectedSubject, onRefresh }) {
  const [jobList, setJobList] = useState(null);
  const [jobError, setJobError] = useState(null);
  const [selected, setSelected] = useState([]);
  const [selectedScopes, setSelectedScopes] = useState(['notes', 'qa', 'pyq']);
  const [loading, setLoading] = useState(false);
  const [retrying, setRetrying] = useState(null);
  const [operation, setOperation] = useState('publish');
  const [preview, setPreview] = useState(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewIds, setPreviewIds] = useState([]);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [subjectId, setSubjectId] = useState(selectedSubject?.id || subjects[0]?.id || '');
  const [subjectChapters, setSubjectChapters] = useState([]);
  const [chaptersLoading, setChaptersLoading] = useState(false);
  const [chaptersError, setChaptersError] = useState(null);
  const allowedReindex = can(user, 'rag:reindex');
  const allowedDelete = can(user, 'content:delete');
  const allowedPublish = can(user, 'content:publish');
  const canOperate = operation === 'reindex' ? allowedReindex : operation === 'delete' ? allowedDelete : allowedPublish;
  const visibleChapters = useMemo(() => subjectChapters, [subjectChapters]);

  const loadChapters = useCallback(async () => {
    if (!subjectId) { setSubjectChapters([]); return; }
    setChaptersLoading(true); setChaptersError(null);
    try { const res = await client().get(`/staff/content/chapters/${subjectId}`); setSubjectChapters(Array.isArray(res.data) ? res.data : (res.data?.chapters || [])); }
    catch (error) { setSubjectChapters([]); setChaptersError(error?.response?.data?.detail || 'Chapter list is unavailable.'); }
    finally { setChaptersLoading(false); }
  }, [subjectId]);
  useEffect(() => { loadChapters(); }, [loadChapters]);

  const loadJobs = useCallback(async () => {
    setJobError(null);
    try { const res = await client().get('/staff/content/reindex-jobs'); setJobList(res.data?.jobs || []); }
    catch (error) { setJobError(error?.response?.data?.detail || 'RAG history is unavailable.'); }
  }, []);
  useEffect(() => { loadJobs(); const id = setInterval(loadJobs, 12000); return () => clearInterval(id); }, [loadJobs]);

  const toggle = (id) => setSelected(prev => prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id]);
  const toggleScope = (id) => setSelectedScopes(prev => prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id]);

  const createJob = async () => {
    if (!allowedReindex || !selected.length || !selectedScopes.length) return;
    setLoading(true);
    try { await client().post('/staff/content/reindex-jobs', { chapter_ids: selected, scopes: selectedScopes }); toast.success('RAG job queued'); setSelected([]); await loadJobs(); }
    catch (error) { toast.error(error?.response?.data?.detail || 'Could not queue RAG job'); }
    finally { setLoading(false); }
  };
  const retry = async (id) => {
    setRetrying(id);
    try { await client().post(`/staff/content/reindex-jobs/${id}/retry-failed`); toast.success('Failed chapters queued again'); await loadJobs(); }
    catch (error) { toast.error(error?.response?.data?.detail || 'Retry failed'); }
    finally { setRetrying(null); }
  };
  const previewImpact = async () => {
    if (!allowedDelete || !selected.length) return;
    setBulkBusy(true);
    try { const res = await client().post('/staff/content/bulk/impact-preview', { chapter_ids: selected }); setPreview(res.data); setPreviewIds([...selected]); setPreviewOpen(true); }
    catch (error) { toast.error(error?.response?.data?.detail || 'Impact preview failed'); }
    finally { setBulkBusy(false); }
  };
  const runBulk = async () => {
    setBulkBusy(true);
    try {
      if (operation === 'delete' && (!preview?.preview_token || previewIds.join('|') !== selected.join('|'))) {
        setPreviewOpen(false);
        toast.error('This preview is missing or no longer matches the selected chapters. Review a fresh impact preview before deleting.');
        return;
      }
      const res = await client().post(`/staff/content/bulk/${operation}`, operation === 'delete'
        ? { chapter_ids: selected, preview_token: preview.preview_token }
        : { chapter_ids: selected });
      const counts = res.data?.counts || {};
      const outcomes = Array.isArray(res.data?.outcomes) ? res.data.outcomes
        : Array.isArray(res.data?.results) ? res.data.results
          : Array.isArray(res.data?.items) ? res.data.items
            : Array.isArray(res.data?.result?.outcomes) ? res.data.result.outcomes : [];
      setOutcome(outcomes);
      toast.success(`${counts.succeeded ?? 0} completed · ${counts.failed ?? 0} failed`);
      setSelected([]); setPreviewOpen(false); onRefresh?.();
    } catch (error) { toast.error(error?.response?.data?.detail || 'Bulk operation failed'); }
    finally { setBulkBusy(false); await loadChapters(); }
  };
  const [outcome, setOutcome] = useState([]);

  return <div className="space-y-5 p-4 sm:p-6" data-testid="staff-operations">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-[11px] font-bold uppercase tracking-[.16em] text-violet-600">Operations</p><h2 className="mt-1 text-xl font-bold text-slate-900">RAG control room</h2><p className="mt-1 text-sm text-slate-500">Durable jobs, freshness, and safe chapter lifecycle actions.</p></div>
      <button type="button" onClick={loadJobs} className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50" data-testid="button-refresh-rag-history"><RefreshCw size={14} /> Refresh</button>
    </div>
    <div className="grid gap-4 xl:grid-cols-[1.05fr_.95fr]">
      <section className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4 sm:p-5">
        <div className="flex items-center justify-between gap-3"><h3 className="text-sm font-bold text-slate-900">Queue a reindex</h3><span className="rounded-full bg-violet-100 px-2 py-1 text-[10px] font-bold text-violet-700">{selected.length} selected</span></div>
        {!allowedReindex && <div className="mt-3"><CapabilityNotice capability="rag:reindex" /></div>}
        <label className="mt-4 block text-xs font-semibold text-slate-600">Subject</label>
        <select value={subjectId} onChange={e => { setSubjectId(e.target.value); setSelected([]); }} className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm" data-testid="select-rag-subject">
          <option value="">Select a subject</option>{subjects.map(subject => <option key={subject.id} value={subject.id}>{subject.name}</option>)}
        </select>
        <div className="mt-4 flex flex-wrap gap-2">{scopes.map(scope => <button key={scope.id} type="button" onClick={() => toggleScope(scope.id)} disabled={!allowedReindex} className={`rounded-lg border px-2.5 py-1.5 text-xs font-semibold ${selectedScopes.includes(scope.id) ? 'border-violet-300 bg-violet-100 text-violet-700' : 'border-slate-200 bg-white text-slate-500'}`} data-testid={`toggle-scope-${scope.id}`}>{selectedScopes.includes(scope.id) ? <Check size={12} className="mr-1 inline" /> : null}{scope.label}</button>)}</div>
        <div className="mt-4 max-h-56 space-y-1.5 overflow-auto pr-1">{chaptersLoading ? <div className="rounded-lg bg-white p-4 text-xs text-slate-500">Loading chapters…</div> : chaptersError ? <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700">{chaptersError} <button type="button" onClick={loadChapters} className="font-bold underline">Retry</button></div> : !visibleChapters.length ? <div className="rounded-lg border border-dashed border-slate-300 bg-white p-4 text-xs text-slate-500">No chapters in this subject.</div> : visibleChapters.map(chapter => { const indexed = chapter.rag_indexed_at ? new Date(chapter.rag_indexed_at).getTime() : 0; const updated = chapter.rag_updated_at ? new Date(chapter.rag_updated_at).getTime() : 0; const state = !indexed ? 'not indexed' : updated > indexed ? 'stale' : 'current'; return <label key={chapter.id} className="flex cursor-pointer items-center gap-2 rounded-lg bg-white px-3 py-2 text-xs hover:bg-violet-50"><input type="checkbox" checked={selected.includes(chapter.id)} onChange={() => toggle(chapter.id)} disabled={!canOperate} /><span className="min-w-0 flex-1 truncate text-slate-700">{chapter.title}</span><span className={`text-[10px] ${state === 'current' ? 'text-emerald-600' : state === 'stale' ? 'text-amber-600' : 'text-slate-400'}`}>{state}</span></label>; })}</div>
        <button type="button" onClick={createJob} disabled={!allowedReindex || loading || !selected.length} className="mt-4 inline-flex items-center gap-2 rounded-xl bg-violet-600 px-3.5 py-2.5 text-xs font-bold text-white hover:bg-violet-700 disabled:opacity-40" data-testid="button-queue-rag"><Database size={14} /> {loading ? 'Queuing…' : 'Queue reindex'}</button>
      </section>
      <section className="rounded-2xl border border-slate-200 bg-white p-4 sm:p-5">
        <div className="flex items-center justify-between"><h3 className="text-sm font-bold text-slate-900">Selected chapter lifecycle</h3><Shield size={16} className="text-slate-400" /></div>
        <p className="mt-1 text-xs text-slate-500">Actions return an outcome for every chapter. Deletes always require an impact preview.</p>
        <div className="mt-4 flex gap-2"><select value={operation} onChange={e => { setOperation(e.target.value); setSelected([]); setPreview(null); }} className="flex-1 rounded-xl border border-slate-200 px-3 py-2 text-xs" data-testid="select-bulk-operation"><option value="publish" disabled={!allowedPublish}>Publish</option><option value="unpublish" disabled={!allowedPublish}>Unpublish</option><option value="reindex" disabled={!allowedReindex}>Reindex</option><option value="delete" disabled={!allowedDelete}>Delete</option></select>{operation === 'delete' ? <button type="button" onClick={previewImpact} disabled={!allowedDelete || !selected.length || bulkBusy} className="inline-flex items-center gap-1.5 rounded-xl border border-rose-200 px-3 py-2 text-xs font-bold text-rose-700 disabled:opacity-40" data-testid="button-preview-delete"><Eye size={14} /> Preview</button> : <button type="button" onClick={runBulk} disabled={!selected.length || bulkBusy || !canOperate} className="rounded-xl bg-slate-900 px-3 py-2 text-xs font-bold text-white disabled:opacity-40" data-testid="button-run-bulk">Run</button>}</div>
        <div className="mt-5 rounded-xl bg-slate-50 p-3 text-xs text-slate-500"><strong className="text-slate-700">Freshness cue:</strong> current means indexed_at is present and not older than updated_at; stale means content changed after indexing; not indexed means no canonical index timestamp.</div>
        {outcome.length > 0 && <div className="mt-3 space-y-1 rounded-xl border border-slate-200 bg-white p-3"><p className="text-xs font-bold text-slate-700">Last operation results</p>{outcome.map(item => <div key={item.chapter_id} className="flex justify-between gap-3 text-xs"><span className="truncate">{item.chapter_id}</span><span className={item.status === 'done' ? 'text-emerald-600' : 'text-rose-600'}>{item.status}{item.error ? ` · ${item.error}` : ''}</span></div>)}</div>}
      </section>
    </div>
    <section><div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-bold text-slate-900">Durable job history</h3><span className="text-xs text-slate-400">polls every 12s</span></div>{jobError ? <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700"><AlertTriangle className="mr-2 inline" size={15} />{jobError} <button type="button" onClick={loadJobs} className="ml-2 font-bold underline">Retry</button></div> : !jobList ? <div className="h-24 animate-pulse rounded-2xl bg-slate-100" /> : jobList.length ? <div className="space-y-3">{jobList.map(job => <JobRow key={job.id} job={job} onRetry={retry} retrying={retrying === job.id} />)}</div> : <div className="rounded-2xl border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">No reindex jobs yet.</div>}</section>
    {previewOpen && preview && <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 p-4" role="dialog" aria-modal="true" aria-label="Delete impact preview"><div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl"><div className="flex items-center justify-between"><h3 className="font-bold text-slate-900">Confirm destructive delete</h3><button type="button" onClick={() => setPreviewOpen(false)} aria-label="Close impact preview"><X size={18} /></button></div><p className="mt-2 text-sm text-slate-500">This server-issued preview expires when the selection changes. Review a fresh preview before deleting.</p><div className="mt-4 grid grid-cols-2 gap-2">{[['Chapters', preview.chapters], ['Topics', preview.topics], ['PYQ papers', preview.pyqs], ['Chunks', preview.chunks], ['Estimated vectors', preview.vectors_estimated]].map(([label, value]) => <div key={label} className="rounded-xl bg-slate-50 p-3"><div className="text-[10px] uppercase tracking-wide text-slate-400">{label}</div><div className="mt-1 text-lg font-bold text-slate-900">{value ?? 0}</div></div>)}</div><div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => setPreviewOpen(false)} className="rounded-xl px-3 py-2 text-xs font-semibold text-slate-600">Cancel</button><button type="button" onClick={runBulk} disabled={bulkBusy || !preview.preview_token || previewIds.join('|') !== selected.join('|')} className="rounded-xl bg-rose-600 px-3 py-2 text-xs font-bold text-white disabled:opacity-50" data-testid="button-confirm-delete">{bulkBusy ? 'Deleting…' : 'Delete selected'}</button></div></div></div>}
  </div>;
}