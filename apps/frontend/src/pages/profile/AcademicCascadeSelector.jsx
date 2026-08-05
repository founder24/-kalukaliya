/**
 * AcademicCascadeSelector
 * Inline cascading Board → Class → Stream → Subject picker for the profile page.
 * Replaces free-text EditFieldDialog for these four fields.
 */
import { useState, useEffect, useCallback } from 'react';
import { Globe, GraduationCap, Layers, BookOpen, ChevronDown, Check, Loader2, Save } from 'lucide-react';
import { getBoards, getClasses, getStreams, getAllSubjects, apiClient } from '@/utils/api';
import { toast } from 'sonner';
import { isDegreeBoard } from '@/utils/courseTypes';

// ── helpers ──────────────────────────────────────────────────────────────────

function ExpandRow({ icon: Icon, label, value, placeholder, open, onToggle, disabled }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      className={`w-full flex items-center gap-3 px-4 py-3.5 transition-colors text-left border-b border-border/50
        ${disabled ? 'opacity-40 cursor-not-allowed' : 'hover:bg-accent/30'}
        ${open ? 'bg-accent/20' : ''}`}
    >
      <div
        className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0"
        style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(139,92,246,0.15)' }}
      >
        <Icon size={14} style={{ color: 'hsl(var(--primary))' }} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className={`text-sm font-medium truncate ${value ? 'text-foreground' : 'text-muted-foreground/60'}`}>
          {value || placeholder}
        </p>
      </div>
      <ChevronDown
        size={14}
        className={`text-muted-foreground/70 flex-shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
      />
    </button>
  );
}

function OptionList({ items, selected, onSelect, loading, emptyLabel = 'No options available' }) {
  if (loading) {
    return (
      <div className="flex justify-center py-5">
        <Loader2 size={18} className="animate-spin text-violet-600" />
      </div>
    );
  }
  if (!items.length) {
    return <p className="text-xs text-muted-foreground py-4 px-1 text-center">{emptyLabel}</p>;
  }
  return (
    <div className="space-y-1 max-h-52 overflow-y-auto pr-0.5">
      {items.map((item) => {
        const isSelected = selected?.id === item.id;
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onSelect(item)}
            className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl border transition-all text-left ${
              isSelected
                ? 'border-violet-500 bg-violet-500/15'
                : 'border-border/40 bg-accent/5 hover:border-border hover:bg-accent/20'
            }`}
          >
            {item.icon && <span className="text-base leading-none">{item.icon}</span>}
            <span className={`text-sm font-medium flex-1 truncate ${isSelected ? 'text-foreground' : 'text-muted-foreground'}`}>
              {item.name}
            </span>
            {isSelected && <Check size={13} className="text-violet-600 flex-shrink-0" />}
          </button>
        );
      })}
    </div>
  );
}

// ── main component ────────────────────────────────────────────────────────────

export default function AcademicCascadeSelector({ profile, onProfileUpdate }) {
  // Current saved values (from profile)
  const savedBoard  = profile?.board_id  ? { id: profile.board_id,  name: profile.board_name  } : null;
  const savedClass  = profile?.class_id  ? { id: profile.class_id,  name: profile.class_name  } : null;
  const savedStream = profile?.stream_id ? { id: profile.stream_id, name: profile.stream_name } : null;

  // Working selections (may differ from saved)
  const [selBoard,  setSelBoard]  = useState(savedBoard);
  const [selClass,  setSelClass]  = useState(savedClass);
  const [selStream, setSelStream] = useState(savedStream);
  const [selSubject, setSelSubject] = useState(
    profile?.saved_subjects?.[0] ? profile.saved_subjects[0] : null
  );

  // Dropdown lists
  const [boards,   setBoards]   = useState([]);
  const [classes,  setClasses]  = useState([]);
  const [streams,  setStreams]  = useState([]);
  const [subjects, setSubjects] = useState([]);

  // Loading states
  const [loadingBoards,   setLoadingBoards]   = useState(false);
  const [loadingClasses,  setLoadingClasses]  = useState(false);
  const [loadingStreams,  setLoadingStreams]   = useState(false);
  const [loadingSubjects, setLoadingSubjects] = useState(false);

  // Which row is expanded
  const [openRow, setOpenRow] = useState(null);

  // Saving state
  const [saving, setSaving] = useState(false);

  // ── board is degree-level? ────────────────────────────────────────────────
  const degreeBoard = isDegreeBoard(selBoard?.name);

  // ── dirty check ──────────────────────────────────────────────────────────
  const isDirty =
    selBoard?.id  !== savedBoard?.id  ||
    selClass?.id  !== savedClass?.id  ||
    selStream?.id !== savedStream?.id;

  // ── fetch boards on mount ─────────────────────────────────────────────────
  useEffect(() => {
    setLoadingBoards(true);
    getBoards()
      .then((r) => setBoards(r.data || []))
      .catch(() => {})
      .finally(() => setLoadingBoards(false));
  }, []);

  // ── fetch classes when board changes ──────────────────────────────────────
  useEffect(() => {
    if (!selBoard) { setClasses([]); return; }
    setLoadingClasses(true);
    getClasses(selBoard.id)
      .then((r) => setClasses(r.data || []))
      .catch(() => {})
      .finally(() => setLoadingClasses(false));
  }, [selBoard?.id]);

  // ── fetch streams when class changes ─────────────────────────────────────
  useEffect(() => {
    if (!selClass) { setStreams([]); return; }
    setLoadingStreams(true);
    getStreams(selClass.id)
      .then((r) => setStreams(r.data || []))
      .catch(() => {})
      .finally(() => setLoadingStreams(false));
  }, [selClass?.id]);

  // ── fetch & filter subjects when stream changes ───────────────────────────
  useEffect(() => {
    if (!selStream) { setSubjects([]); return; }
    setLoadingSubjects(true);
    getAllSubjects()
      .then((r) => {
        const all = r.data || [];
        const filtered = all.filter((s) => s.stream_id === selStream.id);
        setSubjects(filtered);
      })
      .catch(() => {})
      .finally(() => setLoadingSubjects(false));
  }, [selStream?.id]);

  // ── toggle row ────────────────────────────────────────────────────────────
  const toggle = (row) => setOpenRow((prev) => (prev === row ? null : row));

  // ── selection handlers (cascade resets downstream) ────────────────────────
  const pickBoard = (b) => {
    setSelBoard(b);
    setSelClass(null);
    setSelStream(null);
    setSelSubject(null);
    setOpenRow('class');
  };

  const pickClass = (c) => {
    setSelClass(c);
    setSelStream(null);
    setSelSubject(null);
    const newDegree = isDegreeBoard(selBoard?.name);
    setOpenRow(newDegree ? null : 'stream');
  };

  const pickStream = (s) => {
    setSelStream(s);
    setSelSubject(null);
    setOpenRow('subject');
  };

  const pickSubject = (s) => {
    setSelSubject((prev) => (prev?.id === s.id ? null : s));
  };

  // ── save ──────────────────────────────────────────────────────────────────
  const handleSave = useCallback(async () => {
    if (!selBoard || !selClass) {
      toast.error('Please select at least a board and class');
      return;
    }
    setSaving(true);
    const payload = {
      board_id:   selBoard.id,
      board_name: selBoard.name,
      class_id:   selClass.id,
      class_name: selClass.name,
    };
    if (selStream) {
      payload.stream_id   = selStream.id;
      payload.stream_name = selStream.name;
    }
    if (selSubject) {
      payload.saved_subjects = [{ id: selSubject.id, name: selSubject.name }];
    }
    try {
      await apiClient().patch('/user/profile', payload);
      if (onProfileUpdate) onProfileUpdate(payload);
      toast.success('Academic details saved');
      setOpenRow(null);
      // fire the onboarding-updated event so library re-fetches
      try { window.dispatchEvent(new CustomEvent('syrabit:onboarding-updated', { detail: payload })); } catch {}
    } catch {
      toast.error('Failed to save academic details');
    } finally {
      setSaving(false);
    }
  }, [selBoard, selClass, selStream, selSubject, onProfileUpdate]);

  // ── render ────────────────────────────────────────────────────────────────
  return (
    <>
      {/* BOARD */}
      <ExpandRow
        icon={Globe}
        label="Board"
        value={selBoard?.name}
        placeholder="Select board"
        open={openRow === 'board'}
        onToggle={() => toggle('board')}
      />
      {openRow === 'board' && (
        <div className="px-4 pb-3 pt-2 border-b border-border/50">
          <OptionList
            items={boards}
            selected={selBoard}
            onSelect={pickBoard}
            loading={loadingBoards}
            emptyLabel="No boards available"
          />
        </div>
      )}

      {/* CLASS */}
      <ExpandRow
        icon={GraduationCap}
        label="Class / Semester"
        value={selClass?.name}
        placeholder={selBoard ? 'Select class' : 'Select board first'}
        open={openRow === 'class'}
        onToggle={() => toggle('class')}
        disabled={!selBoard}
      />
      {openRow === 'class' && (
        <div className="px-4 pb-3 pt-2 border-b border-border/50">
          <OptionList
            items={classes}
            selected={selClass}
            onSelect={pickClass}
            loading={loadingClasses}
            emptyLabel="No classes for this board"
          />
        </div>
      )}

      {/* STREAM — skip for degree boards */}
      {!degreeBoard && (
        <>
          <ExpandRow
            icon={Layers}
            label="Stream"
            value={selStream?.name}
            placeholder={selClass ? 'Select stream' : 'Select class first'}
            open={openRow === 'stream'}
            onToggle={() => toggle('stream')}
            disabled={!selClass}
          />
          {openRow === 'stream' && (
            <div className="px-4 pb-3 pt-2 border-b border-border/50">
              <OptionList
                items={streams}
                selected={selStream}
                onSelect={pickStream}
                loading={loadingStreams}
                emptyLabel="No streams for this class"
              />
            </div>
          )}
        </>
      )}

      {/* SUBJECT — only when stream is selected and subjects exist */}
      {!degreeBoard && selStream && (
        <>
          <ExpandRow
            icon={BookOpen}
            label="Subject"
            value={selSubject?.name}
            placeholder="Select subject (optional)"
            open={openRow === 'subject'}
            onToggle={() => toggle('subject')}
            disabled={!selStream}
          />
          {openRow === 'subject' && (
            <div className="px-4 pb-3 pt-2 border-b border-border/50">
              <OptionList
                items={subjects}
                selected={selSubject}
                onSelect={pickSubject}
                loading={loadingSubjects}
                emptyLabel="No subjects for this stream"
              />
            </div>
          )}
        </>
      )}

      {/* SAVE BUTTON — visible when a change has been made */}
      {isDirty && (
        <div className="px-4 py-3 border-b border-border/50">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !selBoard || !selClass}
            className="w-full py-2.5 rounded-xl text-sm font-semibold text-white flex items-center justify-center gap-2 transition-all hover:opacity-90 disabled:opacity-40"
            style={{ background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)' }}
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            {saving ? 'Saving…' : 'Save Academic Details'}
          </button>
        </div>
      )}
    </>
  );
}
