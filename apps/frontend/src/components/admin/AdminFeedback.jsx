import { useState, useEffect, useCallback } from 'react';
import { ThumbsUp, ThumbsDown, MessageSquare, Loader2, RefreshCw, Archive, ArchiveRestore } from 'lucide-react';
import { adminGetChatFeedback, adminGetFeedbackStats, adminPatchFeedback } from '@/utils/api';
import { formatDistanceToNow } from 'date-fns';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';

export default function AdminFeedback({ adminToken }) {
  const [feedback, setFeedback] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('all');
  const [archiving, setArchiving] = useState({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const apiFilter = filter === 'archived' ? 'archived' : filter === 'unread' ? 'unread' : null;
      const [fbRes, stRes] = await Promise.all([
        adminGetChatFeedback(adminToken, 100, 0, apiFilter),
        adminGetFeedbackStats(adminToken),
      ]);
      setFeedback(Array.isArray(fbRes?.data?.data) ? fbRes.data.data : []);
      setStats(stRes?.data && typeof stRes.data === 'object' ? stRes.data : null);
    } catch (e) {
      console.error('Failed to load feedback', e);
      setError(e?.response?.data?.detail || 'Failed to load feedback. Click Refresh to try again.');
    } finally {
      setLoading(false);
    }
  }, [adminToken, filter]);

  useEffect(() => { load(); }, [load]);

  const handleArchive = useCallback(async (f) => {
    const action = f.archived ? 'unarchive' : 'archive';
    setArchiving(prev => ({ ...prev, [f.id]: true }));
    try {
      await adminPatchFeedback(adminToken, f.id, action);
      setFeedback(prev => prev.map(item =>
        item.id === f.id ? { ...item, archived: !item.archived } : item
      ));
    } catch (e) {
      console.error('Archive action failed', e);
    } finally {
      setArchiving(prev => ({ ...prev, [f.id]: false }));
    }
  }, [adminToken]);

  const handleMarkRead = useCallback(async (f) => {
    if (f.read) return;
    setArchiving(prev => ({ ...prev, [`r_${f.id}`]: true }));
    try {
      await adminPatchFeedback(adminToken, f.id, 'read');
      setFeedback(prev => prev.map(item =>
        item.id === f.id ? { ...item, read: true } : item
      ));
    } catch (e) {
      console.error('Mark read failed', e);
    } finally {
      setArchiving(prev => ({ ...prev, [`r_${f.id}`]: false }));
    }
  }, [adminToken]);

  const filtered = feedback.filter(f => {
    if (filter === 'likes') return f.rating === 1;
    if (filter === 'dislikes') return f.rating === -1;
    if (filter === 'unread') return !f.read;
    if (filter === 'archived') return f.archived;
    return !f.archived; // default: hide archived
  });

  const FILTER_TABS = ['all', 'likes', 'dislikes', 'unread', 'archived'];

  return (
    <SectionErrorBoundary name="Feedback">
      <div style={{ padding: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
          <h2 style={{ fontSize: 20, fontWeight: 700, color: '#111827' }}>Chat Feedback</h2>
          <button onClick={load} style={{ background: '#ffffff', border: '1px solid #e5e7eb', borderRadius: 8, padding: '6px 12px', color: '#6b7280', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}>
            <RefreshCw size={14} /> Refresh
          </button>
        </div>

        {stats && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 24 }}>
            {[
              { label: 'Total', value: stats.total, color: '#7c3aed' },
              { label: 'Likes', value: stats.likes, color: '#10b981' },
              { label: 'Dislikes', value: stats.dislikes, color: '#ef4444' },
              { label: 'Comments', value: stats.comments, color: '#3b82f6' },
            ].map(s => (
              <div key={s.label} style={{ background: '#ffffff', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, textAlign: 'center', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}>
                <div style={{ fontSize: 28, fontWeight: 800, color: s.color }}>{s.value ?? 0}</div>
                <div style={{ fontSize: 11, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.05em', marginTop: 4 }}>{s.label}</div>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
          {FILTER_TABS.map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                padding: '6px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                border: filter === f ? '1px solid #c4b5fd' : '1px solid #e5e7eb',
                background: filter === f ? '#f5f3ff' : '#ffffff',
                color: filter === f ? '#7c3aed' : '#6b7280',
                textTransform: 'capitalize',
              }}
            >
              {f}
            </button>
          ))}
        </div>

        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
            <Loader2 size={24} className="animate-spin" style={{ color: '#7c3aed' }} />
          </div>
        ) : error ? (
          <div style={{ textAlign: 'center', padding: 32, background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 12, color: '#b91c1c', fontSize: 13 }}>
            ⚠ {error}
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40, color: '#9ca3af', fontSize: 14 }}>No feedback yet</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {filtered.map(f => (
              <div
                key={f.id}
                onClick={() => handleMarkRead(f)}
                style={{
                  background: f.read ? '#ffffff' : '#fafaf7',
                  border: `1px solid ${f.read ? '#e5e7eb' : '#d1c4a0'}`,
                  borderRadius: 12, padding: 14,
                  boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
                  cursor: f.read ? 'default' : 'pointer',
                  opacity: f.archived ? 0.6 : 1,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {f.rating === 1 && <ThumbsUp size={14} style={{ color: '#10b981' }} fill="#10b981" />}
                    {f.rating === -1 && <ThumbsDown size={14} style={{ color: '#ef4444' }} fill="#ef4444" />}
                    {!f.rating && <MessageSquare size={14} style={{ color: '#3b82f6' }} />}
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>
                      {f.user_id ? f.user_id.slice(0, 8) + '…' : 'Anonymous'}
                    </span>
                    {!f.read && (
                      <span style={{ fontSize: 10, fontWeight: 700, color: '#7c3aed', background: '#f5f3ff', borderRadius: 4, padding: '1px 6px' }}>NEW</span>
                    )}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 11, color: '#9ca3af' }}>
                      {f.timestamp ? formatDistanceToNow(new Date(f.timestamp), { addSuffix: true }) : ''}
                    </span>
                    <button
                      onClick={e => { e.stopPropagation(); handleArchive(f); }}
                      disabled={!!archiving[f.id]}
                      title={f.archived ? 'Unarchive' : 'Archive'}
                      style={{
                        background: 'none', border: '1px solid #e5e7eb', borderRadius: 6,
                        padding: '2px 6px', cursor: 'pointer', display: 'flex', alignItems: 'center',
                        color: f.archived ? '#7c3aed' : '#9ca3af',
                      }}
                    >
                      {archiving[f.id]
                        ? <Loader2 size={12} className="animate-spin" />
                        : f.archived
                          ? <ArchiveRestore size={12} />
                          : <Archive size={12} />
                      }
                    </button>
                  </div>
                </div>
                {f.query_text && (
                  <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 4, fontStyle: 'italic' }}>
                    "{f.query_text.slice(0, 120)}{f.query_text.length > 120 ? '…' : ''}"
                  </div>
                )}
                <div style={{ fontSize: 11, color: '#9ca3af' }}>
                  {f.lang?.toUpperCase()} · {f.model_provider} · {f.latency_ms ? `${f.latency_ms}ms` : '—'}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </SectionErrorBoundary>
  );
}
