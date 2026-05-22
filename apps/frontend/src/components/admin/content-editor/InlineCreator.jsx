import { useState, useEffect, useRef } from 'react';
import { Plus, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

const STATUS_OPTIONS = [
  { value: 'published', label: 'Published' },
  { value: 'draft', label: 'Draft' },
  { value: 'unpublished', label: 'Unpublished' },
  { value: 'archived', label: 'Archived' },
];

export default function InlineCreator({ placeholder, onCreate, icon: Icon, color = 'violet' }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [status, setStatus] = useState('published');
  const [saving, setSaving] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => { if (open && inputRef.current) inputRef.current.focus(); }, [open]);

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className={`w-full p-3 rounded-xl border-2 border-dashed border-gray-200 hover:border-${color}-500/40 text-gray-400 hover:text-${color}-400 flex items-center gap-2 text-sm transition-colors`} data-testid={`add-${placeholder.toLowerCase()}`}>
        <Plus size={16} /> Add {placeholder}
      </button>
    );
  }

  const submit = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await onCreate(name.trim(), desc.trim(), status);
      setName(''); setDesc(''); setStatus('published'); setOpen(false);
    } catch (e) {
      toast.error(e.response?.data?.detail || `Failed to create ${placeholder}`);
    } finally { setSaving(false); }
  };

  return (
    <div className="p-3 rounded-xl border border-gray-200 bg-white space-y-2">
      <div className="flex items-center gap-2">
        {Icon && <Icon size={16} className={`text-${color}-400`} />}
        <input
          ref={inputRef}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder={`${placeholder} name...`}
          className="flex-1 h-9 px-3 rounded-lg text-sm text-gray-900 bg-gray-50 border border-gray-200 outline-none focus:border-violet-500"
        />
      </div>
      <input
        value={desc}
        onChange={(e) => setDesc(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && submit()}
        placeholder="Description (optional)"
        className="w-full h-9 px-3 rounded-lg text-sm text-gray-900 bg-gray-50 border border-gray-200 outline-none focus:border-violet-500"
      />
      <select
        value={status}
        onChange={(e) => setStatus(e.target.value)}
        className="w-full h-9 px-2 rounded-lg text-xs text-gray-700 bg-gray-50 border border-gray-200 outline-none focus:border-violet-500"
        data-testid={`status-select-${placeholder.toLowerCase()}`}
      >
        {STATUS_OPTIONS.map(o => <option key={o.value} value={o.value}>Status: {o.label}</option>)}
      </select>
      <div className="flex gap-2">
        <button onClick={() => { setOpen(false); setName(''); setDesc(''); setStatus('published'); }} className="flex-1 h-8 rounded-lg bg-gray-50 hover:bg-gray-100 text-gray-500 text-xs">Cancel</button>
        <button onClick={submit} disabled={saving || !name.trim()} className={`flex-1 h-8 rounded-lg bg-${color}-600 hover:bg-${color}-500 text-white text-xs font-medium disabled:opacity-40 flex items-center justify-center gap-1`}>
          {saving ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
          {saving ? 'Creating...' : 'Create'}
        </button>
      </div>
    </div>
  );
}
