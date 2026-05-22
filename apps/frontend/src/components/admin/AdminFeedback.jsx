import { useState, useEffect } from 'react';
import { ThumbsUp, ThumbsDown, MessageSquare, Loader2, RefreshCw } from 'lucide-react';
import { adminGetChatFeedback, adminGetFeedbackStats } from '@/utils/api';
import { formatDistanceToNow } from 'date-fns';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
export default function AdminFeedback({ adminToken }) {
  const [feedback, setFeedback] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');

  const load = async () => {
    setLoading(true);
    try {
      const [fbRes, stRes] = await Promise.all([
        adminGetChatFeedback(adminToken),
        adminGetFeedbackStats(adminToken),
      ]);
      setFeedback(Array.isArray(fbRes?.data) ? fbRes.data : []);
      setStats(stRes?.data && typeof stRes.data === 'object' ? stRes.data : null);
    } catch (e) {
      console.error('Failed to load feedback', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const filtered = feedback.filter(f => {
    if (filter === 'likes') return f.reaction === 'like';
    if (filter === 'dislikes') return f.reaction === 'dislike';
    if (filter === 'comments') return f.comment;
    return true;
  });

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
                <div style={{ fontSize: 28, fontWeight: 800, color: s.color }}>{s.value}</div>
                <div style={{ fontSize: 11, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.05em', marginTop: 4 }}>{s.label}</div>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
          {['all', 'likes', 'dislikes', 'comments'].map(f => (
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
        ) : filtered.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40, color: '#9ca3af', fontSize: 14 }}>No feedback yet</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {filtered.map(f => (
              <div key={f.id} style={{ background: '#ffffff', border: '1px solid #e5e7eb', borderRadius: 12, padding: 14, boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {f.reaction === 'like' && <ThumbsUp size={14} style={{ color: '#10b981' }} fill="#10b981" />}
                    {f.reaction === 'dislike' && <ThumbsDown size={14} style={{ color: '#ef4444' }} fill="#ef4444" />}
                    {f.comment && !f.reaction && <MessageSquare size={14} style={{ color: '#3b82f6' }} />}
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>
                      {f.user_name || f.user_email || 'Anonymous'}
                    </span>
                  </div>
                  <span style={{ fontSize: 11, color: '#9ca3af' }}>
                    {f.created_at ? formatDistanceToNow(new Date(f.created_at), { addSuffix: true }) : ''}
                  </span>
                </div>
                {f.comment && (
                  <div style={{ fontSize: 13, color: '#374151', marginBottom: 6, padding: '6px 10px', background: '#eff6ff', borderRadius: 8, borderLeft: '3px solid #3b82f6' }}>
                    {f.comment}
                  </div>
                )}
                {f.message_preview && (
                  <div style={{ fontSize: 11, color: '#9ca3af', lineHeight: 1.4 }}>
                    <span style={{ color: '#6b7280', fontWeight: 600 }}>AI response: </span>
                    {f.message_preview.slice(0, 150)}{f.message_preview.length > 150 ? '…' : ''}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </SectionErrorBoundary>
  );
}
