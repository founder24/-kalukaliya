import { AlertTriangle, Loader2, Trash2 } from 'lucide-react';
import ModalOverlay from '@/components/ui/ModalOverlay';

export default function DeleteConfirmDialog({
  showDeleteConfirm, deleteText, setDeleteText,
  deleting, handleDeleteAccount, setShowDeleteConfirm,
}) {
  if (!showDeleteConfirm) return null;
  return (
    <ModalOverlay
      open={showDeleteConfirm}
      onClose={() => { setShowDeleteConfirm(false); setDeleteText(''); }}
      borderColor="rgba(239,68,68,0.25)"
      backdropOpacity="0.7"
      showCloseButton={false}
      header={
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center"
            style={{ background: 'rgba(239,68,68,0.10)', border: '1px solid rgba(239,68,68,0.20)' }}>
            <AlertTriangle size={18} className="text-red-600" />
          </div>
          <div>
            <h3 className="font-semibold text-foreground">Delete Account?</h3>
            <p className="text-xs text-muted-foreground">This cannot be undone after 72 hours</p>
          </div>
        </div>
      }
    >
      <div className="rounded-xl p-3" style={{ background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.15)' }}>
        <p className="text-xs text-amber-700 font-medium">72-hour grace period</p>
        <p className="text-xs text-muted-foreground/70 mt-0.5">
          You can cancel deletion within 72 hours. After that, all data is permanently erased.
        </p>
      </div>

      <div className="space-y-1.5">
        {['Your profile and credentials', 'All chat conversations', 'Saved subjects', 'Credits and plan'].map((item) => (
          <div key={item} className="flex items-center gap-2 text-xs text-muted-foreground/70">
            <div className="w-1.5 h-1.5 rounded-full bg-red-400/60" />
            {item}
          </div>
        ))}
      </div>

      <div>
        <label className="text-xs text-muted-foreground mb-1.5 block">
          Type <span className="font-mono font-bold text-red-600">DELETE</span> to confirm
        </label>
        <input
          type="text"
          value={deleteText}
          onChange={(e) => setDeleteText(e.target.value)}
          placeholder="DELETE"
          className="w-full h-10 px-3 rounded-xl text-sm text-foreground outline-none"
          style={{ background: 'hsl(var(--input))', border: '1px solid rgba(239,68,68,0.30)' }}
        />
      </div>

      <div className="flex gap-2">
        <button onClick={() => { setShowDeleteConfirm(false); setDeleteText(''); }}
          className="flex-1 h-9 rounded-xl text-sm font-medium text-muted-foreground border border-border hover:bg-accent/40 transition-colors">
          Cancel
        </button>
        <button
          onClick={handleDeleteAccount}
          disabled={deleteText !== 'DELETE' || deleting}
          className="flex-1 h-9 rounded-xl text-sm font-semibold text-white flex items-center justify-center gap-1.5 transition-all disabled:opacity-40"
          style={{ background: 'linear-gradient(135deg,#dc2626,#ef4444)' }}
          data-testid="confirm-delete-button"
        >
          {deleting ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
          Schedule Deletion
        </button>
      </div>
    </ModalOverlay>
  );
}
