import { useEffect, useRef } from 'react';
import { AlertTriangle } from 'lucide-react';

export default function ConfirmDialog({ open, title, message, confirmLabel = 'Delete', onConfirm, onCancel, destructive = true }) {
  const confirmRef = useRef(null);

  useEffect(() => {
    if (open && confirmRef.current) confirmRef.current.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center" onClick={onCancel}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="relative rounded-2xl shadow-2xl p-6 max-w-sm w-full mx-4" style={{ background: '#0e0e1a', border: '1px solid #e5e7eb' }} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-3 mb-4">
          <div className={`p-2 rounded-xl ${destructive ? 'bg-red-500/10' : 'bg-violet-500/10'}`}>
            <AlertTriangle size={20} className={destructive ? 'text-red-400' : 'text-violet-400'} />
          </div>
          <div>
            <h3 className="text-gray-900 font-semibold text-sm">{title}</h3>
            <p className="text-gray-500 text-xs mt-1 leading-relaxed">{message}</p>
          </div>
        </div>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="h-8 px-4 rounded-lg bg-gray-50 hover:bg-gray-100 text-gray-500 text-xs font-medium transition-colors">Cancel</button>
          <button ref={confirmRef} onClick={onConfirm} className={`h-8 px-4 rounded-lg text-white text-xs font-medium transition-colors ${destructive ? 'bg-red-600 hover:bg-red-500' : 'bg-violet-600 hover:bg-violet-500'}`}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
