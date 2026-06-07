import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Languages, RefreshCw, Search, ChevronDown, ChevronRight,
  Loader2, CheckCircle2, AlertTriangle, Play, CheckCheck,
} from 'lucide-react';
import axios from 'axios';
import { toast } from 'sonner';
import { API_BASE } from '@/utils/api';

const fmtPct = (n, d) =>
  d > 0 ? `${((n / d) * 100).toFixed(1)}%` : '—';

function ProgressBar({ translated, total }) {
  const pct = total > 0 ? Math.min(100, (translated / total) * 100) : 0;
  const color =
    pct >= 90 ? 'bg-emerald-500' : pct >= 60 ? 'bg-amber-400' : 'bg-rose-400';
  return (
    <div className="h-1.5 w-full rounded-full bg-gray-100 overflow-hidden">
      <div
        className={`h-full rounded-full transition-all duration-500 ${color}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function ChapterRow({ chapter, subjectId, adminToken, onDone }) {
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  const generate = useCallback(async () => {
    setLoading(true);
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (adminToken) headers['X-Admin-Token'] = adminToken;
      const res = await axios.post(
        `${API_BASE}/admin/content/chapters/${chapter.id}/generate-notes/as`,
        { force: false },
        { headers, withCredentials: true },
      );
      if (res.data?.status === 'translated') {
        setDone(true);
        toast.success(`Assamese generated for "${chapter.title}"`);
        onDone(chapter.id);
      } else if (res.data?.status === 'skipped_existing') {
        setDone(true);
        toast.info(`Already translated: "${chapter.title}"`);
        onDone(chapter.id);
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Generation failed');
    } finally {
      setLoading(false);
    }
  }, [chapter, adminToken, onDone]);

  if (done) return null;

  return (
    <div className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-gray-50 group">
      <span className="w-6 text-right text-[11px] text-gray-400 tabular-nums flex-shrink-0">
        {chapter.chapter_number ?? '—'}
      </span>
      <span className="flex-1 text-sm text-gray-800 truncate" title={chapter.title}>
        {chapter.title}
      </span>
      {chapter.status && chapter.status !== 'published' && (
        <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-gray-100 text-gray-500 flex-shrink-0">
          {chapter.status}
        </span>
      )}
      <button
        onClick={generate}
        disabled={loading}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium
          text-violet-700 bg-violet-50 border border-violet-200 hover:bg-violet-100
          disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex-shrink-0
          opacity-0 group-hover:opacity-100 focus:opacity-100"
        title="Generate Assamese translation"
      >
        {loading
          ? <Loader2 size={11} className="animate-spin" />
          : <Play size={11} />}
        {loading ? 'Generating…' : 'Generate'}
      </button>
    </div>
  );
}

function SubjectGroup({ group, adminToken, onChapterDone }) {
  const [open, setOpen] = useState(group.missing <= 5);
  const [visibleIds, setVisibleIds] = useState(() => new Set(group.chapters.map((c) => c.id)));

  const handleDone = useCallback(
    (chId) => {
      setVisibleIds((prev) => {
        const next = new Set(prev);
        next.delete(chId);
        return next;
      });
      onChapterDone();
    },
    [onChapterDone],
  );

  const visible = group.chapters.filter((c) => visibleIds.has(c.id));
  const remaining = visible.length;

  if (remaining === 0) return null;

  const pct = group.total > 0
    ? (((group.total - remaining) / group.total) * 100).toFixed(0)
    : 0;

  return (
    <div className="border border-gray-200 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-gray-50 text-left"
      >
        {open
          ? <ChevronDown size={14} className="text-gray-400 flex-shrink-0" />
          : <ChevronRight size={14} className="text-gray-400 flex-shrink-0" />}
        <span className="flex-1 font-medium text-sm text-gray-800 truncate">
          {group.subject_name}
        </span>
        <span className="flex items-center gap-2 flex-shrink-0">
          <span className="text-xs text-gray-500 tabular-nums">
            {group.total - remaining}/{group.total}
          </span>
          <span className={`text-[11px] font-semibold tabular-nums px-1.5 py-0.5 rounded-md ${
            remaining === 0 ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
          }`}>
            {remaining} missing
          </span>
          <span className="text-[11px] text-gray-400 w-8 text-right">{pct}%</span>
        </span>
      </button>

      {open && (
        <div className="border-t border-gray-100 px-1 py-1 max-h-64 overflow-y-auto">
          {visible.map((ch) => (
            <ChapterRow
              key={ch.id}
              chapter={ch}
              subjectId={group.subject_id}
              adminToken={adminToken}
              onDone={handleDone}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function AdminTranslationProgress({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [version, setVersion] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const headers = useCallback(() => {
    const h = {};
    if (adminToken) h['X-Admin-Token'] = adminToken;
    return h;
  }, [adminToken]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(
        `${API_BASE}/admin/content/translation-progress`,
        { headers: headers(), withCredentials: true },
      );
      if (mountedRef.current) setData(res.data);
    } catch (e) {
      if (mountedRef.current)
        setError(e?.response?.data?.detail || e?.message || 'Failed to load');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [headers]);

  useEffect(() => { load(); }, [load, version]);

  const handleChapterDone = useCallback(() => {
    setData((prev) => {
      if (!prev) return prev;
      return { ...prev, translated: prev.translated + 1, missing: prev.missing - 1 };
    });
  }, []);

  const refresh = () => setVersion((v) => v + 1);

  const filteredSubjects = (data?.subjects || []).filter((s) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    if (s.subject_name.toLowerCase().includes(q)) return true;
    return s.chapters.some((c) => c.title.toLowerCase().includes(q));
  }).map((s) => {
    if (!search.trim()) return s;
    const q = search.toLowerCase();
    if (s.subject_name.toLowerCase().includes(q)) return s;
    return { ...s, chapters: s.chapters.filter((c) => c.title.toLowerCase().includes(q)) };
  });

  const total = data?.total ?? 0;
  const translated = data?.translated ?? 0;
  const missing = data?.missing ?? 0;
  const pct = total > 0 ? ((translated / total) * 100).toFixed(1) : '0.0';

  return (
    <div className="h-full flex flex-col overflow-hidden">

      <div className="flex-shrink-0 px-4 pt-5 pb-4 border-b border-gray-100 bg-white space-y-4">

        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-amber-50 flex items-center justify-center">
              <Languages size={16} className="text-amber-600" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-gray-900">Translation Progress</h2>
              <p className="text-[11px] text-gray-500">Chapters missing Assamese (অসমীয়া) content</p>
            </div>
          </div>
          <button
            onClick={refresh}
            disabled={loading}
            className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition-colors"
            title="Refresh"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>

        {data && (
          <div className="space-y-2.5">
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-gray-50 rounded-xl px-3 py-2.5 text-center">
                <p className="text-lg font-bold text-gray-900 tabular-nums">{total.toLocaleString()}</p>
                <p className="text-[11px] text-gray-500">Total chapters</p>
              </div>
              <div className="bg-emerald-50 rounded-xl px-3 py-2.5 text-center">
                <p className="text-lg font-bold text-emerald-700 tabular-nums">{translated.toLocaleString()}</p>
                <p className="text-[11px] text-emerald-600">Translated</p>
              </div>
              <div className={`rounded-xl px-3 py-2.5 text-center ${missing > 0 ? 'bg-rose-50' : 'bg-emerald-50'}`}>
                <p className={`text-lg font-bold tabular-nums ${missing > 0 ? 'text-rose-700' : 'text-emerald-700'}`}>
                  {missing.toLocaleString()}
                </p>
                <p className={`text-[11px] ${missing > 0 ? 'text-rose-600' : 'text-emerald-600'}`}>Missing</p>
              </div>
            </div>

            <div className="space-y-1">
              <div className="flex justify-between items-center">
                <span className="text-xs text-gray-500">Overall coverage</span>
                <span className={`text-xs font-semibold tabular-nums ${
                  Number(pct) >= 90 ? 'text-emerald-700' : Number(pct) >= 60 ? 'text-amber-700' : 'text-rose-700'
                }`}>{pct}%</span>
              </div>
              <ProgressBar translated={translated} total={total} />
            </div>
          </div>
        )}

        {missing === 0 && data && (
          <div className="flex items-center gap-2 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-xl px-3 py-2.5">
            <CheckCheck size={16} />
            <span className="font-medium">All chapters have Assamese content!</span>
          </div>
        )}
      </div>

      {error && (
        <div className="flex-shrink-0 m-4 flex items-center gap-2 text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-xl px-3 py-2">
          <AlertTriangle size={14} />
          {error}
        </div>
      )}

      {loading && !data && (
        <div className="flex-1 flex items-center justify-center text-gray-400 text-sm gap-2">
          <Loader2 size={16} className="animate-spin" /> Loading translation data…
        </div>
      )}

      {data && missing > 0 && (
        <>
          <div className="flex-shrink-0 px-4 py-3 border-b border-gray-100 bg-white">
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                placeholder="Filter by subject or chapter…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full pl-8 pr-3 py-2 text-sm border border-gray-200 rounded-xl bg-gray-50
                  focus:outline-none focus:ring-2 focus:ring-violet-300 focus:border-transparent"
              />
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
            {filteredSubjects.length === 0 && (
              <p className="text-center text-sm text-gray-400 py-8">No results for "{search}"</p>
            )}
            {filteredSubjects.map((group) => (
              <SubjectGroup
                key={group.subject_id}
                group={group}
                adminToken={adminToken}
                onChapterDone={handleChapterDone}
              />
            ))}
          </div>
        </>
      )}

    </div>
  );
}
