import { Loader2, CheckCheck, AlertTriangle } from 'lucide-react';

export default function JobProgress({ job, onDismiss }) {
  if (!job) return null;
  const pct = job.total > 0 ? Math.min(100, Math.round((job.done / job.total) * 100)) : 0;
  const isDone = job.status === 'done';
  const isErr  = job.status === 'error';
  const barColor = isErr ? '#f87171' : isDone ? '#34d399' : '#7c3aed';
  return (
    <div className="rounded-xl p-4 border" style={{ background: 'rgba(124,58,237,0.06)', borderColor: 'rgba(124,58,237,0.25)' }}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          {isDone ? <CheckCheck size={14} style={{ color: '#34d399' }} />
           : isErr ? <AlertTriangle size={14} style={{ color: '#f87171' }} />
           : <Loader2 size={14} className="animate-spin" style={{ color: '#a78bfa' }} />}
          <span className="text-xs font-semibold" style={{ color: isDone ? '#34d399' : isErr ? '#f87171' : '#7c3aed' }}>
            {isDone ? 'Pipeline Complete' : isErr ? 'Pipeline Error' : 'Pipeline Running…'}
          </span>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded" style={{ background: '#e5e7eb', color: '#9ca3af' }}>
            {job.job_id}
          </span>
        </div>
        {(isDone || isErr) && (
          <button onClick={onDismiss} className="text-[10px]" style={{ color: '#9ca3af' }}>Dismiss</button>
        )}
      </div>
      <div className="flex items-center gap-3 mb-2">
        <div className="flex-1 h-2 rounded-full" style={{ background: '#e5e7eb' }}>
          <div className="h-2 rounded-full transition-all duration-300" style={{ width: `${isDone ? 100 : pct}%`, background: barColor }} />
        </div>
        <span className="text-[11px] font-mono flex-shrink-0" style={{ color: '#6b7280' }}>
          {isDone ? '100%' : `${pct}%`}
        </span>
      </div>
      <div className="flex items-center gap-4 text-[10px]" style={{ color: '#9ca3af' }}>
        <span>✓ {job.done ?? 0} done</span>
        {job.skipped > 0 && <span>⟳ {job.skipped} skipped</span>}
        {job.errors > 0 && <span style={{ color: '#f87171' }}>✗ {job.errors} errors</span>}
        {job.total > 0 && <span>of {job.total}</span>}
      </div>
      {job.current && (
        <p className="text-[10px] truncate mt-1.5" style={{ color: '#9ca3af' }}>{job.current}</p>
      )}

      {Array.isArray(job.outcomes) && job.outcomes.length > 0 && (
        <div className="mt-3 pt-2 border-t" style={{ borderColor: 'rgba(124,58,237,0.2)' }}>
          <div className="text-[10px] font-semibold mb-1" style={{ color: '#6b7280' }}>
            Recent topic outcomes
          </div>
          <ul className="space-y-0.5">
            {job.outcomes.slice(-5).reverse().map((o, i) => {
              const colour = o.status === 'generated' ? '#34d399'
                : o.status === 'skipped' ? '#9ca3af'
                : '#f87171';
              const glyph = o.status === 'generated' ? '✓'
                : o.status === 'skipped' ? '⟳'
                : '✗';
              return (
                <li key={i} className="text-[10px] truncate" style={{ color: colour }}>
                  <span style={{ color: colour, fontWeight: 700 }}>{glyph}</span>{' '}
                  <span style={{ color: '#374151' }}>{o.topic_title || o.topic_id}</span>
                  {' · '}
                  <span style={{ color: '#9ca3af' }}>{o.page_type}</span>
                  {o.reason && <span style={{ color: '#9ca3af' }}> — {o.reason}</span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {(job.avg_seo_score || job.avg_geo_score) && (
        <div className="mt-2 text-[10px]" style={{ color: '#6b7280' }}>
          <span style={{ fontWeight: 600 }}>Run averages: </span>
          SEO {job.avg_seo_score ?? 0}/100 · GEO {job.avg_geo_score ?? 0}/100
        </div>
      )}

      {job.reasons && Object.keys(job.reasons).length > 0 && (
        <div className="mt-2 text-[10px]" style={{ color: '#9ca3af' }}>
          <span style={{ fontWeight: 600 }}>Skip/error reasons: </span>
          {Object.entries(job.reasons).map(([r, n], i) => (
            <span key={r}>{i ? ' · ' : ''}{r} ({n})</span>
          ))}
        </div>
      )}
    </div>
  );
}
