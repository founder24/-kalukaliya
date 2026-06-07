import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  BookOpen, Loader2, CheckCircle2, Circle, AlertCircle,
  ChevronDown, Sparkles, Languages, Plus, RotateCcw,
} from 'lucide-react';
import { API } from '@/utils/adminHelpers';
import { authHeaders } from '@/utils/adminHelpers';

function slugify(text) {
  return (text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
}

const STEP = { idle: 'idle', seeding: 'seeding', generating: 'generating', translating: 'translating', done: 'done', error: 'error' };

function StepBadge({ step }) {
  const map = {
    idle:        { color: '#94a3b8', icon: Circle,        label: 'Ready' },
    seeding:     { color: '#f59e0b', icon: Loader2,       label: 'Seeding topics…', spin: true },
    generating:  { color: '#6366f1', icon: Loader2,       label: 'Generating EN notes…', spin: true },
    translating: { color: '#06b6d4', icon: Loader2,       label: 'Translating to Assamese…', spin: true },
    done:        { color: '#10b981', icon: CheckCircle2,  label: 'Done' },
    error:       { color: '#ef4444', icon: AlertCircle,   label: 'Error' },
  };
  const { color, icon: Icon, label, spin } = map[step] || map.idle;
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full" style={{ background: color + '18', color }}>
      <Icon size={11} className={spin ? 'animate-spin' : ''} />
      {label}
    </span>
  );
}

function ChapterCard({ chapter, adminToken, onDone }) {
  const [topics, setTopics]   = useState(chapter.published_topics || []);
  const [input, setInput]     = useState('');
  const [step, setStep]       = useState(STEP.idle);
  const [log, setLog]         = useState('');

  const busy = [STEP.seeding, STEP.generating, STEP.translating].includes(step);

  const addLog = (msg) => setLog(msg);

  const seedTopics = useCallback(async () => {
    const lines = input.split('\n').map(l => l.trim()).filter(Boolean);
    if (!lines.length) { toast.error('Enter at least one topic name'); return; }

    setStep(STEP.seeding);
    const cfg = authHeaders(adminToken);
    const added = [];
    for (const title of lines) {
      try {
        addLog(`Adding: ${title}`);
        const r = await axios.post(
          `${API}/admin/content/chapters/${chapter.id}/topics`,
          { title, topic_slug: slugify(title) },
          cfg,
        );
        added.push(r.data);
      } catch (e) {
        toast.error(`Failed to add topic: ${title}`);
        setStep(STEP.error);
        return;
      }
    }
    setTopics(prev => [...prev, ...added]);
    setInput('');
    toast.success(`${added.length} topic${added.length > 1 ? 's' : ''} added`);

    setStep(STEP.generating);
    addLog('Generating English notes via Gemini…');
    try {
      await axios.post(`${API}/admin/content/chapters/${chapter.id}/generate-notes`, {}, cfg);
    } catch (e) {
      toast.error('Note generation failed — check Vertex AI credentials');
      setStep(STEP.error);
      return;
    }

    setStep(STEP.translating);
    addLog('Translating to Assamese via Sarvam AI…');
    try {
      await axios.post(`${API}/admin/content/chapters/${chapter.id}/generate-notes/as`, {}, cfg);
    } catch (e) {
      toast.error('Assamese translation failed — check Sarvam API key');
      setStep(STEP.error);
      return;
    }

    setStep(STEP.done);
    addLog('');
    toast.success(`Chapter "${chapter.title}" fully seeded!`);
    onDone?.();
  }, [input, adminToken, chapter.id, chapter.title, onDone]);

  const regenAssamese = useCallback(async () => {
    setStep(STEP.translating);
    addLog('Re-translating to Assamese via Sarvam AI…');
    try {
      await axios.post(`${API}/admin/content/chapters/${chapter.id}/generate-notes/as`, {}, authHeaders(adminToken));
      setStep(STEP.done);
      addLog('');
      toast.success('Assamese regenerated');
    } catch {
      toast.error('Assamese translation failed');
      setStep(STEP.error);
    }
  }, [adminToken, chapter.id]);

  return (
    <div
      className="rounded-xl border flex flex-col gap-3 p-4"
      style={{
        background: step === STEP.done ? '#f0fdf4' : '#ffffff',
        borderColor: step === STEP.done ? '#86efac' : step === STEP.error ? '#fca5a5' : '#e5e7eb',
        transition: 'border-color 0.3s',
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <BookOpen size={14} style={{ color: '#7c3aed', flexShrink: 0 }} />
          <span className="font-semibold text-sm text-gray-800 truncate">Ch {chapter.chapter_number}. {chapter.title}</span>
        </div>
        <StepBadge step={step} />
      </div>

      {topics.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {topics.map((t, i) => (
            <span key={t.id || i} className="text-xs px-2 py-0.5 rounded-full font-medium"
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
        placeholder={`Enter topic names — one per line\ne.g.\nPhotosynthesis\nCell Division\nGrowth and Repair`}
        rows={4}
        className="w-full rounded-lg border text-sm p-2.5 resize-none font-mono focus:outline-none focus:ring-2 disabled:opacity-50"
        style={{ borderColor: '#d1d5db', fontSize: '0.78rem', lineHeight: 1.6, focusRingColor: '#7c3aed' }}
      />

      {log && (
        <p className="text-xs text-indigo-500 flex items-center gap-1">
          <Loader2 size={11} className="animate-spin flex-shrink-0" /> {log}
        </p>
      )}

      <div className="flex gap-2 flex-wrap">
        <button
          onClick={seedTopics}
          disabled={busy || !input.trim()}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-white transition-opacity disabled:opacity-40"
          style={{ background: 'linear-gradient(135deg,#7c3aed,#6366f1)' }}
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
          Seed &amp; Generate
        </button>

        {step === STEP.done && (
          <button
            onClick={regenAssamese}
            disabled={busy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors disabled:opacity-40"
            style={{ borderColor: '#06b6d4', color: '#0891b2', background: '#ecfeff' }}
          >
            <Languages size={12} /> Re-translate AS
          </button>
        )}

        {step === STEP.error && (
          <button
            onClick={() => { setStep(STEP.idle); setLog(''); }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border"
            style={{ borderColor: '#d1d5db', color: '#6b7280' }}
          >
            <RotateCcw size={12} /> Reset
          </button>
        )}
      </div>
    </div>
  );
}

export default function AdminContentSeeder({ adminToken }) {
  const [subjects, setSubjects]   = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [chapters, setChapters]   = useState([]);
  const [loadingCh, setLoadingCh] = useState(false);
  const [loadingSubs, setLoadingSubs] = useState(true);

  useEffect(() => {
    const cfg = authHeaders(adminToken);
    axios.get(`${API}/admin/content/subjects`, cfg)
      .then(r => setSubjects(Array.isArray(r.data) ? r.data : []))
      .catch(() => toast.error('Failed to load subjects'))
      .finally(() => setLoadingSubs(false));
  }, [adminToken]);

  const loadChapters = useCallback(async (subjectId) => {
    if (!subjectId) { setChapters([]); return; }
    setLoadingCh(true);
    try {
      const cfg = authHeaders(adminToken);
      const r = await axios.get(`${API}/admin/content/chapters?subject_id=${subjectId}`, cfg);
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

  const handleSubjectChange = (id) => {
    setSelectedId(id);
    setChapters([]);
    loadChapters(id);
  };

  const subject = subjects.find(s => s.id === selectedId);

  return (
    <div className="p-4 sm:p-6 max-w-4xl mx-auto space-y-6">

      <div className="space-y-1">
        <h2 className="text-base font-bold text-gray-800 flex items-center gap-2">
          <Plus size={16} style={{ color: '#7c3aed' }} /> Content Seeder
        </h2>
        <p className="text-xs text-gray-500">
          Select a subject → enter topic names for each of its first 4 chapters → click <strong>Seed &amp; Generate</strong> to create topic-wise notes in English and Assamese.
        </p>
      </div>

      <div className="space-y-1.5">
        <label className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Content Card (Subject)</label>
        <div className="relative">
          <select
            value={selectedId}
            onChange={e => handleSubjectChange(e.target.value)}
            disabled={loadingSubs}
            className="w-full appearance-none border rounded-xl px-4 py-2.5 pr-9 text-sm font-medium bg-white focus:outline-none focus:ring-2 disabled:opacity-50"
            style={{ borderColor: '#d1d5db' }}
          >
            <option value="">{loadingSubs ? 'Loading subjects…' : '— Select a subject —'}</option>
            {subjects.map(s => (
              <option key={s.id} value={s.id}>
                {s.board_name ? `[${s.board_name}] ` : ''}{s.name}{s.class_name ? ` · ${s.class_name}` : ''}
              </option>
            ))}
          </select>
          <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
        </div>
      </div>

      {selectedId && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
              First 4 Chapters {subject ? `— ${subject.name}` : ''}
            </span>
            {!loadingCh && chapters.length > 0 && (
              <span className="text-xs text-gray-400">{chapters.length} chapter{chapters.length !== 1 ? 's' : ''} loaded</span>
            )}
          </div>

          {loadingCh && (
            <div className="flex items-center gap-2 py-8 justify-center text-gray-400 text-sm">
              <Loader2 size={16} className="animate-spin" /> Loading chapters…
            </div>
          )}

          {!loadingCh && chapters.length === 0 && (
            <div className="text-center py-10 text-gray-400 text-sm border rounded-xl" style={{ borderStyle: 'dashed' }}>
              No chapters found for this subject. Create chapters first in the Content Editor tab.
            </div>
          )}

          {!loadingCh && chapters.length > 0 && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {chapters.map(ch => (
                <ChapterCard
                  key={ch.id}
                  chapter={ch}
                  adminToken={adminToken}
                  onDone={() => loadChapters(selectedId)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {!selectedId && !loadingSubs && (
        <div className="rounded-xl border-2 py-14 flex flex-col items-center gap-3 text-center"
          style={{ borderColor: '#e5e7eb', borderStyle: 'dashed' }}>
          <BookOpen size={32} style={{ color: '#c4b5fd' }} />
          <p className="text-sm font-medium text-gray-500">Pick a subject above to start seeding</p>
          <p className="text-xs text-gray-400 max-w-xs">
            You'll see 4 chapter cards. Enter topic names (one per line) in each and click <strong>Seed &amp; Generate</strong>.
          </p>
        </div>
      )}
    </div>
  );
}
