import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  BookOpen, Loader2, CheckCircle2, Circle, AlertCircle,
  ChevronDown, Sparkles, Languages, Plus, RotateCcw,
  Cloud, Search, Zap,
} from 'lucide-react';
import { API, authHeaders } from '@/utils/adminHelpers';

function slugify(text) {
  return (text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
}

const S = {
  idle:        'idle',
  seeding:     'seeding',
  generating:  'generating',
  translating: 'translating',
  publishing:  'publishing',
  done:        'done',
  error:       'error',
};

const BUSY = new Set([S.seeding, S.generating, S.translating, S.publishing]);

const PIPELINE = [
  { id: S.seeding,     label: 'Seed Topics',       icon: Plus },
  { id: S.generating,  label: 'English Notes',      icon: Sparkles },
  { id: S.translating, label: 'Assamese (Sarvam)',  icon: Languages },
  { id: S.publishing,  label: 'Publish',            icon: Cloud },
];

const ORDER = [S.seeding, S.generating, S.translating, S.publishing, S.done];

function PipelineBar({ step }) {
  const active = ORDER.indexOf(step);
  return (
    <div className="flex items-center gap-0 mt-1">
      {PIPELINE.map((p, i) => {
        const pIdx  = ORDER.indexOf(p.id);
        const done  = step === S.done || active > pIdx;
        const cur   = step === p.id;
        const Icon  = p.icon;
        return (
          <div key={p.id} className="flex items-center">
            <div
              className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold transition-all"
              style={{
                background: done ? '#dcfce7' : cur ? '#ede9fe' : '#f1f5f9',
                color:      done ? '#16a34a' : cur ? '#7c3aed' : '#94a3b8',
              }}
            >
              {cur && !done
                ? <Loader2 size={9} className="animate-spin" />
                : done
                  ? <CheckCircle2 size={9} />
                  : <Icon size={9} />
              }
              {p.label}
            </div>
            {i < PIPELINE.length - 1 && (
              <div className="w-3 h-px mx-0.5" style={{ background: done ? '#86efac' : '#e2e8f0' }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function StatusBadge({ step }) {
  const map = {
    [S.idle]:        { color: '#94a3b8', label: 'Ready' },
    [S.seeding]:     { color: '#f59e0b', label: 'Seeding…' },
    [S.generating]:  { color: '#6366f1', label: 'Generating…' },
    [S.translating]: { color: '#06b6d4', label: 'Translating…' },
    [S.publishing]:  { color: '#8b5cf6', label: 'Publishing…' },
    [S.done]:        { color: '#10b981', label: '✓ Done' },
    [S.error]:       { color: '#ef4444', label: '✗ Error' },
  };
  const { color, label } = map[step] || map[S.idle];
  return (
    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full"
      style={{ background: color + '18', color }}>
      {label}
    </span>
  );
}

function ChapterCard({ chapter, adminToken, onDone }) {
  const [topics, setTopics] = useState(chapter.published_topics || []);
  const [input,  setInput]  = useState('');
  const [step,   setStep]   = useState(S.idle);
  const [log,    setLog]    = useState('');

  const busy = BUSY.has(step);

  const run = useCallback(async () => {
    const lines = input.split('\n').map(l => l.trim()).filter(Boolean);
    if (!lines.length) { toast.error('Enter at least one topic name'); return; }

    const cfg = authHeaders(adminToken);

    setStep(S.seeding);
    const added = [];
    for (const title of lines) {
      setLog(`Adding topic: ${title}`);
      try {
        const r = await axios.post(
          `${API}/admin/content/chapters/${chapter.id}/topics`,
          { title, topic_slug: slugify(title) },
          cfg,
        );
        added.push(r.data);
      } catch {
        toast.error(`Failed to add topic: ${title}`);
        setStep(S.error);
        return;
      }
    }
    setTopics(prev => [...prev, ...added]);
    setInput('');

    setStep(S.generating);
    setLog('Calling Gemini / Vertex AI for English notes…');
    try {
      await axios.post(`${API}/admin/content/chapters/${chapter.id}/generate-notes`, {}, cfg);
    } catch {
      toast.error('Note generation failed — check Vertex AI / Gemini key');
      setStep(S.error);
      return;
    }

    setStep(S.translating);
    setLog('Calling Sarvam AI for Assamese translation…');
    try {
      await axios.post(`${API}/admin/content/chapters/${chapter.id}/generate-notes/as`, {}, cfg);
    } catch {
      toast.error('Assamese translation failed — check SARVAM_API_KEY');
      setStep(S.error);
      return;
    }

    setStep(S.publishing);
    setLog('Publishing HTML to Cloudflare + indexing JSON in Vertex AI Search…');
    try {
      await axios.post(`${API}/admin/content/chapters/${chapter.id}/publish`, {}, cfg);
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Publishing failed';
      toast.error(`Publish step: ${msg}`);
      setStep(S.error);
      return;
    }

    setLog('');
    setStep(S.done);
    toast.success(`"${chapter.title}" seeded, generated & published!`);
    onDone?.();
  }, [input, adminToken, chapter.id, chapter.title, onDone]);

  const regenAS = useCallback(async () => {
    setStep(S.translating);
    setLog('Re-translating to Assamese…');
    try {
      await axios.post(`${API}/admin/content/chapters/${chapter.id}/generate-notes/as`, {}, authHeaders(adminToken));
      setLog('Re-publishing…');
      setStep(S.publishing);
      await axios.post(`${API}/admin/content/chapters/${chapter.id}/publish`, {}, authHeaders(adminToken));
      setStep(S.done);
      setLog('');
      toast.success('Assamese regenerated & republished');
    } catch {
      toast.error('Re-generate / publish failed');
      setStep(S.error);
    }
  }, [adminToken, chapter.id]);

  const rePublish = useCallback(async () => {
    setStep(S.publishing);
    setLog('Re-publishing to Cloudflare + Vertex AI Search…');
    try {
      await axios.post(`${API}/admin/content/chapters/${chapter.id}/publish`, {}, authHeaders(adminToken));
      setStep(S.done);
      setLog('');
      toast.success('Republished successfully');
    } catch {
      toast.error('Republish failed');
      setStep(S.error);
    }
  }, [adminToken, chapter.id]);

  const isDone  = step === S.done;
  const isError = step === S.error;

  return (
    <div
      className="rounded-xl border flex flex-col gap-3 p-4 transition-all"
      style={{
        background:   isDone  ? '#f0fdf4' : '#ffffff',
        borderColor:  isDone  ? '#86efac' : isError ? '#fca5a5' : '#e5e7eb',
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <BookOpen size={13} style={{ color: '#7c3aed', flexShrink: 0 }} />
          <span className="font-semibold text-sm text-gray-800 truncate">
            Ch {chapter.chapter_number}. {chapter.title}
          </span>
        </div>
        <StatusBadge step={step} />
      </div>

      {step !== S.idle && <PipelineBar step={step} />}

      {topics.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {topics.map((t, i) => (
            <span key={t.id || i}
              className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
              style={{ background: '#ede9fe', color: '#6d28d9' }}>
              {t.title}
            </span>
          ))}
        </div>
      )}

      <textarea
        value={input}
        onChange={e => setInput(e.target.value)}
        disabled={busy}
        placeholder={`Enter topic names — one per line\ne.g.\nPhotosynthesis\nCell Division\nOsmosis`}
        rows={4}
        className="w-full rounded-lg border text-sm p-2.5 resize-none font-mono focus:outline-none focus:ring-2 focus:ring-violet-300 disabled:opacity-40"
        style={{ borderColor: '#d1d5db', fontSize: '0.76rem', lineHeight: 1.6 }}
      />

      {log && (
        <p className="text-[11px] text-violet-500 flex items-center gap-1.5">
          <Loader2 size={10} className="animate-spin flex-shrink-0" /> {log}
        </p>
      )}

      <div className="flex gap-2 flex-wrap">
        <button
          onClick={run}
          disabled={busy || !input.trim()}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white disabled:opacity-40 transition-opacity"
          style={{ background: 'linear-gradient(135deg,#7c3aed,#6366f1)' }}
        >
          {busy ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />}
          Seed &amp; Generate &amp; Publish
        </button>

        {isDone && (
          <>
            <button
              onClick={regenAS}
              disabled={busy}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-semibold border disabled:opacity-40"
              style={{ borderColor: '#06b6d4', color: '#0891b2', background: '#ecfeff' }}
            >
              <Languages size={10} /> Regen AS
            </button>
            <button
              onClick={rePublish}
              disabled={busy}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-semibold border disabled:opacity-40"
              style={{ borderColor: '#8b5cf6', color: '#7c3aed', background: '#faf5ff' }}
            >
              <Cloud size={10} /> Republish
            </button>
          </>
        )}

        {isError && (
          <button
            onClick={() => { setStep(S.idle); setLog(''); }}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-semibold border"
            style={{ borderColor: '#d1d5db', color: '#6b7280' }}
          >
            <RotateCcw size={10} /> Reset
          </button>
        )}
      </div>
    </div>
  );
}

export default function AdminContentSeeder({ adminToken }) {
  const [subjects,    setSubjects]    = useState([]);
  const [selectedId,  setSelectedId]  = useState('');
  const [chapters,    setChapters]    = useState([]);
  const [loadingCh,   setLoadingCh]   = useState(false);
  const [loadingSubs, setLoadingSubs] = useState(true);

  useEffect(() => {
    axios.get(`${API}/admin/content/subjects`, authHeaders(adminToken))
      .then(r => setSubjects(Array.isArray(r.data) ? r.data : []))
      .catch(() => toast.error('Failed to load subjects'))
      .finally(() => setLoadingSubs(false));
  }, [adminToken]);

  const loadChapters = useCallback(async (subjectId) => {
    if (!subjectId) { setChapters([]); return; }
    setLoadingCh(true);
    try {
      const r = await axios.get(
        `${API}/admin/content/chapters?subject_id=${subjectId}`,
        authHeaders(adminToken),
      );
      const sorted = (r.data.chapters || [])
        .sort((a, b) => a.chapter_number - b.chapter_number)
        .slice(0, 4);
      setChapters(sorted);
    } catch {
      toast.error('Failed to load chapters');
    } finally {
      setLoadingCh(false);
    }
  }, [adminToken]);

  const handleSelect = (id) => {
    setSelectedId(id);
    setChapters([]);
    loadChapters(id);
  };

  const subject = subjects.find(s => s.id === selectedId);

  return (
    <div className="p-4 sm:p-6 max-w-4xl mx-auto space-y-5">

      <div className="space-y-0.5">
        <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
          <Zap size={14} style={{ color: '#7c3aed' }} /> Content Seeder
        </h2>
        <p className="text-xs text-gray-400">
          Select a subject → fill topic names for each of the first 4 chapters → click
          <strong> Seed &amp; Generate &amp; Publish</strong>. Each chapter runs the full
          pipeline: topics → English notes (Gemini) → Assamese (Sarvam AI) → HTML to
          Cloudflare + JSON indexed in Vertex AI Search for RAG.
        </p>
      </div>

      <div className="flex items-center gap-2 text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
        <span className="flex items-center gap-1"><Plus size={9} className="text-amber-400" /> Seed</span>
        <span>→</span>
        <span className="flex items-center gap-1"><Sparkles size={9} className="text-indigo-400" /> EN Notes</span>
        <span>→</span>
        <span className="flex items-center gap-1"><Languages size={9} className="text-cyan-400" /> Assamese</span>
        <span>→</span>
        <span className="flex items-center gap-1"><Cloud size={9} className="text-violet-400" /> CF HTML</span>
        <span>+</span>
        <span className="flex items-center gap-1"><Search size={9} className="text-violet-400" /> Vertex RAG</span>
      </div>

      <div className="space-y-1.5">
        <label className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">Content Card (Subject)</label>
        <div className="relative">
          <select
            value={selectedId}
            onChange={e => handleSelect(e.target.value)}
            disabled={loadingSubs}
            className="w-full appearance-none border rounded-xl px-4 py-2.5 pr-9 text-sm font-medium bg-white focus:outline-none focus:ring-2 focus:ring-violet-300 disabled:opacity-50"
            style={{ borderColor: '#d1d5db' }}
          >
            <option value="">{loadingSubs ? 'Loading…' : '— Select a subject —'}</option>
            {subjects.map(s => (
              <option key={s.id} value={s.id}>
                {s.board_name ? `[${s.board_name}] ` : ''}{s.name}{s.class_name ? ` · ${s.class_name}` : ''}
              </option>
            ))}
          </select>
          <ChevronDown size={13} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
        </div>
      </div>

      {selectedId && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
              First 4 Chapters{subject ? ` — ${subject.name}` : ''}
            </span>
            {!loadingCh && chapters.length > 0 && (
              <span className="text-[10px] text-gray-400">{chapters.length} chapter{chapters.length !== 1 ? 's' : ''}</span>
            )}
          </div>

          {loadingCh && (
            <div className="flex items-center gap-2 py-10 justify-center text-gray-400 text-sm">
              <Loader2 size={15} className="animate-spin" /> Loading chapters…
            </div>
          )}

          {!loadingCh && chapters.length === 0 && (
            <div className="text-center py-10 text-gray-400 text-sm border rounded-xl border-dashed">
              No chapters found. Create chapters first in the Content Editor tab.
            </div>
          )}

          {!loadingCh && chapters.length > 0 && (
            <div className="flex flex-col gap-3">
              {chapters.map((ch, idx) => (
                <div key={ch.id} className="flex items-start gap-3">
                  <div className="flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold mt-1"
                    style={{ background: '#ede9fe', color: '#7c3aed' }}>
                    {idx + 1}
                  </div>
                  <div className="flex-1 min-w-0">
                    <ChapterCard
                      chapter={ch}
                      adminToken={adminToken}
                      onDone={() => loadChapters(selectedId)}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {!selectedId && !loadingSubs && (
        <div className="rounded-xl border-2 border-dashed py-14 flex flex-col items-center gap-3 text-center"
          style={{ borderColor: '#e5e7eb' }}>
          <BookOpen size={30} style={{ color: '#c4b5fd' }} />
          <p className="text-sm font-medium text-gray-500">Pick a subject to start seeding</p>
          <p className="text-xs text-gray-400 max-w-xs leading-relaxed">
            You'll see 4 chapter cards. Enter topics per chapter, then one click seeds them,
            generates notes, translates to Assamese, publishes HTML to Cloudflare, and indexes
            JSON in Vertex AI Search for RAG.
          </p>
        </div>
      )}
    </div>
  );
}
