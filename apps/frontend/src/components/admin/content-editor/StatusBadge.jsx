const STATUS_STYLES = {
  draft: { bg: 'rgba(245,158,11,0.15)', color: '#b45309', border: 'rgba(245,158,11,0.30)', label: 'Draft' },
  unpublished: { bg: 'rgba(107,114,128,0.15)', color: '#4b5563', border: 'rgba(107,114,128,0.30)', label: 'Unpublished' },
  archived: { bg: 'rgba(239,68,68,0.12)', color: '#b91c1c', border: 'rgba(239,68,68,0.25)', label: 'Archived' },
};

export default function StatusBadge({ status, size = 'sm', className = '' }) {
  const key = (status || '').toString().trim().toLowerCase();
  if (!key || key === 'published') return null;
  const style = STATUS_STYLES[key] || { bg: 'rgba(107,114,128,0.15)', color: '#4b5563', border: 'rgba(107,114,128,0.30)', label: status };
  const padding = size === 'xs' ? 'px-1 py-px text-[8px]' : 'px-1.5 py-0.5 text-[9px]';
  return (
    <span
      title={`Status: ${style.label}`}
      className={`inline-flex items-center rounded font-bold uppercase tracking-wide ${padding} ${className}`}
      style={{ background: style.bg, color: style.color, border: `1px solid ${style.border}` }}
    >
      {style.label}
    </span>
  );
}

export const STATUS_FILTER_OPTIONS = [
  { value: 'all', label: 'All statuses' },
  { value: 'published', label: 'Published' },
  { value: 'draft', label: 'Draft' },
  { value: 'unpublished', label: 'Unpublished' },
  { value: 'archived', label: 'Archived' },
];

export function normalizeStatus(s) {
  return (s || 'published').toString().toLowerCase();
}
