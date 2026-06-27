import { CheckCircle, AlertTriangle, Clock, HelpCircle } from 'lucide-react';

function parseDate(v) {
  if (!v) return null;
  return new Date(v);
}

export function getRagSyncState(ragUpdatedAt, ragIndexedAt) {
  const updated = parseDate(ragUpdatedAt);
  const indexed = parseDate(ragIndexedAt);
  if (!updated && !indexed) return 'not_indexed';
  if (!indexed) return 'stale';
  if (!updated) return 'current';
  return indexed >= updated ? 'current' : 'stale';
}

export default function RagSyncBadge({ ragUpdatedAt, ragIndexedAt, isIndexing = false, size = 'sm' }) {
  const state = isIndexing ? 'indexing' : getRagSyncState(ragUpdatedAt, ragIndexedAt);

  const cfg = {
    current:     { bg: 'rgba(16,185,129,0.12)',  color: '#6ee7b7', border: 'rgba(16,185,129,0.25)',  icon: CheckCircle,    label: 'RAG current' },
    stale:       { bg: 'rgba(245,158,11,0.12)',  color: '#fcd34d', border: 'rgba(245,158,11,0.25)',  icon: AlertTriangle,  label: 'RAG stale' },
    indexing:    { bg: 'rgba(139,92,246,0.12)',  color: '#c4b5fd', border: 'rgba(139,92,246,0.25)',  icon: Clock,          label: 'Indexing…' },
    not_indexed: { bg: 'rgba(156,163,175,0.12)', color: '#9ca3af', border: 'rgba(156,163,175,0.20)', icon: HelpCircle,     label: 'Not indexed' },
  }[state];

  const iconSize = size === 'xs' ? 8 : 9;
  const textSize = size === 'xs' ? '8px' : '9px';
  const px = size === 'xs' ? '6px' : '8px';
  const py = size === 'xs' ? '1px' : '2px';

  const Icon = cfg.icon;

  const tooltip = {
    current:     ragIndexedAt ? `Indexed ${new Date(ragIndexedAt).toLocaleString()}` : 'Up to date',
    stale:       ragUpdatedAt ? `RAG text changed ${new Date(ragUpdatedAt).toLocaleString()} — not yet reindexed` : 'Stale',
    indexing:    'Reindex in progress…',
    not_indexed: 'This chapter has never been indexed into Vectorize',
  }[state];

  return (
    <span
      title={tooltip}
      className="flex items-center gap-1 rounded-full font-semibold select-none"
      style={{
        background: cfg.bg,
        color: cfg.color,
        border: `1px solid ${cfg.border}`,
        fontSize: textSize,
        padding: `${py} ${px}`,
        animation: state === 'indexing' ? 'pulse 1.5s ease-in-out infinite' : undefined,
      }}
    >
      <Icon size={iconSize} />
      {cfg.label}
    </span>
  );
}
