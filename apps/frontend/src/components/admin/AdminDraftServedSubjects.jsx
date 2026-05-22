import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { AlertTriangle, CheckCircle, RefreshCw, Loader2 } from 'lucide-react';
import { adminGetDraftServedSubjects, adminPublishSubject } from '@/utils/api';

function formatTimeAgo(dateStr) {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now - date;
  if (diffMs < 0) return 'just now';
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

export default function AdminDraftServedSubjects({ adminToken }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [publishing, setPublishing] = useState({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await adminGetDraftServedSubjects(adminToken);
      setItems(Array.isArray(res?.data?.items) ? res.data.items : []);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => {
    load();
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
  }, [load]);

  const handlePublish = async (subjectId, name) => {
    setPublishing((p) => ({ ...p, [subjectId]: true }));
    try {
      await adminPublishSubject(adminToken, subjectId);
      toast.success(`Published ${name || subjectId}`);
      setItems((prev) => prev.filter((it) => it.id !== subjectId));
    } catch (e) {
      toast.error(e?.response?.data?.detail || e?.message || 'Publish failed');
    } finally {
      setPublishing((p) => {
        const next = { ...p };
        delete next[subjectId];
        return next;
      });
    }
  };

  return (
    <div
      className="relative rounded-2xl bg-white border border-gray-200 shadow-sm p-5"
      data-testid="draft-served-subjects-widget"
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-amber-50">
            <AlertTriangle size={16} className="text-amber-600" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-gray-900">Subjects served as draft</h3>
            <p className="text-xs text-gray-500">
              Live chapter URLs are rendering even though the subject status isn't "published".
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1 disabled:opacity-50"
          aria-label="Refresh draft-served subjects"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {error && (
        <div
          className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg p-3 mb-3"
          data-testid="draft-served-error"
        >
          {error}
        </div>
      )}

      {loading && items.length === 0 ? (
        <div className="flex items-center justify-center py-8 text-gray-400">
          <Loader2 size={18} className="animate-spin mr-2" />
          <span className="text-sm">Loading…</span>
        </div>
      ) : items.length === 0 ? (
        <div
          className="text-center py-8"
          data-testid="draft-served-empty"
        >
          <CheckCircle size={28} className="text-emerald-400 mx-auto mb-3" />
          <p className="text-gray-500 text-sm">All subjects published — nothing to do</p>
        </div>
      ) : (
        <ul className="divide-y divide-gray-100" data-testid="draft-served-list">
          {items.map((it) => (
            <li
              key={it.id}
              className="py-3 flex items-center justify-between gap-4"
              data-testid="draft-served-row"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-gray-900 truncate">
                    {it.name || it.slug || it.id}
                  </span>
                  <span className="text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-100">
                    {it.status || 'no status'}
                  </span>
                </div>
                <p className="text-xs text-gray-500 mt-0.5 truncate">
                  {it.slug ? <span className="font-mono">{it.slug}</span> : null}
                  <span className="mx-1">·</span>
                  served {it.count ?? 0}× · last {formatTimeAgo(it.last_served_at)}
                </p>
              </div>
              <button
                type="button"
                onClick={() => handlePublish(it.id, it.name)}
                disabled={!!publishing[it.id]}
                className="shrink-0 inline-flex items-center gap-1 text-xs font-medium px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-60"
                data-testid="draft-served-publish"
              >
                {publishing[it.id] ? (
                  <>
                    <Loader2 size={12} className="animate-spin" />
                    Publishing…
                  </>
                ) : (
                  <>
                    <CheckCircle size={12} />
                    Publish
                  </>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
