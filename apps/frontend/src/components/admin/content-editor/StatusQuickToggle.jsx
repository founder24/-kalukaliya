import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Check, Loader2, CircleDot } from 'lucide-react';
import { normalizeStatus } from './StatusBadge';

const STATUS_OPTIONS = [
  { value: 'published',   label: 'Published',   dot: '#10b981' },
  { value: 'draft',       label: 'Draft',       dot: '#f59e0b' },
  { value: 'unpublished', label: 'Unpublished', dot: '#6b7280' },
];

const TRIGGER_STYLES = {
  published:   { bg: 'rgba(16,185,129,0.10)', color: '#047857', border: 'rgba(16,185,129,0.30)' },
  draft:       { bg: 'rgba(245,158,11,0.15)', color: '#b45309', border: 'rgba(245,158,11,0.30)' },
  unpublished: { bg: 'rgba(107,114,128,0.15)', color: '#4b5563', border: 'rgba(107,114,128,0.30)' },
  archived:    { bg: 'rgba(239,68,68,0.12)',  color: '#b91c1c', border: 'rgba(239,68,68,0.25)' },
};

export default function StatusQuickToggle({ status, onChange, size = 'sm', testIdPrefix = 'status-toggle' }) {
  const current = normalizeStatus(status);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const style = TRIGGER_STYLES[current] || TRIGGER_STYLES.unpublished;
  const label = STATUS_OPTIONS.find(o => o.value === current)?.label
    || (current ? current.charAt(0).toUpperCase() + current.slice(1) : 'Status');
  const padding = size === 'xs' ? 'h-5 px-1.5 text-[9px]' : 'h-6 px-2 text-[10px]';

  const handleSelect = async (next) => {
    setOpen(false);
    if (next === current) return;
    setSaving(true);
    try {
      await onChange(next);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div ref={ref} className="relative inline-block" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen(o => !o); }}
        disabled={saving}
        className={`inline-flex items-center gap-1 rounded font-bold uppercase tracking-wide border disabled:opacity-50 transition ${padding}`}
        style={{ background: style.bg, color: style.color, borderColor: style.border }}
        title={`Status: ${label} — click to change`}
        data-testid={`${testIdPrefix}-trigger`}
      >
        {saving
          ? <Loader2 size={10} className="animate-spin" />
          : <CircleDot size={9} />}
        <span>{label}</span>
        <ChevronDown size={9} />
      </button>
      {open && (
        <div
          className="absolute right-0 z-50 mt-1 w-36 rounded-lg border border-gray-200 bg-white shadow-lg py-1"
          data-testid={`${testIdPrefix}-menu`}
        >
          {STATUS_OPTIONS.map(opt => (
            <button
              key={opt.value}
              type="button"
              onClick={(e) => { e.stopPropagation(); handleSelect(opt.value); }}
              className="w-full flex items-center justify-between gap-2 px-2.5 py-1.5 text-[11px] text-gray-700 hover:bg-gray-50"
              data-testid={`${testIdPrefix}-option-${opt.value}`}
            >
              <span className="flex items-center gap-2">
                <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: opt.dot }} />
                {opt.label}
              </span>
              {current === opt.value && <Check size={11} className="text-violet-600" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
