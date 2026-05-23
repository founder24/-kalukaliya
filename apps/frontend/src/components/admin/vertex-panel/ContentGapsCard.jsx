import { useState } from 'react';
import { FileSearch, Loader2, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { vertexContentGaps } from '@/utils/api';
import { card, btn, Badge } from './shared';

export default function ContentGapsCard({ token }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    setLoading(true);
    try {
      const r = await vertexContentGaps(token);
      setData(r.data);
    } catch {
      toast.error('Content gap analysis failed');
    } finally { setLoading(false); }
  }

  const priorityColor = (p) => p === 'high' ? '#ef4444' : p === 'medium' ? '#f59e0b' : '#64748b';

  return (
    <div style={card}>
      <div className="flex items-center gap-2 mb-4">
        <FileSearch size={16} color="#ef4444" />
        <span style={{ fontWeight: 700, color: '#111827' }}>Content Gap Finder</span>
        <Badge label="Search vs Published" color="#ef4444" />
      </div>
      <p style={{ fontSize: 12, color: '#6b7280', marginBottom: 12 }}>
        Cross-references your published pages with actual student search queries to find high-value missing content.
      </p>
      <button onClick={run} disabled={loading} style={btn('#ef4444')}>
        {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
        Analyse Gaps
      </button>
      {data && (
        <div style={{ marginTop: 14 }}>
          <div className="flex gap-4 mb-4">
            <div style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', borderRadius: 10, padding: '8px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 22, fontWeight: 900, color: '#ef4444' }}>{data.gaps?.length || 0}</div>
              <div style={{ fontSize: 10, color: '#6b7280' }}>Gaps Found</div>
            </div>
            <div style={{ background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.2)', borderRadius: 10, padding: '8px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 22, fontWeight: 900, color: '#10b981' }}>{data.published_count}</div>
              <div style={{ fontSize: 10, color: '#6b7280' }}>Published Pages</div>
            </div>
            <div style={{ background: 'rgba(139,92,246,0.08)', border: '1px solid rgba(139,92,246,0.2)', borderRadius: 10, padding: '8px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 22, fontWeight: 900, color: '#8b5cf6' }}>{data.search_queries_analyzed}</div>
              <div style={{ fontSize: 10, color: '#6b7280' }}>Queries Analyzed</div>
            </div>
          </div>
          {data.gaps?.map((gap, i) => (
            <div key={i} style={{ padding: '10px 14px', borderBottom: '1px solid #f3f4f6', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
              <Badge label={gap.priority} color={priorityColor(gap.priority)} />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#111827' }}>{gap.query}</div>
                <div style={{ fontSize: 11, color: '#6b7280', marginTop: 2 }}>{gap.suggested_action}</div>
              </div>
              <div style={{ textAlign: 'right', flexShrink: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: '#ef4444' }}>~{gap.estimated_monthly_searches?.toLocaleString()}</div>
                <div style={{ fontSize: 10, color: '#9ca3af' }}>searches/mo</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
