import React from 'react';

// Renders the per-reason chip row for review_prompt_reason_ctr_drop alerts.
// Extracted from AdminDashboard so the rendering can be unit-tested without
// having to mount the entire admin dashboard tree.
export default function AlertReasonsRow({ alert, alertReasonFilter, onReasonClick }) {
  if (!alert || alert.type !== 'review_prompt_reason_ctr_drop') return null;
  const reasons = Array.isArray(alert?.threshold_snapshot?.reasons)
    ? alert.threshold_snapshot.reasons
    : [];
  if (reasons.length === 0) return null;

  return (
    <div className="flex items-center gap-1.5 mt-1 flex-wrap" data-testid="alert-reasons-row">
      <span className={`text-[10px] font-medium ${alert.acknowledged ? 'text-gray-400' : 'text-gray-500'}`}>Reasons:</span>
      {reasons.map((r, idx) => {
        const rawName = (r && typeof r === 'object') ? (r.reason ?? '') : String(r ?? '');
        const reasonName = rawName || '';
        const displayName = reasonName || 'unknown';
        const deltaPp = (r && typeof r === 'object' && r.delta_pp != null) ? Number(r.delta_pp) : null;
        const title = deltaPp != null
          ? `${displayName}: ${r.prev_ctr_pct ?? '?'}% → ${r.curr_ctr_pct ?? '?'}% (${deltaPp >= 0 ? '+' : ''}${deltaPp.toFixed(1)} pp)`
          : displayName;
        const isActive = !!reasonName && alertReasonFilter === reasonName;
        const clickable = !!reasonName;
        return (
          <button
            type="button"
            key={`${alert._id}-reason-${idx}`}
            title={clickable ? `${title} — click to ${isActive ? 'clear' : 'filter by this reason'}` : title}
            disabled={!clickable}
            onClick={() => clickable && onReasonClick && onReasonClick(isActive ? '' : reasonName)}
            className={`text-[10px] px-1.5 py-0.5 rounded font-medium border transition-colors ${clickable ? 'cursor-pointer' : 'cursor-default'} ${
              isActive
                ? 'bg-violet-100 border-violet-300 text-violet-800 ring-1 ring-violet-300'
                : alert.acknowledged
                  ? 'bg-gray-100 border-gray-200 text-gray-400 hover:bg-gray-200'
                  : 'bg-red-100 border-red-200 text-red-700 hover:bg-red-200'
            }`}
          >
            {displayName}
            {deltaPp != null && (
              <span className={`ml-1 ${isActive ? 'text-violet-600' : alert.acknowledged ? 'text-gray-400' : 'text-red-500'}`}>
                ({deltaPp >= 0 ? '+' : ''}{deltaPp.toFixed(1)} pp)
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
