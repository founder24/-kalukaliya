import { Loader2, RefreshCw, AlertTriangle } from 'lucide-react';
import { toast } from 'sonner';
import {
  adminSeoUpdatePageStatus, adminSeoFlagLowQuality,
  adminSeoBulkReviewAction,
} from '@/utils/api';

export default function ReviewTab({
  adminToken, reviewQueue, setReviewQueue, reviewLoading,
  reviewSelected, setReviewSelected, flagging, setFlagging,
  bulkThreshold, setBulkThreshold, loadReviewQueue,
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h3 className="text-sm font-semibold" style={{ color: '#111827' }}>
          Draft Pages for Review ({reviewQueue.length})
        </h3>
        <div className="flex gap-2 items-center flex-wrap">
          <button onClick={async () => {
            setFlagging(true);
            try {
              const res = await adminSeoFlagLowQuality(adminToken);
              toast.success(`Rescored ${res.data.rescored} pages, flagged ${res.data.flagged_as_draft} as draft`);
              loadReviewQueue();
            } catch { toast.error('Flag operation failed'); }
            finally { setFlagging(false); }
          }}
            className="h-8 px-3 rounded-lg text-xs font-medium flex items-center gap-1.5"
            style={{ background: 'rgba(239,68,68,0.12)', color: '#f87171', border: '1px solid rgba(239,68,68,0.25)' }}
            disabled={flagging}>
            {flagging ? <Loader2 size={12} className="animate-spin" /> : <AlertTriangle size={12} />}
            Flag Low-Quality Published
          </button>
          <div className="flex items-center gap-1">
            <span className="text-[10px]" style={{ color: '#6b7280' }}>Bulk approve score ≥</span>
            <input type="number" value={bulkThreshold} onChange={e => setBulkThreshold(Number(e.target.value))}
              className="w-12 h-7 text-center rounded text-xs"
              style={{ background: '#e5e7eb', border: '1px solid #e5e7eb', color: '#111827' }}
            />
            <button onClick={async () => {
              if (!confirm(`Publish all draft pages scoring ${bulkThreshold}+?`)) return;
              try {
                const res = await adminSeoBulkReviewAction(adminToken, 'publish', [], bulkThreshold);
                toast.success(`Published ${res.data.modified} pages`);
                loadReviewQueue();
              } catch { toast.error('Bulk approve failed'); }
            }}
              className="h-7 px-2 rounded text-[10px] font-semibold"
              style={{ background: 'rgba(52,211,153,0.15)', color: '#34d399', border: '1px solid rgba(52,211,153,0.25)' }}>
              Apply
            </button>
          </div>
          <button onClick={loadReviewQueue} className="h-7 w-7 rounded flex items-center justify-center"
            style={{ background: '#f9fafb', border: '1px solid #e5e7eb' }}>
            <RefreshCw size={11} style={{ color: '#6b7280' }} />
          </button>
        </div>
      </div>

      {reviewLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="animate-spin" size={20} style={{ color: '#7c3aed' }} />
        </div>
      ) : reviewQueue.length === 0 ? (
        <div className="text-center py-12 text-sm" style={{ color: '#9ca3af' }}>
          No pages in review queue — all pages either published or rejected
        </div>
      ) : (
        <div className="space-y-1.5">
          {reviewQueue.map(p => {
            const qs = p.quality_score || {};
            const score = qs.score ?? 0;
            const tierColor = score >= 70 ? '#34d399' : score >= 50 ? '#fbbf24' : '#f87171';
            const selected = reviewSelected.has(p.id);
            return (
              <div key={p.id} className="rounded-lg p-3 flex items-center gap-3"
                style={{ background: selected ? 'rgba(124,58,237,0.08)' : '#f9fafb', border: `1px solid ${selected ? 'rgba(124,58,237,0.30)' : '#e5e7eb'}` }}>
                <input type="checkbox" checked={selected} onChange={() => {
                  const s = new Set(reviewSelected);
                  s.has(p.id) ? s.delete(p.id) : s.add(p.id);
                  setReviewSelected(s);
                }} />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium truncate" style={{ color: '#111827' }}>{p.topic_title || p.title}</p>
                  <p className="text-[10px] mt-0.5" style={{ color: '#9ca3af' }}>
                    {p.board_name} · {p.class_name} · {p.subject_name} · {p.page_type}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <div className="text-center">
                    <span className="text-sm font-bold" style={{ color: tierColor }}>{score}</span>
                    <p className="text-[9px]" style={{ color: '#9ca3af' }}>{qs.word_count ?? '?'}w</p>
                  </div>
                  <div className="flex gap-1">
                    {qs.anchored && <span className="text-[8px] px-1 rounded" style={{ background: 'rgba(52,211,153,0.15)', color: '#34d399' }}>anchored</span>}
                    {(qs.sections_ratio ?? 0) >= 0.8 && <span className="text-[8px] px-1 rounded" style={{ background: 'rgba(59,130,246,0.15)', color: '#60a5fa' }}>sections</span>}
                  </div>
                  <button onClick={async () => {
                    try {
                      await adminSeoUpdatePageStatus(adminToken, p.id, 'published');
                      toast.success('Published');
                      setReviewQueue(q => q.filter(x => x.id !== p.id));
                    } catch { toast.error('Failed'); }
                  }}
                    className="h-6 px-2 rounded text-[10px] font-semibold"
                    style={{ background: 'rgba(52,211,153,0.15)', color: '#34d399' }}>
                    Approve
                  </button>
                  <button onClick={async () => {
                    try {
                      await adminSeoUpdatePageStatus(adminToken, p.id, 'rejected');
                      toast.success('Rejected');
                      setReviewQueue(q => q.filter(x => x.id !== p.id));
                    } catch { toast.error('Failed'); }
                  }}
                    className="h-6 px-2 rounded text-[10px] font-semibold"
                    style={{ background: 'rgba(239,68,68,0.12)', color: '#f87171' }}>
                    Reject
                  </button>
                </div>
              </div>
            );
          })}
          {reviewSelected.size > 0 && (
            <div className="flex gap-2 pt-2">
              <button onClick={async () => {
                try {
                  const ids = [...reviewSelected];
                  await adminSeoBulkReviewAction(adminToken, 'publish', ids);
                  toast.success(`Published ${ids.length} pages`);
                  setReviewQueue(q => q.filter(x => !reviewSelected.has(x.id)));
                  setReviewSelected(new Set());
                } catch { toast.error('Bulk publish failed'); }
              }}
                className="h-8 px-3 rounded-lg text-xs font-semibold"
                style={{ background: 'rgba(52,211,153,0.15)', color: '#34d399', border: '1px solid rgba(52,211,153,0.25)' }}>
                Approve {reviewSelected.size} Selected
              </button>
              <button onClick={async () => {
                try {
                  const ids = [...reviewSelected];
                  await adminSeoBulkReviewAction(adminToken, 'reject', ids);
                  toast.success(`Rejected ${ids.length} pages`);
                  setReviewQueue(q => q.filter(x => !reviewSelected.has(x.id)));
                  setReviewSelected(new Set());
                } catch { toast.error('Bulk reject failed'); }
              }}
                className="h-8 px-3 rounded-lg text-xs font-semibold"
                style={{ background: 'rgba(239,68,68,0.12)', color: '#f87171', border: '1px solid rgba(239,68,68,0.25)' }}>
                Reject {reviewSelected.size} Selected
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
