/**
 * HistoryPage — /history
 *
 * Spec: Server-first, localStorage-fallback conversation manager.
 * 7 state variables | 5 per-card actions | 2 dialogs | time grouping |
 * search + 3-way filter | skeleton loading | visibilitychange cross-device sync.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  MessageSquare, Star, Trash2, Clock, Search, Loader2,
  Archive, Sparkles, MoreHorizontal, Plus, Pencil,
  ArchiveRestore, ExternalLink, ChevronDown, X, Check,
} from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import ModalOverlay from '@/components/ui/ModalOverlay';
import { PageTitle } from '@/components/PageTitle';
import { useAuth } from '@/context/AuthContext';
import { getConversations, deleteConversation, updateConversation, getAnonConversations, deleteAnonConversation } from '@/utils/api';
import { toast } from 'sonner';

import { formatDistanceToNow, isToday, isYesterday } from 'date-fns';
import { cn } from '@/lib/utils';

// ── Time grouping ─────────────────────────────────────────────────────────────
function resolveGroup(conv) {
  const date = new Date(conv.updated_at || conv.created_at || Date.now());
  if (isToday(date))     return 'today';
  if (isYesterday(date)) return 'yesterday';
  return 'older';
}

const GROUP_LABELS = {
  today:     'Today',
  yesterday: 'Yesterday',
  older:     'Older',
};

// ── Filtering pipeline ────────────────────────────────────────────────────────
function applyFilters(conversations, searchQuery, filterValue) {
  return conversations
    .map((c) => ({ ...c, group: resolveGroup(c) }))       // fresh group every render
    .filter((c) => {
      // Category filter
      if (filterValue === 'starred')  return c.starred && !c.archived;
      if (filterValue === 'archived') return c.archived;
      return !c.archived;                                   // "all" = exclude archived
    })
    .filter((c) => {
      if (!searchQuery.trim()) return true;
      const q = searchQuery.toLowerCase();
      return (
        c.title?.toLowerCase().includes(q) ||
        c.preview?.toLowerCase().includes(q) ||
        c.subject_name?.toLowerCase().includes(q)
      );
    });
}

function groupConversations(filtered) {
  const groups = { today: [], yesterday: [], older: [] };
  filtered.forEach((c) => {
    groups[c.group]?.push(c);
  });
  return groups;
}

// ── Skeleton row ──────────────────────────────────────────────────────────────
function SkeletonRow({ i }) {
  const titleW = `${55 + (i % 3) * 15}%`;
  const previewW = `${40 + (i % 4) * 10}%`;
  return (
    <div className="px-4 py-3.5 flex items-start gap-3 animate-pulse">
      <div className="w-9 h-9 rounded-xl flex-shrink-0" style={{ background: 'hsl(var(--muted))' }} />
      <div className="flex-1 space-y-2 min-w-0">
        <div className="h-3.5 rounded" style={{ background: 'hsl(var(--muted))', width: titleW }} />
        <div className="h-2.5 rounded" style={{ background: 'hsl(var(--muted) / 0.7)', width: previewW }} />
        <div className="h-2 rounded w-20" style={{ background: 'hsl(var(--muted) / 0.5)' }} />
      </div>
    </div>
  );
}

// ── Conversation Card ─────────────────────────────────────────────────────────
function ConversationCard({ conv, onOpen, onStar, onArchive, onDelete, onRename, isAnon }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  const timeLabel = conv.updated_at || conv.created_at
    ? formatDistanceToNow(new Date(conv.updated_at || conv.created_at), { addSuffix: true })
    : 'Unknown';

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [menuOpen]);

  return (
    <div className="group relative"
    >
      {/* Main card row */}
      <div
        onClick={() => onOpen(conv.id)}
        className="flex items-start gap-3 px-4 py-3.5 cursor-pointer transition-all duration-200 hover:bg-primary/5"
        style={{ borderBottom: '1px solid rgba(139,92,246,0.06)' }}
        role="button"
        tabIndex={0}
        aria-label={`Open conversation: ${conv.title || 'Untitled'}`}
        onKeyDown={(e) => { if (e.key === 'Enter') onOpen(conv.id); }}
        data-testid="history-conversation-item"
      >
        {/* Avatar icon */}
        <div
          className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 mt-0.5"
          style={{
            background: conv.starred
              ? 'linear-gradient(135deg, rgba(245,158,11,0.20), rgba(234,179,8,0.12))'
              : 'linear-gradient(135deg, rgba(124,58,237,0.15), rgba(139,92,246,0.10))',
            border: conv.starred
              ? '1px solid rgba(245,158,11,0.25)'
              : '1px solid rgba(139,92,246,0.15)',
          }}
          aria-hidden="true"
        >
          <MessageSquare
            size={16}
            style={{ color: conv.starred ? '#f59e0b' : 'hsl(var(--primary))' }}
            aria-hidden="true"
          />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {/* Title row */}
          <div className="flex items-center gap-1.5 mb-0.5">
            <span className="text-sm font-medium text-foreground truncate leading-snug">
              {conv.title || 'Untitled conversation'}
            </span>
            {conv.starred && (
              <Star size={11} className="flex-shrink-0 fill-amber-400 text-amber-400" aria-label="Starred" />
            )}
            {conv.archived && (
              <Archive size={11} className="flex-shrink-0 text-muted-foreground/50" aria-label="Archived" />
            )}
          </div>

          {/* Preview */}
          {(conv.preview || conv.subject_name) && (
            <p className="text-xs text-muted-foreground/60 truncate leading-relaxed">
              {conv.preview || `${conv.subject_name} conversation`}
            </p>
          )}

          {/* Meta row */}
          <div className="flex items-center gap-3 mt-1">
            <span className="text-[11px] text-muted-foreground/40">{timeLabel}</span>
            {conv.tokens > 0 && (
              <span
                className="text-[10px] px-2 py-0.5 rounded-full font-medium"
                style={{
                  background: 'rgba(139,92,246,0.08)',
                  color: 'hsl(var(--primary) / 0.7)',
                  border: '1px solid rgba(139,92,246,0.15)',
                }}
              >
                {(conv.tokens / 1000).toFixed(1)}K tokens
              </span>
            )}
          </div>
        </div>

        {/* ⋯ hover menu trigger */}
        <div className="relative" ref={menuRef} onClick={(e) => e.stopPropagation()}>
          <button
            onClick={(e) => { e.stopPropagation(); setMenuOpen((v) => !v); }}
            className={cn(
              'w-11 h-11 rounded-lg flex items-center justify-center transition-all duration-150',
              'text-muted-foreground hover:text-foreground hover:bg-primary/10',
              'opacity-100 md:opacity-0 md:group-hover:opacity-100 focus:opacity-100',
              menuOpen && 'bg-primary/10 text-foreground'
            )}
            aria-label="Conversation actions"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
          >
            <MoreHorizontal size={15} aria-hidden="true" />
          </button>

          {/* Dropdown menu */}
          {menuOpen && (
              <div
                className="absolute right-0 sm:right-0 left-auto top-8 z-50 rounded-xl overflow-hidden shadow-xl min-w-[160px] max-w-[calc(100vw-2rem)]"
                style={{
                  background: 'var(--popover-glass, hsl(var(--popover)))',
                  backdropFilter: 'blur(20px)',
                  WebkitBackdropFilter: 'blur(20px)',
                  border: '1px solid rgba(139,92,246,0.18)',
                  boxShadow: '0 8px 32px rgba(0,0,0,0.12), 0 0 0 1px rgba(139,92,246,0.08)',
                }}
                role="menu"
              >
                {[
                  {
                    icon: ExternalLink, label: 'Open',
                    action: () => { setMenuOpen(false); onOpen(conv.id); },
                    className: '',
                  },
                  ...(!isAnon ? [{
                    icon: Pencil, label: 'Rename',
                    action: () => { setMenuOpen(false); onRename(conv); },
                    className: '',
                  },
                  {
                    icon: Star, label: conv.starred ? 'Unstar' : 'Star',
                    action: () => { setMenuOpen(false); onStar(conv); },
                    className: conv.starred ? 'text-amber-400' : '',
                  },
                  {
                    icon: conv.archived ? ArchiveRestore : Archive,
                    label: conv.archived ? 'Unarchive' : 'Archive',
                    action: () => { setMenuOpen(false); onArchive(conv); },
                    className: '',
                  }] : []),
                  {
                    icon: Trash2, label: 'Delete',
                    action: () => { setMenuOpen(false); onDelete(conv.id); },
                    className: 'text-destructive',
                    separator: !isAnon,
                  },
                ].map(({ icon: Icon, label, action, className, separator }) => (
                  <div key={label}>
                    {separator && (
                      <div style={{ height: 1, background: 'rgba(139,92,246,0.10)', margin: '2px 0' }} />
                    )}
                    <button
                      onClick={action}
                      className={cn(
                        'w-full flex items-center gap-2.5 px-3 py-2 text-sm text-foreground',
                        'hover:bg-primary/10 transition-colors duration-100 text-left',
                        className
                      )}
                      role="menuitem"
                    >
                      <Icon size={14} aria-hidden="true" />
                      {label}
                    </button>
                  </div>
                ))}
              </div>
            )}
        </div>
      </div>
    </div>
  );
}

// ── HistoryPage ───────────────────────────────────────────────────────────────
export default function HistoryPage() {
  const { user } = useAuth();
  const navigate = useNavigate();

  // ── 7 state variables ─────────────────────────────────────────────────────
  const [conversations, setConversations] = useState([]);
  const [searchQuery,   setSearchQuery]   = useState('');
  const [filterValue,   setFilterValue]   = useState('all');
  const [deleteTarget,  setDeleteTarget]  = useState(null);
  const [renameTarget,  setRenameTarget]  = useState(null);
  const [renameValue,   setRenameValue]   = useState('');
  const [loading,       setLoading]       = useState(true);

  const searchRef = useRef(null);

  const isAnon = !user;

  const loadConversations = useCallback(async () => {
    try {
      const res = isAnon ? await getAnonConversations() : await getConversations();
      const data = (res.data || []).map((c) => ({
        ...c,
        tokens:   Math.round((c.preview || '').length * 1.3),
        starred:  c.starred || false,
        archived: c.archived || false,
      }));
      setConversations(data);
      try {
        localStorage.setItem('syrabit:conversations', JSON.stringify(data));
      } catch {}
    } catch {
      try {
        const cached = localStorage.getItem('syrabit:conversations');
        if (cached) setConversations(JSON.parse(cached));
      } catch {}
    } finally {
      setLoading(false);
    }
  }, [isAnon]);

  // ── Initial load + visibilitychange cross-device sync ─────────────────────
  useEffect(() => {
    loadConversations();
    const onVisible = () => {
      if (document.visibilityState === 'visible') loadConversations();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [loadConversations]);

  // ── Derived state: filter → group ─────────────────────────────────────────
  const filtered = applyFilters(conversations, searchQuery, filterValue);
  const grouped  = groupConversations(filtered);
  const totalFiltered = filtered.length;
  const totalAll = conversations.filter((c) => !c.archived).length;

  // ── Open conversation ──────────────────────────────────────────────────────
  const handleOpen = (id) => navigate(`/chat?id=${id}`);

  // ── Star — optimistic ─────────────────────────────────────────────────────
  const handleStar = async (conv) => {
    const newStarred = !conv.starred;
    setConversations((prev) =>
      prev.map((c) => (c.id === conv.id ? { ...c, starred: newStarred } : c))
    );
    try {
      await updateConversation(conv.id, { starred: newStarred });
    } catch {
      setConversations((prev) =>
        prev.map((c) => (c.id === conv.id ? { ...c, starred: conv.starred } : c))
      );
      toast.error('Failed to update — please try again');
    }
  };

  // ── Archive — optimistic ──────────────────────────────────────────────────
  const handleArchive = async (conv) => {
    const newArchived = !conv.archived;
    setConversations((prev) =>
      prev.map((c) => (c.id === conv.id ? { ...c, archived: newArchived } : c))
    );
    try {
      await updateConversation(conv.id, { archived: newArchived });
      toast.success(newArchived ? 'Conversation archived' : 'Conversation restored');
    } catch {
      setConversations((prev) =>
        prev.map((c) => (c.id === conv.id ? { ...c, archived: conv.archived } : c))
      );
      toast.error('Failed to update — please try again');
    }
  };

  // ── Delete — dialog-gated ─────────────────────────────────────────────────
  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    const idToDelete = deleteTarget;
    setDeleteTarget(null);
    setConversations((prev) => prev.filter((c) => c.id !== idToDelete));
    try {
      isAnon ? await deleteAnonConversation(idToDelete) : await deleteConversation(idToDelete);
      toast.success('Conversation deleted');
    } catch {
      await loadConversations();
      toast.error('Failed to delete');
    }
  };

  // ── Rename — dialog-gated ─────────────────────────────────────────────────
  const handleRenameOpen = (conv) => {
    setRenameTarget(conv);
    setRenameValue(conv.title || '');
  };

  const handleRenameConfirm = async () => {
    if (!renameTarget || !renameValue.trim()) return;
    const newTitle = renameValue.trim();
    setConversations((prev) =>
      prev.map((c) => (c.id === renameTarget.id ? { ...c, title: newTitle } : c))
    );
    setRenameTarget(null);
    setRenameValue('');
    try {
      await updateConversation(renameTarget.id, { title: newTitle });
      toast.success('Renamed');
    } catch {
      toast.error('Failed to rename');
      await loadConversations();
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────


  return (
    <AppLayout pageTitle="History">
      <PageTitle title="Chat History | Syrabit.ai" />

      <div className="flex flex-col h-full overflow-y-auto" data-testid="history-conversation-list">
        <div className="w-full max-w-3xl mx-auto px-4 md:px-6 py-5 space-y-5">

          {isAnon && conversations.length > 0 && (
            <div
              className="flex items-center gap-3 p-3 rounded-xl text-sm"
              style={{
                background: 'rgba(139,92,246,0.08)',
                border: '1px solid rgba(139,92,246,0.15)',
              }}
            >
              <img src="/logo-56.webp" alt="Syrabit.ai" width="20" height="20" className="w-5 h-5 rounded flex-shrink-0" />
              <span className="text-muted-foreground flex flex-col leading-snug">
                <span>Guest chats expire after 7 days.</span>
                <span>
                  <button onClick={() => navigate('/login')} className="text-primary font-semibold hover:underline">
                    Sign up
                  </button>{' '}
                  to save them permanently.
                </span>
              </span>
            </div>
          )}

          {/* ── Header ── */}
          <div className="flex items-center justify-between">
            <div>
              <h1
                className="text-foreground shimmer-text"
                style={{ fontSize: '1.6rem', fontWeight: 700, lineHeight: 1.2 }}
              >
                History
              </h1>
              <p className="text-muted-foreground text-sm mt-1">
                {loading
                  ? 'Loading…'
                  : `${totalAll} conversation${totalAll !== 1 ? 's' : ''}`
                }
              </p>
            </div>

            {/* New Chat CTA */}
            <button
              onClick={() => navigate('/chat')}
              className="flex items-center gap-2 h-9 px-4 rounded-xl text-sm font-semibold text-white transition-all hover:opacity-90 active:scale-95"
              style={{
                background: 'linear-gradient(135deg, hsl(var(--primary)), #8b5cf6)',
                boxShadow: '0 4px 15px var(--glow-primary, rgba(139,92,246,0.35))',
              }}
              aria-label="Start a new chat"
            >
              <Plus size={15} aria-hidden="true" />
              New Chat
            </button>
          </div>

          {/* ── Search + Filter Bar ── */}
          <div className="flex items-center gap-3">
            {/* Search */}
            <div className="relative flex-1 group/search">
              <Search
                size={15}
                className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none transition-colors group-focus-within/search:text-primary"
                aria-hidden="true"
              />
              <input
                ref={searchRef}
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search conversations…"
                className="w-full h-10 pl-10 pr-4 rounded-xl text-sm text-foreground outline-none transition-all"
                style={{
                  background: 'var(--card)',
                  backdropFilter: 'blur(20px)',
                  WebkitBackdropFilter: 'blur(20px)',
                  border: '1px solid rgba(139,92,246,0.15)',
                }}
                aria-label="Search conversations"
                data-testid="history-search-input"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery('')}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground/50 hover:text-foreground text-xs px-1.5 py-0.5 rounded transition-colors"
                  aria-label="Clear search"
                >
                  Clear
                </button>
              )}
            </div>

            {/* Filter select */}
            <div className="relative flex-shrink-0">
              <select
                value={filterValue}
                onChange={(e) => setFilterValue(e.target.value)}
                className="h-10 pl-3 pr-8 rounded-xl text-sm text-foreground outline-none appearance-none cursor-pointer transition-all"
                style={{
                  background: 'var(--card)',
                  backdropFilter: 'blur(20px)',
                  WebkitBackdropFilter: 'blur(20px)',
                  border: '1px solid rgba(139,92,246,0.15)',
                  minWidth: 110,
                }}
                aria-label="Filter conversations"
                data-testid="history-filter-select"
              >
                <option value="all">All</option>
                <option value="starred">Starred</option>
                <option value="archived">Archived</option>
              </select>
              <ChevronDown
                size={13}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none"
                aria-hidden="true"
              />
            </div>
          </div>

          {/* ── Loading skeleton ── */}
          {loading && (
            <div
              className="rounded-2xl overflow-hidden"
              style={{
                background: 'var(--card)',
                backdropFilter: 'blur(20px)',
                WebkitBackdropFilter: 'blur(20px)',
                border: '1px solid rgba(139,92,246,0.10)',
              }}
            >
              {[...Array(7)].map((_, i) => <SkeletonRow key={i} i={i} />)}
            </div>
          )}

          {/* ── Empty state ── */}
          {!loading && totalFiltered === 0 && (
            <div
              className="flex flex-col items-center justify-center py-20 text-center"
            >
              <div
                className="w-20 h-20 rounded-3xl flex items-center justify-center mb-6 float-anim"
                style={{
                  background: 'linear-gradient(135deg, rgba(124,58,237,0.15), rgba(139,92,246,0.08))',
                  border: '1px solid rgba(139,92,246,0.18)',
                  boxShadow: '0 0 30px rgba(124,58,237,0.12)',
                }}
                aria-hidden="true"
              >
                <Clock size={32} style={{ color: 'hsl(var(--primary) / 0.6)' }} aria-hidden="true" />
              </div>
              <h3 className="text-lg font-semibold text-foreground">
                {searchQuery || filterValue !== 'all'
                  ? 'No matching conversations'
                  : 'No conversations yet'
                }
              </h3>
              <p className="text-sm text-muted-foreground/60 mt-1.5 max-w-xs">
                {searchQuery || filterValue !== 'all'
                  ? 'Try adjusting your search or filter'
                  : 'Start chatting with Syra to build your history'
                }
              </p>
              {!searchQuery && filterValue === 'all' && (
                <button
                  onClick={() => navigate('/chat')}
                  className="mt-6 flex items-center gap-2 px-5 py-2.5 rounded-2xl text-sm font-semibold text-white transition-all hover:opacity-90 active:scale-95"
                  style={{
                    background: 'linear-gradient(135deg, hsl(var(--primary)), #8b5cf6)',
                    boxShadow: '0 4px 18px var(--glow-primary, rgba(139,92,246,0.35))',
                  }}
                >
                  <Sparkles size={16} aria-hidden="true" />
                  Start New Chat
                </button>
              )}
              {(searchQuery || filterValue !== 'all') && (
                <button
                  onClick={() => { setSearchQuery(''); setFilterValue('all'); }}
                  className="mt-4 px-4 py-2 rounded-xl text-sm text-primary hover:bg-primary/10 transition-colors"
                  style={{ border: '1px solid rgba(139,92,246,0.25)' }}
                >
                  Reset filters
                </button>
              )}
            </div>
          )}

          {/* ── Grouped conversations ── */}
          {!loading && totalFiltered > 0 && (
            <div className="space-y-5">
              {Object.entries(GROUP_LABELS).map(([groupKey, groupLabel]) => {
                const items = grouped[groupKey] || [];
                if (!items.length) return null;
                return (
                  <div key={groupKey}>
                    {/* Group header */}
                    <div className="flex items-center gap-3 mb-2 px-1">
                      <span
                        className="text-[11px] font-bold tracking-[0.08em] uppercase"
                        style={{ color: 'hsl(var(--primary) / 0.7)' }}
                      >
                        {groupLabel}
                      </span>
                      <div
                        className="flex-1 h-px"
                        style={{ background: 'rgba(139,92,246,0.12)' }}
                        aria-hidden="true"
                      />
                      <span className="text-[10px] text-muted-foreground/40">{items.length}</span>
                    </div>

                    {/* Card list */}
                    <div
                      className="rounded-2xl overflow-hidden card-3d"
                      style={{
                        background: 'var(--card)',
                        backdropFilter: 'blur(20px) saturate(1.5)',
                        WebkitBackdropFilter: 'blur(20px) saturate(1.5)',
                        border: '1px solid rgba(139,92,246,0.10)',
                        boxShadow: '0 4px 24px rgba(0,0,0,0.15)',
                      }}
                    >
                        {items.map((conv) => (
                          <ConversationCard
                            key={conv.id}
                            conv={conv}
                            onOpen={handleOpen}
                            onStar={handleStar}
                            onArchive={handleArchive}
                            onDelete={(id) => setDeleteTarget(id)}
                            onRename={handleRenameOpen}
                            isAnon={isAnon}
                          />
                        ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Delete Dialog ── */}
      <ModalOverlay
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="Delete conversation?"
        description="This action cannot be undone. The conversation and all its messages will be permanently removed."
        footer={
          <>
            <button
              onClick={() => setDeleteTarget(null)}
              className="flex-1 h-9 rounded-xl text-sm font-medium text-muted-foreground border border-border hover:bg-accent/40 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleDeleteConfirm}
              className="flex-1 h-9 rounded-xl text-sm font-semibold text-white transition-all hover:opacity-90"
              style={{ background: 'linear-gradient(135deg, #dc2626, #ef4444)' }}
              data-testid="history-delete-confirm-button"
            >
              <Trash2 size={13} className="inline mr-1.5" aria-hidden="true" />
              Delete
            </button>
          </>
        }
      />

      {/* ── Rename Dialog ── */}
      <ModalOverlay
        open={!!renameTarget}
        onClose={() => { setRenameTarget(null); setRenameValue(''); }}
        title="Rename conversation"
      >
        <input
          type="text"
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleRenameConfirm(); }}
          placeholder="Conversation name"
          className="w-full h-10 px-3 rounded-xl text-sm text-foreground outline-none"
          style={{
            background: 'hsl(var(--input))',
            border: '1px solid rgba(139,92,246,0.20)',
          }}
          autoFocus
          maxLength={100}
          aria-label="New conversation name"
          data-testid="history-rename-input"
        />
        <div className="flex gap-2">
          <button
            onClick={() => { setRenameTarget(null); setRenameValue(''); }}
            className="flex-1 h-9 rounded-xl text-sm font-medium text-muted-foreground border border-border hover:bg-accent/40 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleRenameConfirm}
            disabled={!renameValue.trim()}
            className="flex-1 h-9 rounded-xl text-sm font-semibold text-white disabled:opacity-50 transition-all hover:opacity-90"
            style={{ background: 'linear-gradient(135deg, hsl(var(--primary)), #8b5cf6)' }}
            data-testid="history-rename-confirm-button"
          >
            <Check size={13} className="inline mr-1.5" aria-hidden="true" />
            Rename
          </button>
        </div>
      </ModalOverlay>
    </AppLayout>
  );
}
