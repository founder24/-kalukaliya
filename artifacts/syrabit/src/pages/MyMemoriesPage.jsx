/**
 * MyMemoriesPage — /profile/memories
 *
 * Lets a signed-in student browse and delete entries the backend
 * `memory_brain` collection has saved about them (Q&A turns + confirmed
 * flashcard facts written by Task #401). Backed by:
 *   GET    /api/user/memories            (paginated, scoped to user_id)
 *   DELETE /api/user/memories/{memory_id} (scoped to user_id)
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Trash2, Loader2, Brain, ChevronLeft, AlertTriangle } from 'lucide-react';
import { toast } from 'sonner';
import { formatDistanceToNow } from 'date-fns';

import { AppLayout } from '@/components/layout/AppLayout';
import { PageTitle } from '@/components/PageTitle';
import { useAuth } from '@/context/AuthContext';
import { apiClient } from '@/utils/api';

const PAGE_SIZE = 20;

function MemoryCard({ memory, onDelete, deleting }) {
  const created = memory.created_at ? new Date(memory.created_at) : null;
  const timeLabel = created
    ? formatDistanceToNow(created, { addSuffix: true })
    : '';

  const subjectLabel = memory.subject_name || memory.chapter_name || memory.event;

  return (
    <div
      className="rounded-2xl p-4"
      style={{
        background: 'hsl(var(--card))',
        border: '1px solid hsl(var(--border))',
      }}
      data-testid="memory-card"
    >
      <div className="flex items-start gap-3">
        <div
          className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
          style={{
            background: 'rgba(139,92,246,0.10)',
            border: '1px solid rgba(139,92,246,0.20)',
          }}
        >
          <Brain size={16} style={{ color: 'hsl(var(--primary))' }} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap text-xs text-muted-foreground mb-1">
            <span
              className="px-2 py-0.5 rounded-full font-semibold uppercase tracking-wide"
              style={{
                background: 'rgba(139,92,246,0.10)',
                color: 'hsl(var(--primary))',
              }}
            >
              {memory.kind || 'note'}
            </span>
            {subjectLabel && <span className="truncate">{subjectLabel}</span>}
            {timeLabel && <span>· {timeLabel}</span>}
          </div>
          <p className="text-sm text-foreground whitespace-pre-wrap break-words">
            {memory.text}
          </p>
        </div>
        <button
          type="button"
          onClick={() => onDelete(memory)}
          disabled={deleting}
          aria-label="Delete this memory"
          data-testid="delete-memory"
          className="flex-shrink-0 p-2 rounded-xl text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-50"
        >
          {deleting ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}
        </button>
      </div>
    </div>
  );
}

export default function MyMemoriesPage() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [showForgetAll, setShowForgetAll] = useState(false);
  const [forgetText, setForgetText] = useState('');
  const [forgetting, setForgetting] = useState(false);

  const load = useCallback(async (off, append) => {
    if (off === 0) setLoading(true);
    else setLoadingMore(true);
    try {
      const res = await apiClient().get('/user/memories', {
        params: { limit: PAGE_SIZE, offset: off },
      });
      const data = res.data || {};
      const next = Array.isArray(data.items) ? data.items : [];
      setItems((prev) => (append ? [...prev, ...next] : next));
      setOffset(off + next.length);
      setTotal(Number(data.total || 0));
      setHasMore(Boolean(data.has_more));
    } catch (err) {
      toast.error('Failed to load your saved memories');
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    load(0, false);
  }, [user, load]);

  const handleDelete = (memory) => {
    setPendingDelete(memory);
  };

  const confirmForgetAll = async () => {
    if (forgetText !== 'FORGET') return;
    setForgetting(true);
    try {
      const res = await apiClient().delete('/user/memories');
      const deleted = Number(res?.data?.deleted || 0);
      setItems([]);
      setOffset(0);
      setTotal(0);
      setHasMore(false);
      setShowForgetAll(false);
      setForgetText('');
      toast.success(
        deleted === 0
          ? 'Nothing to forget'
          : `Forgot ${deleted} ${deleted === 1 ? 'memory' : 'memories'}`,
      );
    } catch (err) {
      toast.error('Failed to forget your memories');
    } finally {
      setForgetting(false);
    }
  };

  const confirmDelete = async () => {
    const memory = pendingDelete;
    if (!memory) return;
    setPendingDelete(null);
    setDeletingId(memory.id);
    try {
      await apiClient().delete(`/user/memories/${encodeURIComponent(memory.id)}`);
      setItems((prev) => {
        const next = prev.filter((m) => m.id !== memory.id);
        setOffset(next.length);
        setTotal((t) => {
          const newTotal = Math.max(0, t - 1);
          setHasMore(next.length < newTotal);
          return newTotal;
        });
        return next;
      });
      toast.success('Memory deleted');
    } catch (err) {
      toast.error('Failed to delete memory');
    } finally {
      setDeletingId(null);
    }
  };

  if (!user) {
    return (
      <AppLayout pageTitle="My memories">
        <PageTitle title="My memories | Syrabit.ai" />
        <div className="w-full max-w-2xl mx-auto px-4 md:px-6 py-10 text-center">
          <p className="text-muted-foreground mb-4">
            Sign in to see what Syra has saved about you.
          </p>
          <button
            onClick={() => navigate('/login')}
            className="px-4 py-2 rounded-xl bg-primary text-primary-foreground font-semibold"
          >
            Sign in
          </button>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout pageTitle="My memories">
      <PageTitle title="My memories | Syrabit.ai" />

      <div className="flex flex-col h-full overflow-y-auto">
        <div className="w-full max-w-3xl mx-auto px-4 md:px-6 py-5 space-y-4">
          <div>
            <button
              onClick={() => navigate('/profile')}
              className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-2"
            >
              <ChevronLeft size={14} /> Back to profile
            </button>
            <h1 className="text-xl font-bold text-foreground">My memories</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Things Syra has remembered from your chats and flashcards.
              Delete anything you'd rather it forget.
            </p>
            {total > 0 && (
              <div className="mt-3">
                <button
                  type="button"
                  onClick={() => { setForgetText(''); setShowForgetAll(true); }}
                  data-testid="forget-all-memories"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-destructive border border-destructive/30 hover:bg-destructive/10 transition-colors"
                >
                  <Trash2 size={13} /> Forget everything
                </button>
              </div>
            )}
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-10 text-muted-foreground">
              <Loader2 size={18} className="animate-spin mr-2" /> Loading…
            </div>
          ) : items.length === 0 ? (
            <div
              className="rounded-2xl p-6 text-center text-sm text-muted-foreground"
              style={{
                background: 'hsl(var(--card))',
                border: '1px dashed hsl(var(--border))',
              }}
              data-testid="memories-empty"
            >
              Syra hasn't saved any memories about you yet. Chat with it
              or confirm flashcards and they'll show up here.
            </div>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                Showing {items.length} of {total}
              </p>
              <div className="space-y-3">
                {items.map((m) => (
                  <MemoryCard
                    key={m.id}
                    memory={m}
                    onDelete={handleDelete}
                    deleting={deletingId === m.id}
                  />
                ))}
              </div>
              {hasMore && (
                <div className="flex justify-center pt-2">
                  <button
                    onClick={() => load(offset, true)}
                    disabled={loadingMore}
                    className="px-4 py-2 rounded-xl text-sm font-semibold border border-border hover:bg-muted disabled:opacity-50"
                  >
                    {loadingMore ? (
                      <span className="inline-flex items-center gap-2">
                        <Loader2 size={14} className="animate-spin" /> Loading…
                      </span>
                    ) : (
                      'Load more'
                    )}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {pendingDelete && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(0,0,0,0.5)' }}
          onClick={() => setPendingDelete(null)}
        >
          <div
            className="w-full max-w-sm rounded-2xl p-5"
            style={{
              background: 'hsl(var(--card))',
              border: '1px solid hsl(var(--border))',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-base font-semibold text-foreground mb-1">
              Delete this memory?
            </h2>
            <p className="text-sm text-muted-foreground mb-4">
              Syra will forget this entry. This can't be undone.
            </p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setPendingDelete(null)}
                className="px-3 py-1.5 rounded-xl text-sm border border-border hover:bg-muted"
              >
                Cancel
              </button>
              <button
                onClick={confirmDelete}
                className="px-3 py-1.5 rounded-xl text-sm font-semibold bg-destructive text-destructive-foreground hover:opacity-90"
                data-testid="confirm-delete-memory"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {showForgetAll && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(0,0,0,0.6)' }}
          onClick={() => { if (!forgetting) { setShowForgetAll(false); setForgetText(''); } }}
        >
          <div
            className="w-full max-w-sm rounded-2xl p-5 space-y-4"
            style={{
              background: 'hsl(var(--card))',
              border: '1px solid rgba(239,68,68,0.25)',
            }}
            onClick={(e) => e.stopPropagation()}
            data-testid="forget-all-dialog"
          >
            <div className="flex items-center gap-3">
              <div
                className="w-10 h-10 rounded-xl flex items-center justify-center"
                style={{ background: 'rgba(239,68,68,0.10)', border: '1px solid rgba(239,68,68,0.20)' }}
              >
                <AlertTriangle size={18} className="text-red-600" />
              </div>
              <div>
                <h3 className="font-semibold text-foreground">Forget everything?</h3>
                <p className="text-xs text-muted-foreground">
                  Permanently deletes all {total} saved {total === 1 ? 'memory' : 'memories'}
                </p>
              </div>
            </div>

            <p className="text-xs text-muted-foreground">
              Syra will lose every fact and chat takeaway it has remembered
              about you. This can't be undone.
            </p>

            <div>
              <label className="text-xs text-muted-foreground mb-1.5 block">
                Type <span className="font-mono font-bold text-red-600">FORGET</span> to confirm
              </label>
              <input
                type="text"
                value={forgetText}
                onChange={(e) => setForgetText(e.target.value)}
                placeholder="FORGET"
                autoFocus
                data-testid="forget-all-input"
                className="w-full h-10 px-3 rounded-xl text-sm text-foreground outline-none"
                style={{ background: 'hsl(var(--input))', border: '1px solid rgba(239,68,68,0.30)' }}
              />
            </div>

            <div className="flex gap-2">
              <button
                onClick={() => { setShowForgetAll(false); setForgetText(''); }}
                disabled={forgetting}
                className="flex-1 h-9 rounded-xl text-sm font-medium text-muted-foreground border border-border hover:bg-accent/40 transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={confirmForgetAll}
                disabled={forgetText !== 'FORGET' || forgetting}
                data-testid="confirm-forget-all"
                className="flex-1 h-9 rounded-xl text-sm font-semibold text-white flex items-center justify-center gap-1.5 transition-all disabled:opacity-40"
                style={{ background: 'linear-gradient(135deg,#dc2626,#ef4444)' }}
              >
                {forgetting ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                Forget all
              </button>
            </div>
          </div>
        </div>
      )}
    </AppLayout>
  );
}
