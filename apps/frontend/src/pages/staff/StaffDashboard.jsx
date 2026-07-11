import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '@/context/AuthContext';
import { API_BASE } from '@/utils/api';
import { getToken } from '@/hooks/useTokenManager';
import axios from 'axios';
import { toast } from 'sonner';

const api = () => {
  const token = getToken();
  return axios.create({
    baseURL: API_BASE,
    withCredentials: true,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
};

// ── Status helpers ────────────────────────────────────────────────────────────

const STATUS_COLORS = {
  published: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  draft:     'bg-yellow-100 text-yellow-700 border-yellow-200',
  planned:   'bg-blue-100 text-blue-700 border-blue-200',
  archived:  'bg-gray-100 text-gray-500 border-gray-200',
  active:    'bg-emerald-100 text-emerald-700 border-emerald-200',
};
const statusLabel = (s) => s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Unknown';

function StatusBadge({ status }) {
  const cls = STATUS_COLORS[status] || STATUS_COLORS.planned;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${cls}`}>
      {statusLabel(status)}
    </span>
  );
}

function Spinner({ size = 5 }) {
  return (
    <div
      className={`w-${size} h-${size} border-2 rounded-full animate-spin flex-shrink-0`}
      style={{ borderColor: 'hsl(var(--primary))', borderTopColor: 'transparent' }}
    />
  );
}

// ── Field indicator dots ─────────────────────────────────────────────────────

function Dot({ filled, label }) {
  return (
    <span
      title={label}
      className={`inline-block w-2 h-2 rounded-full ${filled ? 'bg-emerald-400' : 'bg-gray-200'}`}
    />
  );
}

// ── Sidebar ───────────────────────────────────────────────────────────────────

function Sidebar({ user, onLogout, view, onViewChange, onChangePassword }) {
  return (
    <aside className="flex flex-col h-full bg-white border-r border-gray-100">
      <div className="flex items-center gap-3 px-5 py-5 border-b border-gray-100">
        <img src="/logo-144.webp" alt="" className="w-9 h-9 rounded-xl object-cover shadow" />
        <div>
          <div className="text-sm font-bold text-gray-900 leading-tight">Syrabit Staff</div>
          <div className="text-xs text-gray-400">Content Portal</div>
        </div>
      </div>
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        <SidebarLink active={view === 'subjects'} icon={<GridIcon />} label="Subjects" onClick={() => onViewChange('subjects')} />
      </nav>
      <div className="px-4 py-4 border-t border-gray-100">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-9 h-9 rounded-full bg-violet-100 flex items-center justify-center text-violet-700 font-bold text-sm select-none">
            {(user?.name || 'S').charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium text-gray-900 truncate">{user?.name}</div>
            <div className="text-xs text-gray-400 truncate">{user?.email}</div>
          </div>
        </div>
        <span className="inline-block mb-3 px-2 py-0.5 rounded-full text-xs font-semibold bg-violet-50 text-violet-700 border border-violet-200">Staff</span>
        <button onClick={onChangePassword} className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-violet-50 hover:text-violet-700 transition-colors mb-1">
          <KeyIcon /><span>Change password</span>
        </button>
        <button onClick={onLogout} className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-red-50 hover:text-red-600 transition-colors">
          <LogoutIcon /><span>Sign out</span>
        </button>
      </div>
    </aside>
  );
}

function SidebarLink({ active, icon, label, onClick }) {
  return (
    <button onClick={onClick} className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all ${active ? 'bg-violet-50 text-violet-700' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'}`}>
      <span className={active ? 'text-violet-600' : 'text-gray-400'}>{icon}</span>
      {label}
    </button>
  );
}

// ── Change password modal ─────────────────────────────────────────────────────

function ChangePasswordModal({ onClose }) {
  const [form, setForm] = useState({ current: '', next: '', confirm: '' });
  const [saving, setSaving] = useState(false);
  const [strength, setStrength] = useState(0);
  const set = (f) => (e) => {
    const val = e.target.value;
    setForm(p => ({ ...p, [f]: val }));
    if (f === 'next') {
      let s = 0;
      if (val.length >= 8) s++;
      if (/[A-Z]/.test(val)) s++;
      if (/[0-9]/.test(val)) s++;
      if (/[^A-Za-z0-9]/.test(val)) s++;
      setStrength(s);
    }
  };
  const strengthLabel = ['', 'Weak', 'Fair', 'Good', 'Strong'][strength];
  const strengthColor = ['', 'bg-red-400', 'bg-yellow-400', 'bg-blue-400', 'bg-emerald-400'][strength];

  const handleSave = async () => {
    if (form.next.length < 8) { toast.error('Password must be at least 8 characters'); return; }
    if (form.next !== form.confirm) { toast.error('Passwords do not match'); return; }
    setSaving(true);
    try {
      await api().post('/staff/auth/change-password', { current_password: form.current, new_password: form.next });
      toast.success('Password changed');
      onClose();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to change password');
    } finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.45)' }} onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-white w-full max-w-md rounded-2xl shadow-2xl flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h2 className="text-base font-bold text-gray-900">Change Password</h2>
          <button onClick={onClose} className="p-2 rounded-xl text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"><CloseIcon /></button>
        </div>
        <div className="px-5 py-4 space-y-4">
          {[['current', 'Current password'], ['next', 'New password'], ['confirm', 'Confirm new password']].map(([key, label]) => (
            <div key={key}>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">{label}</label>
              <input type="password" value={form[key]} onChange={set(key)} autoComplete="new-password" className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400" />
              {key === 'next' && form.next.length > 0 && (
                <div className="mt-2 flex items-center gap-2">
                  <div className="flex-1 h-1 rounded-full bg-gray-100 overflow-hidden"><div className={`h-full rounded-full transition-all ${strengthColor}`} style={{ width: `${strength * 25}%` }} /></div>
                  <span className="text-xs text-gray-400 w-10">{strengthLabel}</span>
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-gray-100 bg-gray-50">
          <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors">Cancel</button>
          <button onClick={handleSave} disabled={saving} className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-semibold text-white disabled:opacity-60" style={{ background: 'hsl(var(--primary))' }}>
            {saving && <Spinner size={4} />}{saving ? 'Saving…' : 'Change Password'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Chapter editor — dual-layer Notes / Questions / PYQ ──────────────────────

const EDITOR_TABS = [
  { id: 'info',      label: 'Info' },
  { id: 'notes',     label: 'Notes' },
  { id: 'questions', label: 'Questions' },
  { id: 'pyq',       label: 'PYQ' },
];

function FieldLabel({ children, chars }) {
  return (
    <div className="flex items-center justify-between mb-1.5">
      <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide">{children}</label>
      {chars !== undefined && <span className="text-[10px] text-gray-300 tabular-nums">{chars.toLocaleString()} chars</span>}
    </div>
  );
}

function BigTextarea({ value, onChange, placeholder, rows = 14, mono = false }) {
  return (
    <textarea
      value={value}
      onChange={onChange}
      rows={rows}
      placeholder={placeholder}
      spellCheck={false}
      className={`w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400 resize-y ${mono ? 'font-mono text-xs' : 'text-gray-900'}`}
    />
  );
}

// ── Sub-tab bar (Content / RAG) ───────────────────────────────────────────────

function SubTabs({ value, onChange, staleRag }) {
  return (
    <div className="flex gap-1.5 mb-4">
      {['content', 'rag'].map(id => (
        <button
          key={id}
          onClick={() => onChange(id)}
          className={`relative px-4 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
            value === id
              ? 'bg-violet-100 text-violet-700'
              : 'bg-gray-100 text-gray-500 hover:text-gray-700 hover:bg-gray-50'
          }`}
        >
          {id === 'content' ? 'Content' : 'RAG'}
          {id === 'rag' && staleRag && (
            <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-amber-400" title="RAG updated but not reindexed" />
          )}
        </button>
      ))}
    </div>
  );
}

// ── Reindex banner helper ─────────────────────────────────────────────────────

function ReindexBanner({ isStale, indexedAt, updatedAt, onReindex, loading, label }) {
  return isStale ? (
    <div className="flex items-center justify-between gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 mb-4">
      <div className="text-xs text-amber-700">
        <strong>RAG updated but not reindexed.</strong> The AI won't use the latest {label} until you reindex.
        {updatedAt && <span className="ml-1 opacity-70">Edited {new Date(updatedAt).toLocaleString()}</span>}
      </div>
      <button onClick={onReindex} disabled={loading}
        className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-amber-500 hover:bg-amber-600 text-white transition-colors disabled:opacity-50">
        {loading ? <Spinner size={3} /> : <IndexIcon />}
        {loading ? 'Indexing…' : 'Reindex now'}
      </button>
    </div>
  ) : (
    <div className="flex items-center justify-between gap-3 bg-blue-50 border border-blue-100 rounded-xl px-4 py-3 mb-4">
      <div className="text-xs text-blue-700">
        Plain-text the AI uses for retrieval — not shown to students. After editing, press Reindex to push to Vectorize.
        {indexedAt && <span className="ml-1 opacity-70">· Last indexed {new Date(indexedAt).toLocaleString()}</span>}
      </div>
      <button onClick={onReindex} disabled={loading}
        className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50">
        {loading ? <Spinner size={3} /> : <IndexIcon />}
        {loading ? 'Indexing…' : 'Reindex'}
      </button>
    </div>
  );
}

// ── Notes RAG section card ────────────────────────────────────────────────────

function NotesSectionCard({ section, index, total, onChange, onDelete, onMove }) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-3.5 space-y-2.5">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-bold text-gray-300 w-5 text-center select-none">{index + 1}</span>
        <input
          type="text"
          value={section.title || ''}
          onChange={e => onChange('title', e.target.value)}
          placeholder="Section title…"
          className="flex-1 px-3 py-2 border border-gray-200 rounded-lg text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-violet-400"
        />
        <div className="flex items-center gap-0.5 flex-shrink-0">
          <button onClick={() => onMove(-1)} disabled={index === 0} className="p-1.5 rounded text-gray-300 hover:text-gray-600 disabled:opacity-0 transition-colors" title="Move up"><ArrowUpIcon /></button>
          <button onClick={() => onMove(1)} disabled={index === total - 1} className="p-1.5 rounded text-gray-300 hover:text-gray-600 disabled:opacity-0 transition-colors" title="Move down"><ArrowDownIcon /></button>
          <button onClick={onDelete} className="p-1.5 rounded text-gray-300 hover:text-red-500 transition-colors" title="Delete section"><TrashIcon /></button>
        </div>
      </div>
      <textarea
        value={section.content || ''}
        onChange={e => onChange('content', e.target.value)}
        placeholder="Section content (plain text, no Markdown formatting)…"
        rows={4}
        className="w-full px-3 py-2 border border-gray-200 rounded-lg text-xs font-mono focus:outline-none focus:ring-2 focus:ring-violet-400 resize-y"
      />
    </div>
  );
}

// ── Q&A RAG section card ──────────────────────────────────────────────────────

function QaCard({ section, index, total, onChange, onDelete, onMove }) {
  const inputCls = 'w-full px-3 py-2 border border-gray-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-violet-400';
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-3.5 space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-bold text-gray-300 w-5 text-center select-none">Q{index + 1}</span>
        <input type="text" value={section.section || ''} onChange={e => onChange('section', e.target.value)}
          placeholder="Section / topic name (optional)…"
          className="flex-1 px-3 py-2 border border-gray-200 rounded-lg text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-violet-400" />
        <div className="flex items-center gap-0.5 flex-shrink-0">
          <button onClick={() => onMove(-1)} disabled={index === 0} className="p-1.5 rounded text-gray-300 hover:text-gray-600 disabled:opacity-0 transition-colors" title="Move up"><ArrowUpIcon /></button>
          <button onClick={() => onMove(1)} disabled={index === total - 1} className="p-1.5 rounded text-gray-300 hover:text-gray-600 disabled:opacity-0 transition-colors" title="Move down"><ArrowDownIcon /></button>
          <button onClick={onDelete} className="p-1.5 rounded text-gray-300 hover:text-red-500 transition-colors" title="Delete"><TrashIcon /></button>
        </div>
      </div>
      <div>
        <label className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide block mb-1">Question</label>
        <textarea value={section.question || ''} onChange={e => onChange('question', e.target.value)}
          placeholder="Question text…" rows={2} className={`${inputCls} resize-none`} />
      </div>
      <div>
        <label className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide block mb-1">Answer</label>
        <textarea value={section.answer || ''} onChange={e => onChange('answer', e.target.value)}
          placeholder="Answer text…" rows={2} className={`${inputCls} resize-none`} />
      </div>
      <div>
        <label className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide block mb-1">Solution / explanation (optional)</label>
        <textarea value={section.solution || ''} onChange={e => onChange('solution', e.target.value)}
          placeholder="Detailed explanation or solution…" rows={2} className={`${inputCls} resize-none`} />
      </div>
    </div>
  );
}

// ── PYQ upload area ───────────────────────────────────────────────────────────

function PyqUploadArea({ pyqPdfUrl, uploading, onPickFile }) {
  const isPdf  = pyqPdfUrl?.toLowerCase().includes('.pdf');
  const isImg  = pyqPdfUrl && !isPdf;
  return (
    <div className="space-y-3">
      {pyqPdfUrl ? (
        <>
          <div className="text-xs text-gray-500 flex items-center gap-2">
            <span className="text-emerald-600 font-semibold">✓ File uploaded</span>
            <a href={pyqPdfUrl} target="_blank" rel="noreferrer" className="text-violet-600 hover:underline truncate max-w-xs">{pyqPdfUrl.split('/').pop()}</a>
          </div>
          {isPdf && (
            <iframe src={pyqPdfUrl} className="w-full rounded-xl border border-gray-200" style={{ height: 420 }} title="PYQ PDF preview" />
          )}
          {isImg && (
            <img src={pyqPdfUrl} alt="PYQ" className="w-full rounded-xl border border-gray-200 object-contain max-h-96" />
          )}
          <button onClick={onPickFile} disabled={uploading}
            className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-semibold bg-gray-100 text-gray-600 hover:bg-gray-200 transition-colors disabled:opacity-50">
            {uploading ? <Spinner size={3} /> : <AttachIcon />}
            {uploading ? 'Uploading…' : 'Change file'}
          </button>
        </>
      ) : (
        <button onClick={onPickFile} disabled={uploading}
          className="w-full flex flex-col items-center justify-center gap-2 px-4 py-10 border-2 border-dashed border-gray-200 rounded-xl text-gray-400 hover:border-violet-300 hover:text-violet-500 transition-colors disabled:opacity-50">
          {uploading ? <Spinner size={6} /> : <UploadIcon />}
          <span className="text-sm font-medium">{uploading ? 'Uploading…' : 'Click to upload PDF or image'}</span>
          <span className="text-xs opacity-60">PDF, JPG, PNG, WEBP — max 25 MB</span>
        </button>
      )}
    </div>
  );
}

// ── Chapter editor ────────────────────────────────────────────────────────────

function ChapterEditor({ chapterId, subjectName, subjectContext, onClose, onSaved }) {
  const [form,      setForm]      = useState(null);
  const [loading,   setLoading]   = useState(true);
  const [saving,    setSaving]    = useState(false);
  const [reindexing,setReindexing]= useState({});  // { notes: bool, qa: bool, pyq: bool }
  const [tab,       setTab]       = useState('info');
  const [subTab,    setSubTab]    = useState({ notes: 'content', questions: 'content', pyq: 'content' });
  const [pyqUploading, setPyqUploading] = useState(false);
  const pyqFileRef = useRef(null);

  // Load full chapter content on open
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api().get(`/staff/content/chapter/${chapterId}`);
        if (!cancelled) setForm(res.data);
      } catch (err) {
        toast.error(err?.response?.data?.detail || 'Failed to load chapter');
        if (!cancelled) onClose();
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [chapterId]);

  const set    = (field) => (e) => setForm(f => ({ ...f, [field]: e.target.value }));
  const setNum = (field) => (e) => setForm(f => ({ ...f, [field]: parseInt(e.target.value, 10) || 0 }));

  const setSubTabFor = (mainTab, val) => setSubTab(s => ({ ...s, [mainTab]: val }));

  const handleSave = async () => {
    if (!form) return;
    setSaving(true);
    try {
      await api().patch(`/staff/content/chapter/${chapterId}`, form);
      toast.success('Chapter saved');
      onSaved(form);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Save failed');
    } finally { setSaving(false); }
  };

  const handleReindex = async (scope) => {
    setReindexing(r => ({ ...r, [scope]: true }));
    try {
      await api().post(`/staff/content/chapter/${chapterId}/reindex?scope=${scope}`);
      toast.success(`Reindex started (${scope})`);
      const updated = await api().get(`/staff/content/chapter/${chapterId}`);
      setForm(updated.data);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Reindex failed');
    } finally { setReindexing(r => ({ ...r, [scope]: false })); }
  };

  const handleUploadPyq = async () => {
    if (!pyqFileRef.current) return;
    pyqFileRef.current.onchange = async () => {
      const file = pyqFileRef.current?.files?.[0];
      if (!file) return;
      setPyqUploading(true);
      try {
        const fd = new FormData();
        fd.append('file', file);
        const res = await api().post(`/staff/content/chapter/${chapterId}/upload-pyq`, fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        setForm(f => ({ ...f, pyq_pdf_url: res.data.pyq_pdf_url }));
        toast.success('PYQ file uploaded');
      } catch (err) {
        toast.error(err?.response?.data?.detail || 'Upload failed');
      } finally {
        setPyqUploading(false);
        if (pyqFileRef.current) pyqFileRef.current.value = '';
      }
    };
    pyqFileRef.current.click();
  };

  // ── Section helpers ──────────────────────────────────────────────────────

  const addSection = (fieldKey, blank) => setForm(f => ({
    ...f, [fieldKey]: [...(f[fieldKey] || []), blank],
  }));

  const updateSection = (fieldKey, idx, key, val) => setForm(f => {
    const arr = [...(f[fieldKey] || [])];
    arr[idx] = { ...arr[idx], [key]: val };
    return { ...f, [fieldKey]: arr };
  });

  const deleteSection = (fieldKey, idx) => setForm(f => {
    const arr = [...(f[fieldKey] || [])];
    arr.splice(idx, 1);
    return { ...f, [fieldKey]: arr };
  });

  const moveSection = (fieldKey, idx, dir) => setForm(f => {
    const arr = [...(f[fieldKey] || [])];
    const t = idx + dir;
    if (t < 0 || t >= arr.length) return f;
    [arr[idx], arr[t]] = [arr[t], arr[idx]];
    return { ...f, [fieldKey]: arr };
  });

  // ── Lang picker for section lists ────────────────────────────────────────

  const [notesLang, setNotesLang] = useState('en');
  const [qaLang,    setQaLang]    = useState('en');

  const inputCls = 'w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400';

  if (loading) return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.45)' }}>
      <div className="bg-white rounded-2xl p-8 flex items-center gap-3 shadow-2xl">
        <Spinner /><span className="text-sm text-gray-500">Loading chapter…</span>
      </div>
    </div>
  );

  // Stale flags derived from form
  const notesRagStale = form?.notes_rag_stale;
  const qaRagStale    = form?.qa_rag_stale;
  const pyqRagStale   = form?.pyq_rag_stale;

  // Notes RAG section key based on selected lang
  const notesSecKey = notesLang === 'en' ? 'rag_sections_en' : 'rag_sections_as';
  const qaSecKey    = qaLang    === 'en' ? 'qa_rag_sections_en' : 'qa_rag_sections_as';

  return (
    <div className="fixed inset-0 z-50 flex items-stretch justify-center" style={{ background: 'rgba(0,0,0,0.55)' }} onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-white w-full max-w-4xl flex flex-col overflow-hidden sm:my-4 sm:rounded-2xl shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0 bg-white">
          <div className="min-w-0">
            <div className="flex items-center gap-1 flex-wrap text-[10px] text-gray-400 mb-0.5">
              {subjectContext?.board  && <><span>{subjectContext.board}</span><span className="text-gray-300">›</span></>}
              {subjectContext?.cls    && <><span>{subjectContext.cls}</span><span className="text-gray-300">›</span></>}
              {subjectContext?.course && <><span className="text-violet-400">{subjectContext.course}</span><span className="text-gray-300">›</span></>}
              <span>{subjectName}</span>
            </div>
            <h2 className="text-sm font-bold text-gray-900 truncate">Ch. {form?.chapter_number} · {form?.title}</h2>
          </div>
          <div className="flex items-center gap-2 ml-4 flex-shrink-0">
            <StatusBadge status={form?.status} />
            <button onClick={onClose} className="p-2 rounded-xl text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"><CloseIcon /></button>
          </div>
        </div>

        {/* Main tabs */}
        <div className="flex gap-1 px-4 pt-2 pb-0 border-b border-gray-100 flex-shrink-0 bg-white overflow-x-auto">
          {EDITOR_TABS.map(t => {
            const hasStale = (t.id === 'notes' && notesRagStale) || (t.id === 'questions' && qaRagStale) || (t.id === 'pyq' && pyqRagStale);
            return (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={`relative px-3 py-2 text-xs font-semibold rounded-t-lg transition-colors whitespace-nowrap border-b-2 ${tab === t.id ? 'border-violet-500 text-violet-700 bg-violet-50' : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}>
                {t.label}
                {hasStale && (
                  <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-amber-400" title="RAG updated but not reindexed" />
                )}
              </button>
            );
          })}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-5">

          {/* ── INFO TAB ── */}
          {tab === 'info' && (
            <div className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <FieldLabel>Title (English)</FieldLabel>
                  <input type="text" value={form?.title || ''} onChange={set('title')} className={inputCls} placeholder="Chapter title" />
                </div>
                <div>
                  <FieldLabel>Title (Assamese)</FieldLabel>
                  <input type="text" value={form?.title_as || ''} onChange={set('title_as')} className={inputCls} placeholder="অধ্যায়ৰ শিৰোনাম" />
                </div>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <div>
                  <FieldLabel>Chapter #</FieldLabel>
                  <input type="number" min="0" value={form?.chapter_number ?? ''} onChange={setNum('chapter_number')} className={inputCls} placeholder="1" />
                </div>
                <div className="sm:col-span-3">
                  <FieldLabel>Slug (URL identifier)</FieldLabel>
                  <input type="text" value={form?.slug || ''} onChange={set('slug')} className={inputCls} placeholder="chapter-slug" />
                </div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <FieldLabel>Status</FieldLabel>
                  <select value={form?.status || 'draft'} onChange={set('status')} className={`${inputCls} bg-white`}>
                    <option value="planned">Planned</option>
                    <option value="draft">Draft</option>
                    <option value="published">Published</option>
                    <option value="archived">Archived</option>
                  </select>
                </div>
                <div>
                  <FieldLabel>Content type</FieldLabel>
                  <select value={form?.content_type || 'notes'} onChange={set('content_type')} className={`${inputCls} bg-white`}>
                    <option value="notes">Notes</option>
                    <option value="qa">Q&amp;A</option>
                    <option value="question_paper">Question Paper</option>
                    <option value="formula">Formula</option>
                    <option value="summary">Summary</option>
                    <option value="solution">Solution</option>
                    <option value="reference">Reference</option>
                  </select>
                </div>
              </div>
              <div>
                <FieldLabel>Meta description</FieldLabel>
                <textarea value={form?.meta_description || ''} onChange={set('meta_description')} rows={2} className={`${inputCls} resize-none`} placeholder="SEO meta description (max 160 chars)" />
              </div>
              <div>
                <FieldLabel>Keywords</FieldLabel>
                <input type="text" value={form?.keywords || ''} onChange={set('keywords')} className={inputCls} placeholder="comma, separated, keywords" />
              </div>
              {/* Content presence summary */}
              <div className="bg-gray-50 rounded-xl p-3 border border-gray-100">
                <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Content presence</div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                  {[
                    ['notes_en',          'Notes EN'],
                    ['notes_as',          'Notes AS'],
                    ['qa_text_en',        'Q&A EN'],
                    ['qa_text_as',        'Q&A AS'],
                    ['pyq_pdf_url',       'PYQ file'],
                    ['pyq_rag_text',      'PYQ RAG'],
                    ['rag_text_en',       'RAG EN'],
                    ['rag_text_as',       'RAG AS'],
                  ].map(([field, label]) => (
                    <div key={field} className="flex items-center gap-1.5">
                      <Dot filled={!!(field === 'rag_sections_en' || field === 'qa_rag_sections_en'
                        ? (form?.[field]?.length > 0)
                        : form?.[field]?.trim?.()
                      )} label={label} />
                      <span className={form?.[field] ? 'text-gray-700' : 'text-gray-400'}>{label}</span>
                    </div>
                  ))}
                </div>
                {/* Per-section RAG sync summary */}
                <div className="mt-3 pt-2.5 border-t border-gray-100 grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px]">
                  {[
                    { label: 'Notes RAG', stale: notesRagStale, at: form?.notes_rag_indexed_at },
                    { label: 'Q&A RAG',   stale: qaRagStale,    at: form?.qa_rag_indexed_at },
                    { label: 'PYQ RAG',   stale: pyqRagStale,   at: form?.pyq_rag_indexed_at },
                  ].map(({ label, stale, at }) => (
                    <div key={label} className="flex items-center gap-1.5">
                      <span className="text-gray-400 font-semibold">{label}</span>
                      {at
                        ? <span className={`flex items-center gap-1 ${stale ? 'text-amber-600' : 'text-emerald-600'}`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${stale ? 'bg-amber-400' : 'bg-emerald-400'}`} />
                            {stale ? 'Stale' : 'Indexed'}
                          </span>
                        : <span className="text-gray-400">—</span>
                      }
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* ── NOTES TAB ── */}
          {tab === 'notes' && (
            <div>
              <SubTabs value={subTab.notes} onChange={v => setSubTabFor('notes', v)} staleRag={notesRagStale} />

              {subTab.notes === 'content' && (
                <div className="space-y-4">
                  <div className="bg-blue-50 border border-blue-100 rounded-xl px-4 py-2.5 text-xs text-blue-700">
                    <strong>Content layer</strong> — the study notes students read on the library page. Use Markdown.
                  </div>
                  <div>
                    <FieldLabel chars={form?.notes_en?.length || 0}>Notes — English (Markdown)</FieldLabel>
                    <BigTextarea value={form?.notes_en || ''} onChange={set('notes_en')} placeholder="## Introduction\n\nStudy notes in English…" rows={16} mono />
                  </div>
                  <div>
                    <FieldLabel chars={form?.notes_as?.length || 0}>Notes — Assamese (Markdown)</FieldLabel>
                    <BigTextarea value={form?.notes_as || ''} onChange={set('notes_as')} placeholder="## পৰিচয়\n\nঅসমীয়া ভাষাত টোকা…" rows={12} />
                  </div>
                </div>
              )}

              {subTab.notes === 'rag' && (
                <div className="space-y-3">
                  <ReindexBanner
                    isStale={notesRagStale}
                    indexedAt={form?.notes_rag_indexed_at}
                    updatedAt={form?.notes_rag_updated_at}
                    onReindex={() => handleReindex('notes')}
                    loading={reindexing.notes}
                    label="notes"
                  />
                  <div className="bg-violet-50 border border-violet-100 rounded-xl px-4 py-2.5 text-xs text-violet-700">
                    <strong>RAG layer</strong> — topic sections the AI uses for retrieval. Each section becomes its own vector chunk. Not shown to students.
                  </div>

                  {/* Lang toggle */}
                  <div className="flex gap-1">
                    {['en', 'as'].map(l => (
                      <button key={l} onClick={() => setNotesLang(l)}
                        className={`px-3 py-1 rounded-lg text-xs font-semibold transition-colors ${notesLang === l ? 'bg-violet-100 text-violet-700' : 'bg-gray-100 text-gray-500 hover:text-gray-700'}`}>
                        {l === 'en' ? 'English' : 'Assamese'}
                        {' '}
                        <span className="opacity-60">({(form?.[l === 'en' ? 'rag_sections_en' : 'rag_sections_as'] || []).length})</span>
                      </button>
                    ))}
                  </div>

                  {/* Section cards */}
                  <div className="space-y-2.5">
                    {(form?.[notesSecKey] || []).map((sec, idx) => (
                      <NotesSectionCard
                        key={idx}
                        section={sec}
                        index={idx}
                        total={(form?.[notesSecKey] || []).length}
                        onChange={(key, val) => updateSection(notesSecKey, idx, key, val)}
                        onDelete={() => deleteSection(notesSecKey, idx)}
                        onMove={(dir) => moveSection(notesSecKey, idx, dir)}
                      />
                    ))}
                  </div>

                  <button onClick={() => addSection(notesSecKey, { title: '', content: '' })}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold text-violet-700 bg-violet-50 hover:bg-violet-100 border border-violet-200 transition-colors">
                    <PlusIcon />+ Add section
                  </button>

                  {/* Legacy blob fallback */}
                  <details className="mt-2">
                    <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600 select-none">Legacy RAG text blob (fallback)</summary>
                    <div className="mt-2 space-y-2">
                      <BigTextarea value={form?.rag_text_en || ''} onChange={set('rag_text_en')} placeholder="Full plain-text content for AI retrieval (English)…" rows={6} mono />
                      <BigTextarea value={form?.rag_text_as || ''} onChange={set('rag_text_as')} placeholder="সম্পূৰ্ণ সাদা-পাঠ্য বিষয়বস্তু (অসমীয়া)…" rows={6} />
                    </div>
                  </details>
                </div>
              )}
            </div>
          )}

          {/* ── QUESTIONS TAB ── */}
          {tab === 'questions' && (
            <div>
              <SubTabs value={subTab.questions} onChange={v => setSubTabFor('questions', v)} staleRag={qaRagStale} />

              {subTab.questions === 'content' && (
                <div className="space-y-4">
                  <div className="bg-emerald-50 border border-emerald-100 rounded-xl px-4 py-2.5 text-xs text-emerald-700">
                    <strong>Content layer</strong> — the Q&amp;A bank shown to students on the library page. Use Markdown.
                  </div>
                  <div>
                    <FieldLabel chars={form?.qa_text_en?.length || 0}>Q&amp;A — English (Markdown)</FieldLabel>
                    <BigTextarea value={form?.qa_text_en || ''} onChange={set('qa_text_en')} placeholder={'## Q1. What is…\n**Answer:** …\n\n## Q2. …'} rows={16} mono />
                  </div>
                  <div>
                    <FieldLabel chars={form?.qa_text_as?.length || 0}>Q&amp;A — Assamese (Markdown)</FieldLabel>
                    <BigTextarea value={form?.qa_text_as || ''} onChange={set('qa_text_as')} placeholder="অসমীয়া ভাষাত প্ৰশ্ন-উত্তৰ…" rows={12} />
                  </div>
                </div>
              )}

              {subTab.questions === 'rag' && (
                <div className="space-y-3">
                  <ReindexBanner
                    isStale={qaRagStale}
                    indexedAt={form?.qa_rag_indexed_at}
                    updatedAt={form?.qa_rag_updated_at}
                    onReindex={() => handleReindex('qa')}
                    loading={reindexing.qa}
                    label="Q&A"
                  />
                  <div className="bg-violet-50 border border-violet-100 rounded-xl px-4 py-2.5 text-xs text-violet-700">
                    <strong>RAG layer</strong> — Q&amp;A cards the AI uses when a student asks a question. Each card becomes its own vector chunk. Not shown to students.
                  </div>

                  {/* Lang toggle */}
                  <div className="flex gap-1">
                    {['en', 'as'].map(l => (
                      <button key={l} onClick={() => setQaLang(l)}
                        className={`px-3 py-1 rounded-lg text-xs font-semibold transition-colors ${qaLang === l ? 'bg-violet-100 text-violet-700' : 'bg-gray-100 text-gray-500 hover:text-gray-700'}`}>
                        {l === 'en' ? 'English' : 'Assamese'}
                        {' '}
                        <span className="opacity-60">({(form?.[l === 'en' ? 'qa_rag_sections_en' : 'qa_rag_sections_as'] || []).length})</span>
                      </button>
                    ))}
                  </div>

                  {/* Q&A cards */}
                  <div className="space-y-2.5">
                    {(form?.[qaSecKey] || []).map((sec, idx) => (
                      <QaCard
                        key={idx}
                        section={sec}
                        index={idx}
                        total={(form?.[qaSecKey] || []).length}
                        onChange={(key, val) => updateSection(qaSecKey, idx, key, val)}
                        onDelete={() => deleteSection(qaSecKey, idx)}
                        onMove={(dir) => moveSection(qaSecKey, idx, dir)}
                      />
                    ))}
                  </div>

                  <button onClick={() => addSection(qaSecKey, { section: '', question: '', answer: '', solution: '' })}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold text-violet-700 bg-violet-50 hover:bg-violet-100 border border-violet-200 transition-colors">
                    <PlusIcon />+ Add question
                  </button>

                  {/* Legacy blob fallback */}
                  <details className="mt-2">
                    <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600 select-none">Legacy Q&amp;A RAG text blob (fallback)</summary>
                    <div className="mt-2 space-y-2">
                      <BigTextarea value={form?.qa_rag_text_en || ''} onChange={set('qa_rag_text_en')} placeholder="Expanded Q&A pairs for AI retrieval (English)…" rows={6} mono />
                      <BigTextarea value={form?.qa_rag_text_as || ''} onChange={set('qa_rag_text_as')} placeholder="বিস্তাৰিত প্ৰশ্ন-উত্তৰ (অসমীয়া)…" rows={6} />
                    </div>
                  </details>
                </div>
              )}
            </div>
          )}

          {/* ── PYQ TAB ── */}
          {tab === 'pyq' && (
            <div>
              <input ref={pyqFileRef} type="file" accept=".pdf,.jpg,.jpeg,.png,.webp,.gif" className="hidden" />
              <SubTabs value={subTab.pyq} onChange={v => setSubTabFor('pyq', v)} staleRag={pyqRagStale} />

              {subTab.pyq === 'content' && (
                <div className="space-y-3">
                  <div className="bg-blue-50 border border-blue-100 rounded-xl px-4 py-2.5 text-xs text-blue-700">
                    <strong>Content layer</strong> — the PDF or image shown inline to students on the library page PYQ tab. Stored in Cloudflare R2.
                  </div>
                  <PyqUploadArea
                    pyqPdfUrl={form?.pyq_pdf_url}
                    uploading={pyqUploading}
                    onPickFile={handleUploadPyq}
                  />
                </div>
              )}

              {subTab.pyq === 'rag' && (
                <div className="space-y-3">
                  <ReindexBanner
                    isStale={pyqRagStale}
                    indexedAt={form?.pyq_rag_indexed_at}
                    updatedAt={form?.pyq_rag_updated_at}
                    onReindex={() => handleReindex('pyq')}
                    loading={reindexing.pyq}
                    label="PYQ"
                  />
                  <div className="bg-violet-50 border border-violet-100 rounded-xl px-4 py-2.5 text-xs text-violet-700">
                    <strong>RAG layer</strong> — the full question paper as plain text. When a student asks "give me the question paper" in chat, the AI retrieves and delivers this text verbatim. Not shown to students as a formatted document.
                  </div>
                  <div>
                    <FieldLabel chars={form?.pyq_rag_text?.length || 0}>PYQ plain text (for AI retrieval)</FieldLabel>
                    <BigTextarea value={form?.pyq_rag_text || ''} onChange={set('pyq_rag_text')} placeholder="Paste the full question paper text here — all questions, options, and answers…" rows={18} mono />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-t border-gray-100 flex-shrink-0 bg-gray-50">
          <div className="text-[11px] text-gray-400 space-x-2">
            {form?.content_saved_at && <span>Saved {new Date(form.content_saved_at).toLocaleString()}</span>}
            {form?.word_count > 0 && <span>· {form.word_count.toLocaleString()} words</span>}
          </div>
          <div className="flex items-center gap-2">
            <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors">Cancel</button>
            <button onClick={handleSave} disabled={saving} className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-semibold text-white transition-colors disabled:opacity-60" style={{ background: 'hsl(var(--primary))' }}>
              {saving && <Spinner size={4} />}{saving ? 'Saving…' : 'Save Chapter'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Chapters view ─────────────────────────────────────────────────────────────

function ChaptersView({ subject, subjectContext, chapters, loadingChapters, onBack, onEditChapter }) {
  const [search, setSearch] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [filterType, setFilterType] = useState('');

  const filtered = chapters.filter(c => {
    const matchSearch = !search       || c.title?.toLowerCase().includes(search.toLowerCase());
    const matchStatus = !filterStatus || c.status === filterStatus;
    const matchType   = !filterType   || c.content_type === filterType;
    return matchSearch && matchStatus && matchType;
  });

  // Content coverage stats
  const stats = {
    total:      chapters.length,
    contentEN:  chapters.filter(c => c.has_content_en).length,
    contentAS:  chapters.filter(c => c.has_content_as).length,
    rag:        chapters.filter(c => c.has_rag_en).length,
    qa:         chapters.filter(c => c.has_qa_en).length,
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header with full hierarchy breadcrumb */}
      <div className="px-4 sm:px-6 py-4 border-b border-gray-100 bg-white flex-shrink-0">
        <div className="flex items-start gap-3">
          <button onClick={onBack} className="mt-0.5 p-2 rounded-xl text-gray-500 hover:bg-gray-100 transition-colors flex-shrink-0"><BackIcon /></button>
          <div className="min-w-0 flex-1">
            {/* Board → Class → Course → Subject breadcrumb */}
            <div className="flex items-center gap-1 flex-wrap text-[10px] text-gray-400 mb-0.5">
              {subjectContext?.board  && <span>{subjectContext.board}</span>}
              {subjectContext?.board  && <span className="text-gray-300">›</span>}
              {subjectContext?.cls    && <span>{subjectContext.cls}</span>}
              {subjectContext?.cls    && <span className="text-gray-300">›</span>}
              {subjectContext?.course && <span className="text-violet-500 font-medium">{subjectContext.course}</span>}
              {subjectContext?.course && <span className="text-gray-300">›</span>}
              <span className="text-gray-500 font-medium">{subject.name}</span>
            </div>
            <h1 className="text-base font-bold text-gray-900">Chapters</h1>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <StatusBadge status={subject.status} />
            <span className="text-xs text-gray-400">{stats.total} ch.</span>
          </div>
        </div>

        {/* Coverage mini-bar */}
        {stats.total > 0 && (
          <div className="mt-3 ml-11 flex items-center gap-4 text-[10px] text-gray-400">
            <span><span className="text-emerald-600 font-semibold">{stats.contentEN}</span>/{stats.total} Content EN</span>
            <span><span className="text-blue-500 font-semibold">{stats.contentAS}</span>/{stats.total} Content AS</span>
            <span><span className="text-violet-500 font-semibold">{stats.rag}</span>/{stats.total} RAG</span>
            <span><span className="text-amber-500 font-semibold">{stats.qa}</span>/{stats.total} Q&A</span>
          </div>
        )}
      </div>

      <div className="flex gap-2 px-4 sm:px-6 py-3 border-b border-gray-100 bg-white flex-shrink-0 flex-wrap">
        <input type="search" placeholder="Search chapters…" value={search} onChange={e => setSearch(e.target.value)} className="flex-1 min-w-[140px] px-3 py-2 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400" />
        <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} className="px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400">
          <option value="">All Status</option>
          <option value="published">Published</option>
          <option value="draft">Draft</option>
          <option value="planned">Planned</option>
          <option value="archived">Archived</option>
        </select>
        <select value={filterType} onChange={e => setFilterType(e.target.value)} className="px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400">
          <option value="">All Types</option>
          <option value="notes">Notes</option>
          <option value="qa">Q&A</option>
          <option value="question_paper">Question Paper</option>
          <option value="formula">Formula</option>
          <option value="summary">Summary</option>
          <option value="solution">Solution</option>
          <option value="reference">Reference</option>
        </select>
      </div>

      <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-4">
        {loadingChapters ? (
          <div className="flex justify-center py-16"><Spinner /></div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-16 text-gray-400 text-sm">
            {chapters.length === 0 ? 'No chapters in this subject.' : 'No chapters match the filter.'}
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((ch, idx) => {
              const hasUnpublishedEdit = !!(ch.content_saved_at && ch.published_at && new Date(ch.content_saved_at) > new Date(ch.published_at));
              return (
                <div key={ch.id} className="flex items-center gap-3 p-3.5 bg-white rounded-xl border hover:border-violet-200 hover:shadow-sm transition-all"
                  style={{ borderColor: hasUnpublishedEdit ? 'rgba(245,158,11,0.4)' : '#e5e7eb' }}>
                  <div className="w-8 h-8 rounded-lg bg-gray-50 border border-gray-100 flex items-center justify-center text-xs font-bold text-gray-400 flex-shrink-0">
                    {ch.chapter_number ?? idx + 1}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-gray-900 truncate">{ch.title}</span>
                      {ch.content_type && ch.content_type !== 'notes' && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 font-medium border border-blue-100">{ch.content_type}</span>
                      )}
                      {hasUnpublishedEdit && <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-semibold border border-amber-200">Unsaved</span>}
                    </div>
                    <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                      {/* Content dots: EN content, AS content, Notes EN, Q&A EN, Q&A AS, RAG EN, RAG AS */}
                      <div className="flex items-center gap-1" title="Content EN · Content AS · Notes EN · Q&A EN · Q&A AS · RAG EN · RAG AS">
                        <Dot filled={ch.has_content_en} label="Content EN" />
                        <Dot filled={ch.has_content_as} label="Content AS" />
                        <Dot filled={ch.has_notes_en}   label="Notes EN" />
                        <Dot filled={ch.has_qa_en}      label="Q&A EN" />
                        <Dot filled={ch.has_qa_as}      label="Q&A AS" />
                        <Dot filled={ch.has_rag_en}     label="RAG EN" />
                        <Dot filled={ch.has_rag_as}     label="RAG AS" />
                      </div>
                      {ch.word_count > 0 && <span className="text-[10px] text-gray-400">{ch.word_count.toLocaleString()} words</span>}
                    </div>
                  </div>
                  <StatusBadge status={ch.status} />
                  <button onClick={() => onEditChapter(ch.id)} className="flex-shrink-0 flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold text-violet-700 bg-violet-50 hover:bg-violet-100 transition-colors">
                    <EditIcon /><span>Edit</span>
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Subjects view ─────────────────────────────────────────────────────────────

function SubjectsView({ subjects, boards, classes, streams, loading, onSelectSubject }) {
  const [search,       setSearch]       = useState('');
  const [filterBoard,  setFilterBoard]  = useState('');
  const [filterClass,  setFilterClass]  = useState('');
  const [filterStream, setFilterStream] = useState('');
  const [filterStatus, setFilterStatus] = useState('');

  // Cascade: classes visible under the selected board
  const visibleClasses = filterBoard
    ? classes.filter(c => c.board_id === filterBoard)
    : classes;

  // Cascade: streams visible under selected board+class
  const visibleStreams = streams.filter(s => {
    if (filterClass)  return s.class_id === filterClass;
    if (filterBoard)  return s.board_id  === filterBoard;
    return true;
  });

  // Reset child selections when parent changes
  const handleBoardChange = (val) => {
    setFilterBoard(val);
    setFilterClass('');
    setFilterStream('');
  };
  const handleClassChange = (val) => {
    setFilterClass(val);
    setFilterStream('');
  };

  const filtered = subjects.filter(s => {
    const matchSearch = !search       || s.name?.toLowerCase().includes(search.toLowerCase());
    const matchBoard  = !filterBoard  || s.board_id   === filterBoard;
    const matchClass  = !filterClass  || s.class_id   === filterClass;
    const matchStream = !filterStream || s.stream_id  === filterStream;
    const matchStatus = !filterStatus || s.status     === filterStatus;
    return matchSearch && matchBoard && matchClass && matchStream && matchStatus;
  });

  const published = subjects.filter(s => s.status === 'published').length;
  const drafted   = subjects.filter(s => s.status === 'draft').length;

  const selectCls = 'px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400';

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 sm:px-6 py-4 border-b border-gray-100 bg-white flex-shrink-0">
        <h1 className="text-lg font-bold text-gray-900">Subjects</h1>
        <p className="text-xs text-gray-400 mt-0.5">{subjects.length} total · {published} published · {drafted} drafts</p>
      </div>

      {/* Filter bar — Board → Class → Course → Status */}
      <div className="px-4 sm:px-6 py-3 border-b border-gray-100 bg-white flex-shrink-0">
        <div className="flex flex-wrap gap-2">
          <input
            type="search" placeholder="Search subjects…" value={search}
            onChange={e => setSearch(e.target.value)}
            className="flex-1 min-w-[140px] px-3 py-2 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400"
          />
          {/* Board */}
          <select value={filterBoard} onChange={e => handleBoardChange(e.target.value)} className={selectCls}>
            <option value="">All Boards</option>
            {boards.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          {/* Class — cascades from Board */}
          <select value={filterClass} onChange={e => handleClassChange(e.target.value)} className={selectCls}>
            <option value="">All Classes</option>
            {visibleClasses.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          {/* Course/Stream — cascades from Board+Class */}
          <select value={filterStream} onChange={e => setFilterStream(e.target.value)} className={selectCls}>
            <option value="">All Courses</option>
            {visibleStreams.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          {/* Status */}
          <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} className={selectCls}>
            <option value="">All Status</option>
            <option value="published">Published</option>
            <option value="draft">Draft</option>
            <option value="planned">Planned</option>
            <option value="archived">Archived</option>
          </select>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-4">
        {loading ? (
          <div className="flex justify-center py-20"><Spinner /></div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20 text-gray-400 text-sm">
            {subjects.length === 0 ? 'No subjects found.' : 'No subjects match the filter.'}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {filtered.map(subj => (
              <SubjectCard
                key={subj.id}
                subject={subj}
                boards={boards}
                classes={classes}
                onClick={() => onSelectSubject(subj)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SubjectCard({ subject, boards, classes, onClick }) {
  const board = boards.find(b => b.id === subject.board_id);
  const cls   = classes.find(c => c.id === subject.class_id);
  // stream_name is resolved server-side and returned on the subject object
  const courseName = subject.stream_name || null;
  return (
    <button onClick={onClick} className="text-left p-4 bg-white rounded-2xl border border-gray-100 hover:border-violet-200 hover:shadow-md transition-all group">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-50 to-violet-100 flex items-center justify-center text-violet-600 flex-shrink-0"><BookIcon /></div>
        <StatusBadge status={subject.status} />
      </div>
      <div className="font-semibold text-gray-900 text-sm leading-snug group-hover:text-violet-700 transition-colors line-clamp-2 mb-2">{subject.name}</div>
      {/* Hierarchy breadcrumb: Board → Class → Course */}
      <div className="flex items-center gap-1 flex-wrap text-[10px] text-gray-400">
        {board && <span className="px-1.5 py-0.5 rounded bg-gray-50 border border-gray-100">{board.name}</span>}
        {(board && (cls || courseName)) && <span className="text-gray-300">›</span>}
        {cls   && <span className="px-1.5 py-0.5 rounded bg-gray-50 border border-gray-100">{cls.name}</span>}
        {(cls && courseName) && <span className="text-gray-300">›</span>}
        {courseName && <span className="px-1.5 py-0.5 rounded bg-violet-50 border border-violet-100 text-violet-500">{courseName}</span>}
      </div>
    </button>
  );
}

// ── Root component ────────────────────────────────────────────────────────────

export default function StaffDashboard() {
  const { user, logout } = useAuth();

  const [sidebarOpen,  setSidebarOpen]  = useState(false);
  const [changePwOpen, setChangePwOpen] = useState(false);
  const [view,         setView]         = useState('subjects');

  const [boards,   setBoards]   = useState([]);
  const [classes,  setClasses]  = useState([]);
  const [streams,  setStreams]  = useState([]);
  const [subjects, setSubjects] = useState([]);
  const [loading,  setLoading]  = useState(true);

  const [selectedSubject, setSelectedSubject] = useState(null);
  const [subjectContext,  setSubjectContext]  = useState(null); // { board, cls, course }
  const [chapters,        setChapters]        = useState([]);
  const [loadingChapters, setLoadingChapters] = useState(false);
  const [editingChapterId, setEditingChapterId] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const [br, cl, st, su] = await Promise.all([
          api().get('/staff/content/boards'),
          api().get('/staff/content/classes'),
          api().get('/staff/content/streams'),
          api().get('/staff/content/subjects'),
        ]);
        setBoards(br.data);
        setClasses(cl.data);
        setStreams(st.data);
        setSubjects(su.data);
      } catch {
        toast.error('Failed to load content. Please refresh.');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const selectSubject = useCallback(async (subj) => {
    setSelectedSubject(subj);
    setChapters([]);
    setView('chapters');
    setLoadingChapters(true);
    // Resolve Board → Class → Course names for breadcrumb context
    const board  = boards.find(b => b.id === subj.board_id);
    const cls    = classes.find(c => c.id === subj.class_id);
    const course = subj.stream_name || streams.find(s => s.id === subj.stream_id)?.name || null;
    setSubjectContext({ board: board?.name, cls: cls?.name, course });
    try {
      const res = await api().get(`/staff/content/chapters/${subj.id}`);
      setChapters(res.data);
    } catch {
      toast.error('Failed to load chapters.');
    } finally {
      setLoadingChapters(false);
    }
  }, [boards, classes, streams]);

  // Refresh chapter list after save to reflect updated indicators
  const handleChapterSaved = useCallback(async (updatedData) => {
    setEditingChapterId(null);
    // Refresh the chapter list so dots/badges update
    if (selectedSubject) {
      try {
        const res = await api().get(`/staff/content/chapters/${selectedSubject.id}`);
        setChapters(res.data);
      } catch { /* ignore */ }
    }
    toast.success('Saved');
  }, [selectedSubject]);

  const handleViewChange = (v) => {
    setView(v);
    setSidebarOpen(false);
    if (v === 'subjects') setSelectedSubject(null);
  };

  const handleLogout = async () => {
    try { await logout(); } catch { /* ignore */ }
    window.location.href = '/login';
  };

  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden">
      {/* Desktop sidebar */}
      <div className="hidden lg:flex lg:w-64 lg:flex-shrink-0">
        <div className="w-full h-full">
          <Sidebar user={user} onLogout={handleLogout} view={view} onViewChange={handleViewChange} onChangePassword={() => { setSidebarOpen(false); setChangePwOpen(true); }} />
        </div>
      </div>

      {/* Mobile drawer backdrop */}
      {sidebarOpen && <div className="fixed inset-0 z-40 bg-black/40 lg:hidden" onClick={() => setSidebarOpen(false)} />}

      {/* Mobile drawer */}
      <div className={`fixed inset-y-0 left-0 z-50 w-72 lg:hidden transform transition-transform duration-300 ease-in-out ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <Sidebar user={user} onLogout={handleLogout} view={view} onViewChange={handleViewChange} onChangePassword={() => { setSidebarOpen(false); setChangePwOpen(true); }} />
      </div>

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header className="lg:hidden flex items-center gap-3 px-4 py-3 bg-white border-b border-gray-100 flex-shrink-0 shadow-sm">
          <button onClick={() => setSidebarOpen(true)} className="p-2 rounded-xl text-gray-500 hover:bg-gray-100 transition-colors" aria-label="Open menu"><HamburgerIcon /></button>
          <div className="flex items-center gap-2">
            <img src="/logo-144.webp" alt="" className="w-7 h-7 rounded-lg object-cover" />
            <span className="font-bold text-gray-900 text-sm">Staff Portal</span>
          </div>
          <div className="ml-auto">
            <div className="w-8 h-8 rounded-full bg-violet-100 flex items-center justify-center text-violet-700 font-bold text-sm select-none">{(user?.name || 'S').charAt(0).toUpperCase()}</div>
          </div>
        </header>

        <main className="flex-1 overflow-hidden">
          {view === 'subjects' && (
            <SubjectsView subjects={subjects} boards={boards} classes={classes} streams={streams} loading={loading} onSelectSubject={selectSubject} />
          )}
          {view === 'chapters' && selectedSubject && (
            <ChaptersView subject={selectedSubject} subjectContext={subjectContext} chapters={chapters} loadingChapters={loadingChapters} onBack={() => handleViewChange('subjects')} onEditChapter={setEditingChapterId} />
          )}
        </main>
      </div>

      {/* Chapter editor */}
      {editingChapterId && (
        <ChapterEditor
          chapterId={editingChapterId}
          subjectName={selectedSubject?.name || ''}
          subjectContext={subjectContext}
          onClose={() => setEditingChapterId(null)}
          onSaved={handleChapterSaved}
        />
      )}

      {/* Change password */}
      {changePwOpen && <ChangePasswordModal onClose={() => setChangePwOpen(false)} />}
    </div>
  );
}

// ── Icons ─────────────────────────────────────────────────────────────────────

function HamburgerIcon() {
  return <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" /></svg>;
}
function GridIcon() {
  return <svg width="17" height="17" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></svg>;
}
function BookIcon() {
  return <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8"><path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" /></svg>;
}
function KeyIcon() {
  return <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z" /></svg>;
}
function LogoutIcon() {
  return <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" /></svg>;
}
function CloseIcon() {
  return <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>;
}
function BackIcon() {
  return <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" /></svg>;
}
function EditIcon() {
  return <svg width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>;
}
function AttachIcon() {
  return <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" /></svg>;
}
function IndexIcon() {
  return <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>;
}
function PlusIcon() {
  return <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" /></svg>;
}
function TrashIcon() {
  return <svg width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>;
}
function ArrowUpIcon() {
  return <svg width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" /></svg>;
}
function ArrowDownIcon() {
  return <svg width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" /></svg>;
}
function UploadIcon() {
  return <svg width="24" height="24" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5"><path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" /></svg>;
}
