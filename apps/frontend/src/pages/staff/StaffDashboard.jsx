import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '@/context/AuthContext';
import { API_BASE } from '@/utils/api';
import axios from 'axios';
import { toast } from 'sonner';

const api = () => axios.create({ baseURL: API_BASE, withCredentials: true });

const STATUS_COLORS = {
  published: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  draft:     'bg-yellow-100 text-yellow-700 border-yellow-200',
  planned:   'bg-blue-100 text-blue-700 border-blue-200',
  archived:  'bg-gray-100 text-gray-500 border-gray-200',
};
const statusLabel = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Unknown');

function StatusBadge({ status }) {
  const cls = STATUS_COLORS[status] || STATUS_COLORS.planned;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${cls}`}>
      {statusLabel(status)}
    </span>
  );
}

function LoadingSpinner({ size = 6 }) {
  return (
    <div
      className={`w-${size} h-${size} border-2 rounded-full animate-spin`}
      style={{ borderColor: 'hsl(var(--primary))', borderTopColor: 'transparent' }}
    />
  );
}

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
        <SidebarLink
          active={view === 'subjects'}
          icon={<GridIcon />}
          label="Subjects"
          onClick={() => onViewChange('subjects')}
        />
        <SidebarLink
          active={false}
          icon={<InfoIcon />}
          label="About"
          onClick={() => toast.info('Staff portal v1 — content management only.')}
        />
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
        <span className="inline-block mb-3 px-2 py-0.5 rounded-full text-xs font-semibold bg-violet-50 text-violet-700 border border-violet-200">
          Staff
        </span>
        <button
          onClick={onChangePassword}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-violet-50 hover:text-violet-700 transition-colors mb-1"
        >
          <KeyIcon />
          Change password
        </button>
        <button
          onClick={onLogout}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-red-50 hover:text-red-600 transition-colors"
        >
          <LogoutIcon />
          Sign out
        </button>
      </div>
    </aside>
  );
}

function SidebarLink({ active, icon, label, onClick }) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all ${
        active
          ? 'bg-violet-50 text-violet-700'
          : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
      }`}
    >
      <span className={active ? 'text-violet-600' : 'text-gray-400'}>{icon}</span>
      {label}
    </button>
  );
}

function ChangePasswordModal({ onClose }) {
  const [form, setForm] = useState({ current: '', next: '', confirm: '' });
  const [saving, setSaving] = useState(false);
  const [strength, setStrength] = useState(0);

  const set = (f) => (e) => {
    const val = e.target.value;
    setForm((prev) => ({ ...prev, [f]: val }));
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
    if (form.next.length < 8) {
      toast.error('Password must be at least 8 characters');
      return;
    }
    if (form.next !== form.confirm) {
      toast.error('Passwords do not match');
      return;
    }
    setSaving(true);
    try {
      await api().post('/staff/auth/change-password', {
        current_password: form.current,
        new_password: form.next,
      });
      toast.success('Password changed — use your new password next time you log in.');
      onClose();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to change password');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-white w-full sm:max-w-md sm:rounded-2xl shadow-2xl flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h2 className="text-base font-bold text-gray-900">Change Password</h2>
          <button
            onClick={onClose}
            className="p-2 rounded-xl text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
          >
            <CloseIcon />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {[
            { key: 'current', label: 'Current password',  type: 'password' },
            { key: 'next',    label: 'New password',      type: 'password' },
            { key: 'confirm', label: 'Confirm new password', type: 'password' },
          ].map(({ key, label, type }) => (
            <div key={key}>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
                {label}
              </label>
              <input
                type={type}
                value={form[key]}
                onChange={set(key)}
                autoComplete="new-password"
                className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-violet-400 focus:border-transparent"
              />
              {key === 'next' && form.next.length > 0 && (
                <div className="mt-2 flex items-center gap-2">
                  <div className="flex-1 h-1 rounded-full bg-gray-100 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${strengthColor}`}
                      style={{ width: `${strength * 25}%` }}
                    />
                  </div>
                  <span className="text-xs text-gray-400 w-10">{strengthLabel}</span>
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-gray-100 bg-gray-50">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-semibold text-white transition-colors disabled:opacity-60"
            style={{ background: 'hsl(var(--primary))' }}
          >
            {saving && <LoadingSpinner size={4} />}
            {saving ? 'Saving…' : 'Change Password'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ChapterEditor({ chapter, subjectName, onClose, onSaved }) {
  const [form, setForm] = useState({
    title:       chapter.title || '',
    description: chapter.description || '',
    content:     chapter.content || '',
    status:      chapter.status || 'planned',
  });
  const [saving, setSaving] = useState(false);

  const set = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }));

  const handleSave = async () => {
    setSaving(true);
    try {
      await api().patch(`/staff/content/chapter/${chapter.id}`, form);
      toast.success('Chapter saved');
      onSaved({ ...chapter, ...form });
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-white w-full sm:max-w-2xl sm:rounded-2xl shadow-2xl flex flex-col max-h-screen sm:max-h-[92vh] overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0">
          <div>
            <div className="text-xs text-gray-400 mb-0.5">{subjectName}</div>
            <h2 className="text-base font-bold text-gray-900 leading-tight line-clamp-2">
              Edit Chapter
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-xl text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors ml-4 flex-shrink-0"
          >
            <CloseIcon />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
              Title
            </label>
            <input
              type="text"
              value={form.title}
              onChange={set('title')}
              className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-violet-400 focus:border-transparent"
              placeholder="Chapter title"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
              Status
            </label>
            <select
              value={form.status}
              onChange={set('status')}
              className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-violet-400 bg-white"
            >
              <option value="planned">Planned</option>
              <option value="draft">Draft</option>
              <option value="published">Published</option>
              <option value="archived">Archived</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
              Description
            </label>
            <textarea
              value={form.description}
              onChange={set('description')}
              rows={3}
              className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-violet-400 resize-none"
              placeholder="Short chapter description"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
              Content (HTML)
            </label>
            <textarea
              value={form.content}
              onChange={set('content')}
              rows={12}
              className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-xs font-mono text-gray-900 focus:outline-none focus:ring-2 focus:ring-violet-400 resize-none"
              placeholder="<h2>Chapter Notes</h2>..."
              spellCheck={false}
            />
          </div>
        </div>

        <div className="flex items-center justify-end gap-3 px-5 py-4 border-t border-gray-100 flex-shrink-0 bg-gray-50">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-semibold text-white transition-colors disabled:opacity-60"
            style={{ background: 'hsl(var(--primary))' }}
          >
            {saving && <LoadingSpinner size={4} />}
            {saving ? 'Saving…' : 'Save Chapter'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ChaptersView({ subject, chapters, loadingChapters, onBack, onEditChapter }) {
  const [search, setSearch] = useState('');
  const [filterStatus, setFilterStatus] = useState('');

  const filtered = chapters.filter((c) => {
    const matchSearch = !search || c.title?.toLowerCase().includes(search.toLowerCase());
    const matchStatus = !filterStatus || c.status === filterStatus;
    return matchSearch && matchStatus;
  });

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-3 px-4 sm:px-6 py-4 border-b border-gray-100 bg-white flex-shrink-0">
        <button
          onClick={onBack}
          className="p-2 rounded-xl text-gray-500 hover:bg-gray-100 transition-colors"
        >
          <BackIcon />
        </button>
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
        <input
          type="search"
          placeholder="Search chapters…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 min-w-0 px-3 py-2 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400 focus:border-transparent"
        />
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400"
        >
          <option value="">All</option>
          <option value="published">Published</option>
          <option value="draft">Draft</option>
          <option value="planned">Planned</option>
          <option value="archived">Archived</option>
        </select>
      </div>

      <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-4">
        {loadingChapters ? (
          <div className="flex justify-center py-16"><LoadingSpinner /></div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-16 text-gray-400">
            {chapters.length === 0 ? 'No chapters in this subject.' : 'No chapters match the filter.'}
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((ch, idx) => (
              <div
                key={ch.id}
                className="flex items-center gap-3 p-3.5 bg-white rounded-xl border border-gray-100 hover:border-violet-200 hover:shadow-sm transition-all"
              >
                <div className="w-8 h-8 rounded-lg bg-gray-50 border border-gray-100 flex items-center justify-center text-xs font-bold text-gray-400 flex-shrink-0">
                  {ch.order_index ?? idx + 1}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-gray-900 truncate">{ch.title}</div>
                  {ch.description && (
                    <div className="text-xs text-gray-400 truncate mt-0.5">{ch.description}</div>
                  )}
                </div>
                <StatusBadge status={ch.status} />
                <button
                  onClick={() => onEditChapter(ch)}
                  className="flex-shrink-0 px-3 py-1.5 rounded-lg text-xs font-semibold text-violet-700 bg-violet-50 hover:bg-violet-100 transition-colors"
                >
                  Edit
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SubjectsView({ subjects, boards, classes, loading, onSelectSubject }) {
  const [search, setSearch] = useState('');
  const [filterBoard, setFilterBoard] = useState('');
  const [filterClass, setFilterClass] = useState('');
  const [filterStatus, setFilterStatus] = useState('');

  const filtered = subjects.filter((s) => {
    const matchSearch = !search || s.name?.toLowerCase().includes(search.toLowerCase());
    const matchBoard  = !filterBoard  || s.board_id  === filterBoard;
    const matchClass  = !filterClass  || s.class_id  === filterClass;
    const matchStatus = !filterStatus || s.status    === filterStatus;
    return matchSearch && matchBoard && matchClass && matchStatus;
  });

  const published   = subjects.filter((s) => s.status === 'published').length;
  const drafted     = subjects.filter((s) => s.status === 'draft').length;

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 sm:px-6 py-4 border-b border-gray-100 bg-white flex-shrink-0">
        <h1 className="text-lg font-bold text-gray-900">Subjects</h1>
        <p className="text-xs text-gray-400 mt-0.5">
          {subjects.length} total · {published} published · {drafted} drafts
        </p>
      </div>

      <div className="px-4 sm:px-6 py-3 border-b border-gray-100 bg-white flex-shrink-0">
        <div className="flex flex-wrap gap-2">
          <input
            type="search"
            placeholder="Search subjects…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 min-w-[140px] px-3 py-2 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-400 focus:border-transparent"
          />
          <select
            value={filterBoard}
            onChange={(e) => setFilterBoard(e.target.value)}
            className="px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400"
          >
            <option value="">All Boards</option>
            {boards.map((b) => (
              <option key={b.id} value={b.id}>{b.name}</option>
            ))}
          </select>
          <select
            value={filterClass}
            onChange={(e) => setFilterClass(e.target.value)}
            className="px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400"
          >
            <option value="">All Classes</option>
            {classes.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white focus:outline-none focus:ring-2 focus:ring-violet-400"
          >
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
          <div className="flex justify-center py-20"><LoadingSpinner /></div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20 text-gray-400">
            {subjects.length === 0 ? 'No subjects found.' : 'No subjects match the filter.'}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {filtered.map((subj) => (
              <SubjectCard key={subj.id} subject={subj} boards={boards} classes={classes} onClick={() => onSelectSubject(subj)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SubjectCard({ subject, boards, classes, onClick }) {
  const board = boards.find((b) => b.id === subject.board_id);
  const cls   = classes.find((c) => c.id === subject.class_id);

  return (
    <button
      onClick={onClick}
      className="text-left p-4 bg-white rounded-2xl border border-gray-100 hover:border-violet-200 hover:shadow-md transition-all group"
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-50 to-violet-100 flex items-center justify-center text-violet-600 flex-shrink-0">
          <BookIcon />
        </div>
        <StatusBadge status={subject.status} />
      </div>
      <div className="font-semibold text-gray-900 text-sm leading-snug group-hover:text-violet-700 transition-colors line-clamp-2 mb-2">
        {subject.name}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        {board && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-gray-50 text-gray-500 border border-gray-100">
            {board.name}
          </span>
        )}
        {cls && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-gray-50 text-gray-500 border border-gray-100">
            {cls.name}
          </span>
        )}
      </div>
    </button>
  );
}

export default function StaffDashboard() {
  const { user, logout } = useAuth();

  const [sidebarOpen,  setSidebarOpen]  = useState(false);
  const [changePwOpen, setChangePwOpen] = useState(false);
  const [view, setView]                 = useState('subjects');

  const [boards,   setBoards]   = useState([]);
  const [classes,  setClasses]  = useState([]);
  const [subjects, setSubjects] = useState([]);
  const [loading,  setLoading]  = useState(true);

  const [selectedSubject,  setSelectedSubject]  = useState(null);
  const [chapters,         setChapters]         = useState([]);
  const [loadingChapters,  setLoadingChapters]  = useState(false);
  const [editingChapter,   setEditingChapter]   = useState(null);

  useEffect(() => {
    const load = async () => {
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
    };
    load();
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

  const handleChapterSaved = useCallback((updated) => {
    setChapters((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
    setEditingChapter(null);
  }, []);

  const handleViewChange = (v) => {
    setView(v);
    setSidebarOpen(false);
    if (v === 'subjects') setSelectedSubject(null);
  };

  const openChangePw = () => {
    setSidebarOpen(false);
    setChangePwOpen(true);
  };

  const handleLogout = async () => {
    try {
      await logout();
      window.location.href = '/login';
    } catch {
      window.location.href = '/login';
    }
  };

  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden">
      {/* ── Desktop sidebar ── */}
      <div className="hidden lg:flex lg:w-64 lg:flex-shrink-0">
        <div className="w-full h-full">
          <Sidebar
            user={user}
            onLogout={handleLogout}
            view={view}
            onViewChange={handleViewChange}
            onChangePassword={openChangePw}
          />
        </div>
      </div>

      {/* ── Mobile drawer backdrop ── */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ── Mobile drawer ── */}
      <div
        className={`fixed inset-y-0 left-0 z-50 w-72 lg:hidden transform transition-transform duration-300 ease-in-out ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <Sidebar
          user={user}
          onLogout={handleLogout}
          view={view}
          onViewChange={handleViewChange}
          onChangePassword={openChangePw}
        />
      </div>

      {/* ── Main area ── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Mobile top bar */}
        <header className="lg:hidden flex items-center gap-3 px-4 py-3 bg-white border-b border-gray-100 flex-shrink-0 shadow-sm">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-2 rounded-xl text-gray-500 hover:bg-gray-100 transition-colors"
            aria-label="Open menu"
          >
            <HamburgerIcon />
          </button>
          <div className="flex items-center gap-2">
            <img src="/logo-144.webp" alt="" className="w-7 h-7 rounded-lg object-cover" />
            <span className="font-bold text-gray-900 text-sm">Staff Portal</span>
          </div>
          <div className="ml-auto">
            <div className="w-8 h-8 rounded-full bg-violet-100 flex items-center justify-center text-violet-700 font-bold text-sm select-none">
              {(user?.name || 'S').charAt(0).toUpperCase()}
            </div>
          </div>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-hidden">
          {view === 'subjects' && (
            <SubjectsView
              subjects={subjects}
              boards={boards}
              classes={classes}
              loading={loading}
              onSelectSubject={selectSubject}
            />
          )}
          {view === 'chapters' && selectedSubject && (
            <ChaptersView
              subject={selectedSubject}
              chapters={chapters}
              loadingChapters={loadingChapters}
              onBack={() => handleViewChange('subjects')}
              onEditChapter={setEditingChapter}
            />
          )}
        </main>
      </div>

      {/* ── Chapter editor modal ── */}
      {editingChapter && (
        <ChapterEditor
          chapter={editingChapter}
          subjectName={selectedSubject?.name || ''}
          onClose={() => setEditingChapter(null)}
          onSaved={handleChapterSaved}
        />
      )}

      {/* ── Change password modal ── */}
      {changePwOpen && (
        <ChangePasswordModal onClose={() => setChangePwOpen(false)} />
      )}
    </div>
  );
}

// ── Icons ────────────────────────────────────────────────────────────────────

function HamburgerIcon() {
  return (
    <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  );
}

function GridIcon() {
  return (
    <svg width="17" height="17" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  );
}

function BookIcon() {
  return (
    <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" />
    </svg>
  );
}

function InfoIcon() {
  return (
    <svg width="17" height="17" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="10" />
      <path strokeLinecap="round" d="M12 16v-4m0-4h.01" />
    </svg>
  );
}

function KeyIcon() {
  return (
    <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z" />
    </svg>
  );
}

function LogoutIcon() {
  return (
    <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
}

function BackIcon() {
  return (
    <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
    </svg>
  );
}
