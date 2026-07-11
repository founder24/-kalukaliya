import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '@/context/AuthContext';
import { API_BASE } from '@/utils/api';
import axios from 'axios';
import { toast } from 'sonner';

const api = () => axios.create({ baseURL: API_BASE, withCredentials: true });

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

// ── Chapter editor — full content + RAG tabs ──────────────────────────────────

const EDITOR_TABS = [
  { id: 'info',       label: 'Info' },
  { id: 'content_en', label: 'Content (EN)' },
  { id: 'content_as', label: 'Content (AS)' },
  { id: 'rag',        label: 'RAG' },
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

function ChapterEditor({ chapterId, subjectName, onClose, onSaved }) {
  const [form, setForm] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState('info');
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef(null);

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

  const set = (field) => (e) => setForm(f => ({ ...f, [field]: e.target.value }));

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

  const handleAttachFile = async (field) => {
    if (!fileRef.current) return;
    const file = fileRef.current.files[0];
    if (!file) return;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await api().post(`/staff/content/chapter/${chapterId}/attach-file?field=${field}`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      fileRef.current.value = '';
      if (res.data?.skipped) {
        toast.info('File already attached — no duplicate content added');
      } else {
        const extracted = res.data?.text_extracted ?? 0;
        toast.success(`Extracted ${extracted.toLocaleString()} chars and appended to ${field}`);
        // Reload the field value so the textarea reflects the new text
        const updated = await api().get(`/staff/content/chapter/${chapterId}`);
        setForm(updated.data);
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'File attach failed');
    } finally { setUploading(false); }
  };

  if (loading) return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.45)' }}>
      <div className="bg-white rounded-2xl p-8 flex items-center gap-3 shadow-2xl">
        <Spinner /><span className="text-sm text-gray-500">Loading chapter…</span>
      </div>
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-stretch justify-center" style={{ background: 'rgba(0,0,0,0.55)' }} onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-white w-full max-w-4xl flex flex-col overflow-hidden sm:my-4 sm:rounded-2xl shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0 bg-white">
          <div className="min-w-0">
            <div className="text-xs text-gray-400 mb-0.5">{subjectName}</div>
            <h2 className="text-sm font-bold text-gray-900 truncate">Ch. {form?.chapter_number} · {form?.title}</h2>
          </div>
          <div className="flex items-center gap-2 ml-4 flex-shrink-0">
            <StatusBadge status={form?.status} />
            <button onClick={onClose} className="p-2 rounded-xl text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"><CloseIcon /></button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 px-4 pt-2 pb-0 border-b border-gray-100 flex-shrink-0 bg-white overflow-x-auto">
          {EDITOR_TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-2 text-xs font-semibold rounded-t-lg transition-colors whitespace-nowrap border-b-2 ${tab === t.id ? 'border-violet-500 text-violet-700 bg-violet-50' : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}
            >{t.label}</button>
          ))}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-5 space-y-4">

          {tab === 'info' && (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <FieldLabel>Title (English)</FieldLabel>
                  <input type="text" value={form?.title || ''} onChange={set('title')} className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400" placeholder="Chapter title" />
                </div>
                <div>
                  <FieldLabel>Title (Assamese)</FieldLabel>
                  <input type="text" value={form?.title_as || ''} onChange={set('title_as')} className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400" placeholder="অধ্যায়ৰ শিৰোনাম" />
                </div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <FieldLabel>Status</FieldLabel>
                  <select value={form?.status || 'draft'} onChange={set('status')} className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400">
                    <option value="planned">Planned</option>
                    <option value="draft">Draft</option>
                    <option value="published">Published</option>
                    <option value="archived">Archived</option>
                  </select>
                </div>
                <div>
                  <FieldLabel>Content type</FieldLabel>
                  <select value={form?.content_type || 'notes'} onChange={set('content_type')} className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400">
                    <option value="notes">Notes</option>
                    <option value="qa">Q&A</option>
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
                <textarea value={form?.meta_description || ''} onChange={set('meta_description')} rows={2} className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400 resize-none" placeholder="SEO meta description (max 160 chars)" />
              </div>
              <div>
                <FieldLabel>Keywords</FieldLabel>
                <input type="text" value={form?.keywords || ''} onChange={set('keywords')} className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400" placeholder="comma, separated, keywords" />
              </div>
              {/* Content presence summary */}
              <div className="bg-gray-50 rounded-xl p-3 border border-gray-100">
                <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Content presence</div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs text-gray-600">
                  {[
                    ['content_en', 'Content EN'], ['content_as', 'Content AS'],
                    ['notes_en', 'Notes EN'], ['notes_as', 'Notes AS'],
                    ['qa_text_en', 'Q&A EN'], ['qa_text_as', 'Q&A AS'],
                    ['rag_text_en', 'RAG EN'], ['rag_text_as', 'RAG AS'],
                  ].map(([field, label]) => (
                    <div key={field} className="flex items-center gap-1.5">
                      <Dot filled={!!(form?.[field]?.trim())} label={label} />
                      <span className={form?.[field]?.trim() ? 'text-gray-700' : 'text-gray-400'}>{label}</span>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {tab === 'content_en' && (
            <>
              <div>
                <FieldLabel chars={form?.content_en?.length || 0}>Content — English (HTML/Markdown)</FieldLabel>
                <BigTextarea value={form?.content_en || ''} onChange={set('content_en')} placeholder="Chapter content in English…" mono />
              </div>
              <div>
                <FieldLabel chars={form?.notes_en?.length || 0}>Structured Notes — English (Markdown)</FieldLabel>
                <BigTextarea value={form?.notes_en || ''} onChange={set('notes_en')} placeholder="AI-structured study notes in English…" rows={10} mono />
              </div>
            </>
          )}

          {tab === 'content_as' && (
            <>
              <div>
                <FieldLabel chars={form?.content_as?.length || 0}>Content — Assamese</FieldLabel>
                <BigTextarea value={form?.content_as || ''} onChange={set('content_as')} placeholder="অসমীয়া ভাষাত অধ্যায়ৰ বিষয়বস্তু…" rows={14} />
              </div>
              <div>
                <FieldLabel chars={form?.notes_as?.length || 0}>Structured Notes — Assamese (Markdown)</FieldLabel>
                <BigTextarea value={form?.notes_as || ''} onChange={set('notes_as')} placeholder="অসমীয়া ভাষাত গঠনমূলক টোকা…" rows={10} />
              </div>
            </>
          )}

          {tab === 'rag' && (
            <>
              {/* Hidden file input shared by all RAG attach buttons */}
              <input ref={fileRef} type="file" accept=".pdf,.txt,.md" className="hidden" />

              <div className="bg-blue-50 border border-blue-100 rounded-xl px-4 py-3 text-xs text-blue-700">
                <strong>RAG fields</strong> are the plain-text extraction the AI uses for retrieval. They should be full, unformatted text from the textbook or source PDF — not markdown. Paste directly or attach a file to auto-extract.
              </div>

              {[
                { field: 'rag_text_en',    label: 'RAG Text — English',        placeholder: 'Full plain-text chapter content for AI retrieval (English)…' },
                { field: 'rag_text_as',    label: 'RAG Text — Assamese',       placeholder: 'সম্পূৰ্ণ সাদা-পাঠ্য অধ্যায়ৰ বিষয়বস্তু (অসমীয়া)…' },
                { field: 'qa_rag_text_en', label: 'Q&A RAG — English',         placeholder: 'Expanded Q&A pairs for AI retrieval (English)…' },
                { field: 'qa_rag_text_as', label: 'Q&A RAG — Assamese',        placeholder: 'বিস্তাৰিত প্ৰশ্ন-উত্তৰ (অসমীয়া)…' },
                { field: 'pyq_rag_text',   label: 'PYQ Text (extracted PDF)',  placeholder: 'Text extracted from PYQ PDF…' },
              ].map(({ field, label, placeholder }) => (
                <div key={field}>
                  <div className="flex items-center justify-between mb-1.5">
                    <FieldLabel chars={form?.[field]?.length || 0}>{label}</FieldLabel>
                    <button
                      onClick={() => {
                        fileRef.current.onchange = () => handleAttachFile(field);
                        fileRef.current.click();
                      }}
                      disabled={uploading}
                      className="ml-3 flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-violet-50 text-violet-700 hover:bg-violet-100 border border-violet-200 transition-colors disabled:opacity-50 flex-shrink-0"
                    >
                      {uploading ? <Spinner size={3} /> : <AttachIcon />}
                      Attach file
                    </button>
                  </div>
                  <BigTextarea value={form?.[field] || ''} onChange={set(field)} placeholder={placeholder} rows={8} mono />
                </div>
              ))}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-t border-gray-100 flex-shrink-0 bg-gray-50">
          <div className="text-[11px] text-gray-400">
            {form?.content_saved_at && <>Saved {new Date(form.content_saved_at).toLocaleString()}</>}
            {form?.rag_updated_at && tab === 'rag' && <> · RAG {new Date(form.rag_updated_at).toLocaleString()}</>}
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

function ChaptersView({ subject, chapters, loadingChapters, onBack, onEditChapter }) {
  const [search, setSearch] = useState('');
  const [filterStatus, setFilterStatus] = useState('');

  const filtered = chapters.filter(c => {
    const matchSearch = !search || c.title?.toLowerCase().includes(search.toLowerCase());
    const matchStatus = !filterStatus || c.status === filterStatus;
    return matchSearch && matchStatus;
  });

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-3 px-4 sm:px-6 py-4 border-b border-gray-100 bg-white flex-shrink-0">
        <button onClick={onBack} className="p-2 rounded-xl text-gray-500 hover:bg-gray-100 transition-colors"><BackIcon /></button>
        <div className="min-w-0">
          <div className="text-xs text-gray-400">Chapters</div>
          <h1 className="text-base font-bold text-gray-900 truncate">{subject.name}</h1>
        </div>
        <div className="ml-auto flex items-center gap-1.5 flex-shrink-0">
          <StatusBadge status={subject.status} />
          <span className="text-xs text-gray-400">{chapters.length} ch.</span>
        </div>
      </div>

      <div className="flex gap-2 px-4 sm:px-6 py-3 border-b border-gray-100 bg-white flex-shrink-0">
        <input type="search" placeholder="Search chapters…" value={search} onChange={e => setSearch(e.target.value)} className="flex-1 min-w-0 px-3 py-2 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400" />
        <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} className="px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400">
          <option value="">All</option>
          <option value="published">Published</option>
          <option value="draft">Draft</option>
          <option value="planned">Planned</option>
          <option value="archived">Archived</option>
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
                      {hasUnpublishedEdit && <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-semibold border border-amber-200">Unsaved</span>}
                    </div>
                    <div className="flex items-center gap-2 mt-1 flex-wrap">
                      <div className="flex items-center gap-1">
                        <Dot filled={ch.has_content_en} label="Content EN" />
                        <Dot filled={ch.has_content_as} label="Content AS" />
                        <Dot filled={ch.has_rag_en} label="RAG EN" />
                        <Dot filled={ch.has_rag_as} label="RAG AS" />
                        <Dot filled={ch.has_notes_en} label="Notes EN" />
                        <Dot filled={ch.has_qa_en} label="Q&A EN" />
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

function SubjectsView({ subjects, boards, classes, loading, onSelectSubject }) {
  const [search, setSearch] = useState('');
  const [filterBoard, setFilterBoard] = useState('');
  const [filterClass, setFilterClass] = useState('');
  const [filterStatus, setFilterStatus] = useState('');

  const filtered = subjects.filter(s => {
    const matchSearch = !search || s.name?.toLowerCase().includes(search.toLowerCase());
    const matchBoard  = !filterBoard  || s.board_id  === filterBoard;
    const matchClass  = !filterClass  || s.class_id  === filterClass;
    const matchStatus = !filterStatus || s.status    === filterStatus;
    return matchSearch && matchBoard && matchClass && matchStatus;
  });

  const published = subjects.filter(s => s.status === 'published').length;
  const drafted   = subjects.filter(s => s.status === 'draft').length;

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 sm:px-6 py-4 border-b border-gray-100 bg-white flex-shrink-0">
        <h1 className="text-lg font-bold text-gray-900">Subjects</h1>
        <p className="text-xs text-gray-400 mt-0.5">{subjects.length} total · {published} published · {drafted} drafts</p>
      </div>

      <div className="px-4 sm:px-6 py-3 border-b border-gray-100 bg-white flex-shrink-0">
        <div className="flex flex-wrap gap-2">
          <input type="search" placeholder="Search subjects…" value={search} onChange={e => setSearch(e.target.value)} className="flex-1 min-w-[140px] px-3 py-2 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400" />
          <select value={filterBoard} onChange={e => setFilterBoard(e.target.value)} className="px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400">
            <option value="">All Boards</option>
            {boards.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          <select value={filterClass} onChange={e => setFilterClass(e.target.value)} className="px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400">
            <option value="">All Classes</option>
            {classes.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} className="px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400">
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
              <SubjectCard key={subj.id} subject={subj} boards={boards} classes={classes} onClick={() => onSelectSubject(subj)} />
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
  return (
    <button onClick={onClick} className="text-left p-4 bg-white rounded-2xl border border-gray-100 hover:border-violet-200 hover:shadow-md transition-all group">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-50 to-violet-100 flex items-center justify-center text-violet-600 flex-shrink-0"><BookIcon /></div>
        <StatusBadge status={subject.status} />
      </div>
      <div className="font-semibold text-gray-900 text-sm leading-snug group-hover:text-violet-700 transition-colors line-clamp-2 mb-2">{subject.name}</div>
      <div className="flex items-center gap-2 flex-wrap">
        {board && <span className="text-xs px-2 py-0.5 rounded-full bg-gray-50 text-gray-500 border border-gray-100">{board.name}</span>}
        {cls   && <span className="text-xs px-2 py-0.5 rounded-full bg-gray-50 text-gray-500 border border-gray-100">{cls.name}</span>}
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
  const [subjects, setSubjects] = useState([]);
  const [loading,  setLoading]  = useState(true);

  const [selectedSubject, setSelectedSubject] = useState(null);
  const [chapters,        setChapters]        = useState([]);
  const [loadingChapters, setLoadingChapters] = useState(false);
  const [editingChapterId, setEditingChapterId] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const [br, cl, su] = await Promise.all([
          api().get('/staff/content/boards'),
          api().get('/staff/content/classes'),
          api().get('/staff/content/subjects'),
        ]);
        setBoards(br.data);
        setClasses(cl.data);
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
    try {
      const res = await api().get(`/staff/content/chapters/${subj.id}`);
      setChapters(res.data);
    } catch {
      toast.error('Failed to load chapters.');
    } finally {
      setLoadingChapters(false);
    }
  }, []);

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
            <SubjectsView subjects={subjects} boards={boards} classes={classes} loading={loading} onSelectSubject={selectSubject} />
          )}
          {view === 'chapters' && selectedSubject && (
            <ChaptersView subject={selectedSubject} chapters={chapters} loadingChapters={loadingChapters} onBack={() => handleViewChange('subjects')} onEditChapter={setEditingChapterId} />
          )}
        </main>
      </div>

      {/* Chapter editor */}
      {editingChapterId && (
        <ChapterEditor
          chapterId={editingChapterId}
          subjectName={selectedSubject?.name || ''}
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
