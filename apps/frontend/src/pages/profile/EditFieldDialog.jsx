import { Save, Loader2 } from 'lucide-react';
import ModalOverlay from '@/components/ui/ModalOverlay';

export default function EditFieldDialog({
  editField, editValue, setEditValue,
  editLoading, editInputRef,
  handleSaveField, setEditField,
}) {
  if (!editField) return null;
  return (
    <ModalOverlay
      open={!!editField}
      onClose={() => setEditField(null)}
      title={`Edit ${editField.label}`}
      backdropOpacity="0.6"
    >
      <input
        ref={editInputRef}
        type="text"
        value={editValue}
        onChange={(e) => setEditValue(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') handleSaveField(); if (e.key === 'Escape') setEditField(null); }}
        placeholder={editField.placeholder}
        className="w-full h-10 px-3 rounded-xl text-sm text-foreground outline-none"
        style={{ background: 'hsl(var(--input))', border: '1px solid rgba(139,92,246,0.20)' }}
      />
      <div className="flex gap-2">
        <button onClick={() => setEditField(null)}
          className="flex-1 h-9 rounded-xl text-sm font-medium text-muted-foreground border border-border hover:bg-accent/40 transition-colors">
          Cancel
        </button>
        <button
          onClick={handleSaveField}
          disabled={editLoading || !editValue.trim()}
          className="flex-1 h-9 rounded-xl text-sm font-semibold text-white flex items-center justify-center gap-1.5 transition-all hover:opacity-90 disabled:opacity-50"
          style={{ background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)' }}
          data-testid="edit-field-save-button"
        >
          {editLoading ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
          Save
        </button>
      </div>
    </ModalOverlay>
  );
}
