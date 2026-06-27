import { useState, useEffect, useCallback } from 'react';
import { X, Clock, RefreshCw, AlertTriangle, FilePen, Trash2, Plus, Database } from 'lucide-react';
import axios from 'axios';
import { API, authHeaders } from '@/utils/adminHelpers';

function relativeTime(iso) {
  if (!iso) return '';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

function actionMeta(action) {
  switch (action) {
    case 'created':    return { icon: Plus,     color: '#34d399', label: 'Created' };
    case 'updated':    return { icon: FilePen,  color: '#7c3aed', label: 'Updated' };
    case 'deleted':    return { icon: Trash2,   color: '#f87171', label: 'Deleted' };
    case 'rag_updated':return { icon: Database, color: '#3b82f6', label: 'RAG updated' };
    default:           return { icon: Clock,    color: '#9ca3af', label: action };
  }
}

function diffSummary(changes) {
  if (!changes || Object.keys(changes).length === 0) return null;
  const parts = [];
  if (changes.title) parts.push(`title → "${changes.title.after}"`);
  if (changes.status) parts.push(`status ${changes.status.before} → ${changes.status.after}`);
  if (changes.content_en) {
    const d = (changes.content_en.words_after ?? 0) - (changes.content_en.words_before ?? 0);
    parts.push(`content ${d >= 0 ? '+' : ''}${d} words`);
  }
  if (changes.content_as) {
    const d = (changes.content_as.words_after ?? 0) - (changes.content_as.words_before ?? 0);
    parts.push(`Assamese ${d >= 0 ? '+' : ''}${d} words`);
  }
  if (changes.rag_text_en) parts.push('RAG EN updated');
  if (changes.rag_text_as) parts.push('RAG AS updated');
  if (changes.topics) parts.push(`topics set`);
  return parts.join(' · ') || null;
}

export default function ChapterAuditLog({ chapterId, adminToken, onClose }) {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchLog = useCallback(async () => {
    if (!chapterId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(
        `${API}/admin/content/chapters/${chapterId}/audit-log`,
        authHeaders(adminToken),
      );
      setEntries(res.data?.entries || []);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load audit log');
    } finally {
      setLoading(false);
    }
  }, [chapterId, adminToken]);

  useEffect(() => { fetchLog(); }, [fetchLog]);

  return (
    <div className="fixed inset-0 z-[200] flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30 backdrop-blur-[2px]" />
      <div
        className="relative flex flex-col h-full shadow-2xl"
        style={{ width: 360, background: '#fff', borderLeft: '1px solid #e5e7eb' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b" style={{ borderColor: '#f3f4f6' }}>
          <div className="flex items-center gap-2">
            <Clock size={15} style={{ color: '#7c3aed' }} />
            <span className="text-sm font-semibold text-gray-900">Edit History</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={fetchLog}
              disabled={loading}
              className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
              title="Refresh"
            >
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} style={{ color: '#9ca3af' }} />
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
            >
              <X size={15} style={{ color: '#9ca3af' }} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading && entries.length === 0 && (
            <div className="flex items-center justify-center h-32">
              <RefreshCw size={16} className="animate-spin" style={{ color: '#a78bfa' }} />
            </div>
          )}

          {error && (
            <div className="flex items-center gap-2 m-4 px-3 py-2.5 rounded-xl" style={{ background: '#fef2f2', border: '1px solid #fecaca' }}>
              <AlertTriangle size={13} style={{ color: '#f87171' }} />
              <span className="text-[11px] text-red-600">{error}</span>
            </div>
          )}

          {!loading && !error && entries.length === 0 && (
            <div className="flex flex-col items-center justify-center h-40 text-center px-6">
              <Clock size={24} style={{ color: '#e5e7eb', marginBottom: 8 }} />
              <p className="text-[12px] font-medium text-gray-400">No history yet</p>
              <p className="text-[11px] text-gray-300 mt-1">Saves will appear here as a timeline.</p>
            </div>
          )}

          {entries.length > 0 && (
            <div className="px-4 py-4 space-y-0">
              {entries.map((entry, idx) => {
                const meta = actionMeta(entry.action);
                const Icon = meta.icon;
                const summary = diffSummary(entry.changes);
                const isLast = idx === entries.length - 1;

                return (
                  <div key={entry.id || idx} className="flex gap-3">
                    <div className="flex flex-col items-center flex-shrink-0" style={{ width: 24 }}>
                      <div
                        className="flex items-center justify-center rounded-full flex-shrink-0"
                        style={{ width: 24, height: 24, background: `${meta.color}18`, border: `1.5px solid ${meta.color}40` }}
                      >
                        <Icon size={11} style={{ color: meta.color }} />
                      </div>
                      {!isLast && (
                        <div className="flex-1 w-px mt-1" style={{ background: '#f3f4f6', minHeight: 16 }} />
                      )}
                    </div>

                    <div className="pb-4 min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2 mb-0.5">
                        <span className="text-[12px] font-semibold" style={{ color: meta.color }}>
                          {meta.label}
                        </span>
                        <span className="text-[10px] flex-shrink-0" style={{ color: '#9ca3af' }}>
                          {relativeTime(entry.created_at)}
                        </span>
                      </div>

                      <p className="text-[11px] text-gray-500 truncate">
                        {entry.actor_email || entry.actor_id}
                      </p>

                      {summary && (
                        <p className="text-[10px] mt-1 leading-relaxed" style={{ color: '#6b7280' }}>
                          {summary}
                        </p>
                      )}

                      {entry.version_before != null && entry.version_after != null && (
                        <span
                          className="inline-block text-[9px] font-mono mt-1 px-1.5 py-0.5 rounded"
                          style={{ background: '#f9fafb', border: '1px solid #e5e7eb', color: '#9ca3af' }}
                        >
                          v{entry.version_before} → v{entry.version_after}
                        </span>
                      )}

                      {entry.created_at && (
                        <p className="text-[9px] mt-0.5" style={{ color: '#d1d5db' }}>
                          {new Date(entry.created_at).toLocaleString()}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="px-4 py-3 border-t" style={{ borderColor: '#f3f4f6' }}>
          <p className="text-[10px] text-center" style={{ color: '#d1d5db' }}>
            Showing last {entries.length} edit{entries.length !== 1 ? 's' : ''}
          </p>
        </div>
      </div>
    </div>
  );
}
