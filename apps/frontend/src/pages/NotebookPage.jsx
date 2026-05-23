/**
 * NotebookPage — saved highlights from chapters, reader, and chat,
 * plus NotebookLM-style AI-generated notes (Task #641).
 *
 * Lets the learner search, filter by tag, edit, delete, export their
 * notes (markdown / CSV), and generate structured study notes with
 * citations from a chat conversation, a chapter, or saved highlights.
 */
import { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import {
  Search, Tag as TagIcon, Trash2, Download, ExternalLink,
  Loader2, NotebookPen, Pencil, Check, X, Sparkles, Wand2,
  BookOpen, MessageSquare, ListChecks, AlertCircle,
  Table2, GitBranch, Share2, Lightbulb, ChevronDown, ChevronRight,
} from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import { PageTitle } from '@/components/PageTitle';
import { studyApi } from '@/utils/studyApi';
import { useLibraryBundleSlim } from '@/hooks/useContent';
import { apiClient } from '@/utils/api';
import PinResetBanner from '@/components/PinResetBanner';
import { getClaimSeenAt, markClaimSeen, isRecentlyClaimed } from '@/utils/claimSeen';
import { toast } from 'sonner';

/* ─────────────── URL safety ───────────────
 * Only allow http(s) absolute URLs or app-internal paths starting with "/".
 * Blocks javascript:/data:/vbscript: schemes that could fire on click. */
function safeHref(url) {
  if (!url || typeof url !== 'string') return '';
  const u = url.trim();
  if (!u) return '';
  if (u.startsWith('/') && !u.startsWith('//')) return u;
  if (/^https?:\/\//i.test(u)) return u;
  return '';
}

/* ─────────────── Visual element helpers ─────────────── */

function countVisualElements(structured) {
  if (!structured) return {};
  const counts = {};
  if (structured.tables?.length)    counts.tables    = structured.tables.length;
  if (structured.diagrams?.length)  counts.diagrams  = structured.diagrams.length;
  if (structured.mindmap)           counts.mindmap   = 1;
  if (structured.mnemonics?.length) counts.mnemonics = structured.mnemonics.length;
  return counts;
}

function visualElementParts(structured) {
  const c = countVisualElements(structured);
  const parts = [];
  if (c.tables)    parts.push(`${c.tables} ${c.tables === 1 ? 'table' : 'tables'}`);
  if (c.diagrams)  parts.push(`${c.diagrams} ${c.diagrams === 1 ? 'diagram' : 'diagrams'}`);
  if (c.mindmap)   parts.push('1 mind map');
  if (c.mnemonics) parts.push(`${c.mnemonics} ${c.mnemonics === 1 ? 'mnemonic' : 'mnemonics'}`);
  return parts;
}

const _ELEMENT_BADGES = [
  { key: 'tables',    Icon: Table2,    label: (n) => `${n} ${n === 1 ? 'table' : 'tables'}`,       color: 'bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-500/20' },
  { key: 'diagrams',  Icon: GitBranch, label: (n) => `${n} ${n === 1 ? 'diagram' : 'diagrams'}`,   color: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/20' },
  { key: 'mindmap',   Icon: Share2,    label: () => 'mind map',                                     color: 'bg-purple-500/10 text-purple-700 dark:text-purple-300 border-purple-500/20' },
  { key: 'mnemonics', Icon: Lightbulb, label: (n) => `${n} ${n === 1 ? 'mnemonic' : 'mnemonics'}`, color: 'bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/20' },
];

function VisualElementBadges({ structured }) {
  const counts = countVisualElements(structured);
  const badges = _ELEMENT_BADGES.filter(({ key }) => counts[key]);
  if (!badges.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {badges.map(({ key, Icon, label, color }) => (
        <span
          key={key}
          className={`inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full border ${color}`}
        >
          <Icon className="w-3 h-3" />
          {label(counts[key])}
        </span>
      ))}
    </div>
  );
}

/* ─────────────── Citation chip ─────────────── */

function CitationChip({ id, citationsMap }) {
  const c = citationsMap[id];
  const label = c?.label || id;
  const url = safeHref(c?.url);
  const isInternal = url && url.startsWith('/');
  const className =
    'inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded ' +
    'bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 transition';
  if (!url) {
    return <span className={className} title={label}>[{id}]</span>;
  }
  if (isInternal) {
    return (
      <Link to={url} className={className} title={label}>[{id}]</Link>
    );
  }
  return (
    <a href={url} target="_blank" rel="noreferrer" className={className} title={label}>
      [{id}]
    </a>
  );
}

function CitationList({ ids, citationsMap }) {
  if (!ids?.length) return null;
  return (
    <span className="ml-1 inline-flex flex-wrap gap-1 align-middle">
      {ids.map((cid) => (
        <CitationChip key={cid} id={cid} citationsMap={citationsMap} />
      ))}
    </span>
  );
}

/* ─────────────── Note tables ─────────────── */

function NoteTable({ table }) {
  const { caption, headers, rows } = table;
  return (
    <div className="rounded-lg border border-border/60 overflow-x-auto">
      <table className="w-full text-xs border-collapse min-w-[400px]">
        {caption && (
          <caption className="text-left text-[11px] font-semibold text-muted-foreground px-3 py-1.5 bg-muted/40 border-b border-border/40">
            {caption}
          </caption>
        )}
        <thead>
          <tr className="bg-muted/60 sticky top-0">
            {headers.map((h, i) => (
              <th key={i}
                  className="px-3 py-2 text-left font-semibold text-foreground border-r last:border-r-0 border-border/30 whitespace-nowrap font-mono">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className={ri % 2 === 0 ? 'bg-background' : 'bg-muted/20'}>
              {row.map((cell, ci) => (
                <td key={ci}
                    className="px-3 py-1.5 border-r last:border-r-0 border-border/20 font-mono text-muted-foreground whitespace-pre-wrap">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ─────────────── Note diagrams ─────────────── */

const _DIAGRAM_STYLES = {
  cell:      'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800',
  flowchart: 'bg-blue-50   dark:bg-blue-950/30   border-blue-200   dark:border-blue-800',
  timeline:  'bg-amber-50  dark:bg-amber-950/30  border-amber-200  dark:border-amber-800',
  other:     'bg-muted/40  border-border/50',
};

function NoteDiagram({ diagram }) {
  const style = _DIAGRAM_STYLES[diagram.type] || _DIAGRAM_STYLES.other;
  return (
    <div className={`rounded-lg border p-3 space-y-2 ${style}`}>
      <div className="flex items-center gap-2">
        <GitBranch className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
        <span className="text-xs font-semibold">{diagram.label}</span>
        <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-background/60 border border-border/30 text-muted-foreground capitalize">
          {diagram.type}
        </span>
      </div>
      <pre className="text-xs leading-relaxed whitespace-pre-wrap font-mono text-foreground/80 bg-background/50 rounded p-2 border border-border/20 overflow-x-auto">
        {diagram.content}
      </pre>
    </div>
  );
}

/* ─────────────── Note mindmap ─────────────── */

function NoteMindmap({ mindmap }) {
  const [open, setOpen] = useState({});
  const toggle = (i) => setOpen((p) => ({ ...p, [i]: !p[i] }));

  return (
    <div className="rounded-lg border border-border/50 bg-muted/10 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 bg-muted/30 border-b border-border/40">
        <Share2 className="w-3.5 h-3.5 text-primary shrink-0" />
        <span className="text-xs font-semibold">{mindmap.root}</span>
        <span className="text-[10px] text-muted-foreground ml-auto">Mind map</span>
      </div>
      <ul className="p-2 space-y-1">
        {(mindmap.branches || []).map((br, i) => {
          const isOpen = open[i] !== false && (open[i] === true || br.children?.length <= 3);
          return (
            <li key={i} className="rounded-md overflow-hidden border border-border/30">
              <button
                onClick={() => toggle(i)}
                className="w-full flex items-center gap-2 px-3 py-1.5 text-xs font-medium bg-background hover:bg-muted/40 transition text-left"
              >
                {isOpen
                  ? <ChevronDown className="w-3 h-3 shrink-0 text-muted-foreground" />
                  : <ChevronRight className="w-3 h-3 shrink-0 text-muted-foreground" />}
                <span>{br.label}</span>
                {br.children?.length > 0 && (
                  <span className="ml-auto text-[10px] text-muted-foreground font-mono">
                    {br.children.length}
                  </span>
                )}
              </button>
              {isOpen && br.children?.length > 0 && (
                <ul className="px-3 pb-2 pt-1 flex flex-wrap gap-1.5 border-t border-border/20 bg-muted/20">
                  {br.children.map((child, j) => (
                    <li key={j}
                        className="text-[11px] px-2 py-0.5 rounded-full bg-primary/10 border border-primary/20 text-primary">
                      {child}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ─────────────── Note mnemonics ─────────────── */

function NoteMnemonics({ mnemonics }) {
  if (!mnemonics?.length) return null;
  return (
    <section className="space-y-2">
      <h4 className="text-sm font-semibold flex items-center gap-1.5">
        <Lightbulb className="w-3.5 h-3.5 text-amber-500" />
        Mnemonics
      </h4>
      <div className="space-y-2">
        {mnemonics.map((mn, i) => (
          <div key={i}
               className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3 space-y-1">
            <div className="text-[10px] font-medium text-amber-700 dark:text-amber-400 uppercase tracking-wide">
              {mn.for}
            </div>
            <div className="text-sm font-bold text-foreground leading-snug">{mn.mnemonic}</div>
            {mn.explanation && (
              <div className="text-xs text-muted-foreground leading-relaxed">{mn.explanation}</div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

/* ─────────────── Structured AI note body ─────────────── */

function StructuredNoteBody({ structured, citations }) {
  const cmap = useMemo(() => {
    const m = {};
    (citations || []).forEach((c) => { if (c?.id) m[c.id] = c; });
    return m;
  }, [citations]);

  if (!structured) return null;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full bg-violet-500/10 text-violet-700 dark:text-violet-300 border border-violet-500/20">
          <Wand2 className="w-3 h-3" /> AI generated
        </span>
        {structured.title && (
          <h3 className="text-base font-semibold leading-snug truncate">{structured.title}</h3>
        )}
      </div>

      <VisualElementBadges structured={structured} />

      {structured.summary && (
        <p className="text-sm text-muted-foreground leading-relaxed">{structured.summary}</p>
      )}

      {(structured.outline || []).map((sec, i) => (
        <section key={i} className="space-y-1.5">
          <h4 className="text-sm font-semibold flex items-center gap-1 flex-wrap">
            <span>{sec.heading}</span>
            <CitationList ids={sec.citations} citationsMap={cmap} />
          </h4>
          <ul className="text-sm leading-relaxed space-y-1 list-disc pl-5">
            {(sec.points || []).map((p, j) => (
              <li key={j}>{p}</li>
            ))}
          </ul>
        </section>
      ))}

      {structured.key_terms?.length > 0 && (
        <section className="space-y-1.5">
          <h4 className="text-sm font-semibold">Key terms</h4>
          <dl className="text-sm space-y-1">
            {structured.key_terms.map((kt, i) => (
              <div key={i} className="flex flex-wrap items-baseline gap-1">
                <dt className="font-semibold">{kt.term}:</dt>
                <dd className="text-muted-foreground">{kt.definition}</dd>
                <CitationList ids={kt.citations} citationsMap={cmap} />
              </div>
            ))}
          </dl>
        </section>
      )}

      {structured.qa?.length > 0 && (
        <section className="space-y-2">
          <h4 className="text-sm font-semibold">Q&amp;A</h4>
          <div className="space-y-2">
            {structured.qa.map((qa, i) => (
              <div key={i} className="rounded-lg border border-border/50 p-2.5 bg-muted/30">
                <div className="text-sm font-medium flex flex-wrap items-baseline gap-1">
                  Q: {qa.q}
                  <CitationList ids={qa.citations} citationsMap={cmap} />
                </div>
                <div className="text-sm text-muted-foreground mt-1">A: {qa.a}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      {structured.tables?.length > 0 && (
        <section className="space-y-2">
          <h4 className="text-sm font-semibold flex items-center gap-1.5">
            <Table2 className="w-3.5 h-3.5 text-muted-foreground" />
            Tables
          </h4>
          <div className="space-y-3">
            {structured.tables.map((tbl, i) => (
              <NoteTable key={i} table={tbl} />
            ))}
          </div>
        </section>
      )}

      {structured.diagrams?.length > 0 && (
        <section className="space-y-2">
          <h4 className="text-sm font-semibold flex items-center gap-1.5">
            <GitBranch className="w-3.5 h-3.5 text-muted-foreground" />
            Diagrams
          </h4>
          <div className="space-y-2">
            {structured.diagrams.map((diag, i) => (
              <NoteDiagram key={i} diagram={diag} />
            ))}
          </div>
        </section>
      )}

      {structured.mindmap && (
        <section className="space-y-2">
          <h4 className="text-sm font-semibold flex items-center gap-1.5">
            <Share2 className="w-3.5 h-3.5 text-muted-foreground" />
            Mind Map
          </h4>
          <NoteMindmap mindmap={structured.mindmap} />
        </section>
      )}

      {structured.mnemonics?.length > 0 && (
        <NoteMnemonics mnemonics={structured.mnemonics} />
      )}

      {citations?.length > 0 && (
        <details className="text-xs text-muted-foreground border-t border-border/40 pt-2">
          <summary className="cursor-pointer hover:text-foreground">
            Sources ({citations.length})
          </summary>
          <ul className="mt-2 space-y-1">
            {citations.map((c) => {
              const safe = safeHref(c.url);
              return (
                <li key={c.id} className="flex items-center gap-1.5">
                  <span className="font-mono">[{c.id}]</span>
                  {safe ? (
                    safe.startsWith('/') ? (
                      <Link to={safe} className="hover:underline truncate">{c.label}</Link>
                    ) : (
                      <a href={safe} target="_blank" rel="noreferrer"
                         className="hover:underline truncate">{c.label}</a>
                    )
                  ) : (
                    <span className="truncate">{c.label}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </div>
  );
}

/* ─────────────── Note card ─────────────── */

function NoteCard({ note, onChange, onDelete, recentlySynced, selected, onToggleSelect }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(note.text);
  const [tags, setTags] = useState((note.tags || []).join(', '));
  const [saving, setSaving] = useState(false);
  const isGenerated = !!note.generated && !!note.structured;

  const save = async () => {
    setSaving(true);
    try {
      const tagArr = tags.split(',').map(t => t.trim()).filter(Boolean);
      // Generated notes render the structured body, not `text`. Editing
      // `text` here would be invisible to the user, so only patch tags.
      const patch = isGenerated ? { tags: tagArr } : { text, tags: tagArr };
      const updated = await studyApi.patchNote(note.id, patch);
      onChange(updated);
      setEditing(false);
    } catch (e) {
      toast.error(e.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <article className="rounded-2xl border border-border/60 bg-card p-4 shadow-sm">
      {editing ? (
        <>
          {isGenerated ? (
            <p className="text-xs text-muted-foreground italic mb-2">
              AI-generated content is read-only. You can still edit tags below.
            </p>
          ) : (
            <textarea
              value={text} onChange={(e) => setText(e.target.value)}
              rows={4}
              className="w-full text-sm rounded-lg border border-border/60 p-2 bg-background mb-2 font-mono"
            />
          )}
          <input
            value={tags} onChange={(e) => setTags(e.target.value)}
            placeholder="Tags (comma-separated)"
            className="w-full text-xs rounded-lg border border-border/60 px-2 py-1.5 bg-background mb-2"
          />
        </>
      ) : (
        <>
          {recentlySynced && (
            <div className="mb-2 inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border border-emerald-500/20">
              <Sparkles className="w-3 h-3" /> Recently synced
            </div>
          )}
          {isGenerated ? (
            <StructuredNoteBody structured={note.structured} citations={note.citations} />
          ) : (
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{note.text}</p>
          )}
          {note.tags?.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {note.tags.map((t) => (
                <span key={t} className="text-[11px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground">
                  #{t}
                </span>
              ))}
            </div>
          )}
        </>
      )}

      <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
        <div className="flex items-center gap-2 min-w-0">
          {onToggleSelect && !isGenerated && (
            <label className="inline-flex items-center gap-1 cursor-pointer select-none">
              <input
                type="checkbox" checked={!!selected}
                onChange={() => onToggleSelect(note.id)}
                className="h-3.5 w-3.5"
              />
              <span className="text-[10px]">use as source</span>
            </label>
          )}
          {(() => {
            const safe = safeHref(note.source_url);
            const label = note.source_title || safe || '';
            if (safe) {
              return safe.startsWith('/') ? (
                <Link to={safe}
                      className="inline-flex items-center gap-1 hover:underline truncate max-w-[260px]">
                  <ExternalLink className="w-3 h-3 shrink-0" />
                  <span className="truncate">{label}</span>
                </Link>
              ) : (
                <a href={safe} target="_blank" rel="noreferrer"
                   className="inline-flex items-center gap-1 hover:underline truncate max-w-[260px]">
                  <ExternalLink className="w-3 h-3 shrink-0" />
                  <span className="truncate">{label}</span>
                </a>
              );
            }
            if (note.chapter_ref) return <span className="truncate">{note.chapter_ref}</span>;
            return <span>Saved</span>;
          })()}
        </div>
        <div className="flex items-center gap-1">
          {editing ? (
            <>
              <button onClick={save} disabled={saving}
                      className="p-1.5 rounded hover:bg-muted text-emerald-600">
                {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
              </button>
              <button onClick={() => { setEditing(false); setText(note.text); }}
                      className="p-1.5 rounded hover:bg-muted">
                <X className="w-3.5 h-3.5" />
              </button>
            </>
          ) : (
            <>
              <button onClick={() => setEditing(true)} className="p-1.5 rounded hover:bg-muted">
                <Pencil className="w-3.5 h-3.5" />
              </button>
              <button onClick={() => onDelete(note.id)} className="p-1.5 rounded hover:bg-muted text-red-600">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </>
          )}
        </div>
      </div>
    </article>
  );
}

/* ─────────────── Generate-with-AI modal ─────────────── */

const SOURCE_TABS = [
  { key: 'conversation', label: 'Recent chat', icon: MessageSquare },
  { key: 'chapter',      label: 'Chapter',     icon: BookOpen },
  { key: 'subject',      label: 'Subject',     icon: BookOpen },
  { key: 'highlights',   label: 'Highlights',  icon: ListChecks },
];

/* Cascading board → class → stream → subject → (chapter) picker. The
 * `mode` prop selects whether we surface the chapter dropdown
 * (mode='chapter') or stop at subject (mode='subject'). Returns the
 * picked id via onPick. */
function LibraryPicker({ mode, onPick, value }) {
  const { data: bundle, isLoading: bundleLoading } = useLibraryBundleSlim();
  const [boardId, setBoardId] = useState('');
  const [classId, setClassId] = useState('');
  const [streamId, setStreamId] = useState('');
  const [subjectId, setSubjectId] = useState('');
  const [chapters, setChapters] = useState([]);
  const [chaptersLoading, setChaptersLoading] = useState(false);
  const [chapterId, setChapterId] = useState('');

  const boards = bundle?.boards || [];
  const classes = useMemo(
    () => (bundle?.classes || []).filter((c) => !boardId || c.board_id === boardId),
    [bundle, boardId],
  );
  const streams = useMemo(
    () => (bundle?.streams || []).filter((s) => !classId || s.class_id === classId),
    [bundle, classId],
  );
  const subjects = useMemo(() => {
    const all = bundle?.subjects || [];
    if (streams.length > 0) {
      return all.filter((s) => s.stream_id && s.stream_id === streamId);
    }
    // Subjects whose stream's class matches (subjects always belong to a
    // stream in the slim bundle, so derive the class via the stream).
    if (!classId) return [];
    const classStreamIds = new Set(
      (bundle?.streams || []).filter((s) => s.class_id === classId).map((s) => s.id),
    );
    return all.filter((s) => classStreamIds.has(s.stream_id));
  }, [bundle, classId, streamId, streams]);

  // Auto-pick first available step so the user can complete the picker
  // with minimal clicks; they can always change it.
  useEffect(() => { if (boards.length === 1) setBoardId(boards[0].id); }, [boards]);
  useEffect(() => {
    if (boardId && classes.length && !classes.find((c) => c.id === classId)) {
      setClassId(''); setStreamId(''); setSubjectId(''); setChapterId('');
    }
  }, [boardId]); // eslint-disable-line
  useEffect(() => {
    if (classId && !streams.find((s) => s.id === streamId)) {
      setStreamId(''); setSubjectId(''); setChapterId('');
    }
  }, [classId]); // eslint-disable-line
  useEffect(() => { setSubjectId(''); setChapterId(''); }, [streamId]);

  // Load chapters once a subject is picked, but only in chapter mode.
  useEffect(() => {
    setChapterId('');
    setChapters([]);
    if (mode !== 'chapter' || !subjectId) return;
    setChaptersLoading(true);
    apiClient().get(`/content/chapters/${subjectId}`)
      .then((r) => setChapters(r.data?.chapters || r.data || []))
      .catch(() => setChapters([]))
      .finally(() => setChaptersLoading(false));
  }, [subjectId, mode]);

  // Surface the picked id upward.
  useEffect(() => {
    if (mode === 'subject') onPick(subjectId);
    else onPick(chapterId);
  }, [chapterId, subjectId, mode]); // eslint-disable-line

  if (bundleLoading) {
    return (
      <div className="text-xs text-muted-foreground inline-flex items-center gap-1">
        <Loader2 className="w-3 h-3 animate-spin" /> Loading library…
      </div>
    );
  }

  const selectCls =
    'w-full text-sm rounded-lg border border-border/60 bg-background px-3 py-2';

  return (
    <div className="grid grid-cols-2 gap-2">
      <select className={selectCls} value={boardId}
              onChange={(e) => setBoardId(e.target.value)}>
        <option value="">Board…</option>
        {boards.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
      </select>
      <select className={selectCls} value={classId}
              onChange={(e) => setClassId(e.target.value)} disabled={!boardId}>
        <option value="">Class…</option>
        {classes.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      {streams.length > 0 && (
        <select className={selectCls} value={streamId}
                onChange={(e) => setStreamId(e.target.value)}>
          <option value="">Stream…</option>
          {streams.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      )}
      <select
        className={selectCls + (streams.length === 0 ? ' col-span-2' : '')}
        value={subjectId}
        onChange={(e) => setSubjectId(e.target.value)}
        disabled={!classId || (streams.length > 0 && !streamId)}
      >
        <option value="">Subject…</option>
        {subjects.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select>
      {mode === 'chapter' && (
        <select
          className={selectCls + ' col-span-2'}
          value={chapterId}
          onChange={(e) => setChapterId(e.target.value)}
          disabled={!subjectId || chaptersLoading}
        >
          <option value="">
            {chaptersLoading ? 'Loading chapters…'
              : !subjectId ? 'Pick a subject first…'
              : chapters.length === 0 ? 'No chapters yet'
              : 'Chapter…'}
          </option>
          {chapters.map((ch) => (
            <option key={ch.id} value={ch.id}>{ch.title || ch.name || ch.slug}</option>
          ))}
        </select>
      )}
    </div>
  );
}

function GenerateNotesModal({ open, onClose, onGenerated, selectedNoteIds, allNotes }) {
  const [tab, setTab] = useState('conversation');
  const [convs, setConvs] = useState(null);   // null = unloaded, [] = empty
  const [loadingConvs, setLoadingConvs] = useState(false);
  const [convErr, setConvErr] = useState('');
  const [convId, setConvId] = useState('');
  const [pickerId, setPickerId] = useState(''); // chapter or subject id from LibraryPicker
  const [focus, setFocus] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!open) return;
    setErr('');
    if (tab === 'conversation' && convs === null && !loadingConvs) {
      setLoadingConvs(true);
      studyApi.listMyConversations()
        .then((r) => {
          const list = Array.isArray(r) ? r : (r?.conversations || []);
          setConvs(list.slice(0, 50));
          if (list[0]) setConvId(list[0].id);
        })
        .catch((e) => {
          if (e.status === 401) {
            setConvErr('Sign in to use a chat conversation as a source.');
          } else {
            setConvErr(e.message || 'Could not load chats');
          }
          setConvs([]);
        })
        .finally(() => setLoadingConvs(false));
    }
  }, [open, tab]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null;

  const submit = async () => {
    setErr('');
    let payload = {
      source_kind: tab,
      response_lang: 'en',
      custom_focus: focus.trim().slice(0, 300),
    };
    if (tab === 'conversation') {
      if (!convId) { setErr('Pick a conversation.'); return; }
      payload.source_id = convId;
    } else if (tab === 'chapter') {
      if (!pickerId) { setErr('Pick a chapter from the library.'); return; }
      payload.source_id = pickerId;
    } else if (tab === 'subject') {
      if (!pickerId) { setErr('Pick a subject from the library.'); return; }
      payload.source_id = pickerId;
    } else if (tab === 'highlights') {
      const ids = (selectedNoteIds || []).filter(
        (id) => !allNotes.find((n) => n.id === id && n.generated),
      );
      if (!ids.length) {
        setErr('Tick the “use as source” boxes on at least one manual highlight first.');
        return;
      }
      payload.note_ids = ids.slice(0, 30);
    }
    setBusy(true);
    try {
      const r = await studyApi.generateNotes(payload);
      onGenerated(r.note);
      const parts = visualElementParts(r.note?.structured);
      toast.success(
        parts.length
          ? `Note generated with ${parts.join(', ')}`
          : 'Generated note saved.'
      );
      onClose();
    } catch (e) {
      // Surface the backend's clear message — Gemini-only path means we
      // never silently fall back to another model.
      const msg = e?.detail?.message || e.message || 'Generation failed';
      setErr(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4"
         onClick={onClose}>
      <div className="bg-card border border-border/60 rounded-2xl w-full max-w-lg shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between px-5 py-3 border-b border-border/50">
          <h2 className="font-semibold text-base inline-flex items-center gap-2">
            <Wand2 className="w-4 h-4 text-primary" />
            Generate notes with AI
          </h2>
          <button onClick={onClose} className="p-1.5 rounded hover:bg-muted">
            <X className="w-4 h-4" />
          </button>
        </header>

        <div className="px-5 py-4 space-y-4">
          <p className="text-xs text-muted-foreground">
            Notes are generated by Google Gemini using only the source you pick —
            with citations back to each chat message, chapter section, or highlight.
          </p>

          <div className="flex gap-1 rounded-xl bg-muted p-1">
            {SOURCE_TABS.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                onClick={() => { setTab(key); setErr(''); }}
                className={
                  'flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition ' +
                  (tab === key ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground')
                }
              >
                <Icon className="w-3.5 h-3.5" /> {label}
              </button>
            ))}
          </div>

          {tab === 'conversation' && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium">Pick a chat</label>
              {loadingConvs ? (
                <div className="text-xs text-muted-foreground inline-flex items-center gap-1">
                  <Loader2 className="w-3 h-3 animate-spin" /> Loading chats…
                </div>
              ) : convErr ? (
                <div className="text-xs text-amber-600 inline-flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" /> {convErr}
                </div>
              ) : (convs || []).length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No chat history yet. Try the chapter or highlights tabs.
                </p>
              ) : (
                <select
                  value={convId} onChange={(e) => setConvId(e.target.value)}
                  className="w-full text-sm rounded-lg border border-border/60 bg-background px-3 py-2"
                >
                  {convs.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.title || 'Untitled chat'}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}

          {tab === 'chapter' && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium">Pick a chapter from your library</label>
              <LibraryPicker mode="chapter" onPick={setPickerId} />
            </div>
          )}

          {tab === 'subject' && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium">Pick a subject from your library</label>
              <LibraryPicker mode="subject" onPick={setPickerId} />
              <p className="text-[11px] text-muted-foreground">
                We’ll pull the top sections from each chapter under this subject and ground the
                note in them.
              </p>
            </div>
          )}

          {tab === 'highlights' && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium">
                Selected highlights: {selectedNoteIds?.length || 0}
              </label>
              <p className="text-[11px] text-muted-foreground">
                Tick the “use as source” checkbox on any manual highlight in the list
                below the modal, then come back here.
              </p>
            </div>
          )}

          <div className="space-y-1.5">
            <label className="text-xs font-medium">Focus (optional)</label>
            <input
              value={focus} onChange={(e) => setFocus(e.target.value)}
              placeholder="e.g. only the photosynthesis equations"
              className="w-full text-sm rounded-lg border border-border/60 bg-background px-3 py-2"
            />
          </div>

          {err && (
            <div className="text-xs text-red-600 inline-flex items-start gap-1">
              <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              <span>{err}</span>
            </div>
          )}
        </div>

        <footer className="flex justify-end gap-2 px-5 py-3 border-t border-border/50">
          <button
            onClick={onClose}
            className="text-sm px-3 py-2 rounded-xl hover:bg-muted"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={busy}
            className="inline-flex items-center gap-1.5 text-sm font-medium px-3 py-2 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
            Generate
          </button>
        </footer>
      </div>
    </div>
  );
}

/* ─────────────── Page ─────────────── */

export default function NotebookPage() {
  const [notes, setNotes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [tag, setTag] = useState('');
  const [building, setBuilding] = useState(false);
  const [genOpen, setGenOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState([]);
  // Snapshot the per-surface high-water mark at mount so the badge
  // stays visible the entire time the page is open.
  const [seenAt] = useState(() => getClaimSeenAt('notes'));
  const maxClaimedRef = useRef('');

  const load = useCallback(() => {
    setLoading(true);
    studyApi.listNotes({ q, tag, limit: 200 })
      .then((r) => setNotes(r.notes || []))
      .catch((e) => toast.error(e.message || 'Failed to load notes'))
      .finally(() => setLoading(false));
  }, [q, tag]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const m = notes.reduce(
      (acc, n) => (n.claimed_at && n.claimed_at > acc ? n.claimed_at : acc),
      maxClaimedRef.current);
    maxClaimedRef.current = m;
  }, [notes]);

  useEffect(() => () => {
    if (maxClaimedRef.current) markClaimSeen('notes', maxClaimedRef.current);
  }, []);

  const allTags = useMemo(() => {
    const s = new Set();
    notes.forEach(n => (n.tags || []).forEach(t => s.add(t)));
    return Array.from(s).sort();
  }, [notes]);

  const onDelete = async (id) => {
    setNotes((n) => n.filter(x => x.id !== id));
    setSelectedIds((s) => s.filter((sid) => sid !== id));
    try { await studyApi.deleteNote(id); toast.success('Deleted'); }
    catch (e) { toast.error(e.message || 'Delete failed'); load(); }
  };

  const onChange = (updated) => {
    // The patch endpoint currently returns {ok, note}; older callers may
    // have returned the bare row. Handle both.
    const next = updated?.note || updated;
    setNotes((n) => n.map(x => x.id === next.id ? { ...x, ...next } : x));
  };

  const onGenerated = (note) => {
    setNotes((n) => [note, ...n]);
  };

  const toggleSelect = (id) => {
    setSelectedIds((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  };

  const buildCards = async () => {
    setBuilding(true);
    try {
      const r = await studyApi.buildFlashcards();
      toast.success(`Created ${r.created || 0} flashcards`);
    } catch (e) { toast.error(e.message || 'Could not build flashcards'); }
    finally { setBuilding(false); }
  };

  return (
    <AppLayout>
      <PageTitle title="Notebook · Syrabit.ai" />
      <div className="max-w-4xl mx-auto px-4 py-6">
        <div className="mb-3"><PinResetBanner /></div>
        <header className="flex flex-wrap items-start justify-between gap-3 mb-5">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <NotebookPen className="w-6 h-6 text-primary" />
              Notebook
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Highlights you saved + AI-generated study notes from your own chats and chapters.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => setGenOpen(true)}
              className="inline-flex items-center gap-1.5 text-sm px-3 py-2 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90"
            >
              <Wand2 className="w-4 h-4" />
              Generate with AI
            </button>
            <button
              onClick={buildCards}
              disabled={building || notes.length === 0}
              className="inline-flex items-center gap-1.5 text-sm px-3 py-2 rounded-xl border border-border/60 hover:bg-muted disabled:opacity-50"
            >
              {building ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              Build flashcards
            </button>
            <a
              href={studyApi.exportNotesUrl('md')}
              className="inline-flex items-center gap-1.5 text-sm px-3 py-2 rounded-xl border border-border/60 hover:bg-muted"
            >
              <Download className="w-4 h-4" /> .md
            </a>
            <a
              href={studyApi.exportNotesUrl('csv')}
              className="inline-flex items-center gap-1.5 text-sm px-3 py-2 rounded-xl border border-border/60 hover:bg-muted"
            >
              <Download className="w-4 h-4" /> .csv
            </a>
          </div>
        </header>

        <div className="flex flex-col sm:flex-row gap-2 mb-4">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Search notes…"
              className="w-full pl-9 pr-3 py-2 rounded-xl border border-border/60 bg-background text-sm"
            />
          </div>
          <select
            value={tag} onChange={(e) => setTag(e.target.value)}
            className="px-3 py-2 rounded-xl border border-border/60 bg-background text-sm"
          >
            <option value="">All tags</option>
            {allTags.map(t => <option key={t} value={t}>#{t}</option>)}
          </select>
        </div>

        {selectedIds.length > 0 && (
          <div className="mb-3 inline-flex items-center gap-2 text-xs text-muted-foreground">
            <TagIcon className="w-3 h-3" />
            {selectedIds.length} highlight{selectedIds.length === 1 ? '' : 's'} selected as source.
            <button
              onClick={() => setSelectedIds([])}
              className="underline hover:text-foreground"
            >
              clear
            </button>
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-20 text-muted-foreground">
            <Loader2 className="w-5 h-5 animate-spin mr-2" /> Loading notes…
          </div>
        ) : notes.length === 0 ? (
          <div className="text-center py-16 border border-dashed border-border/60 rounded-2xl">
            <NotebookPen className="w-10 h-10 mx-auto mb-3 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              No notes yet. Highlight any text in a chapter or article and tap <em>Save</em>,
              or generate a structured note from a chat or chapter.
            </p>
            <div className="mt-3 flex justify-center gap-2">
              <Link to="/library" className="inline-block text-sm font-medium text-primary hover:underline">
                Browse the library →
              </Link>
              <button
                onClick={() => setGenOpen(true)}
                className="text-sm font-medium text-primary hover:underline"
              >
                Generate with AI →
              </button>
            </div>
          </div>
        ) : (
          <div className="grid gap-3">
            {notes.map((n) => (
              <NoteCard
                key={n.id} note={n} onChange={onChange} onDelete={onDelete}
                recentlySynced={isRecentlyClaimed(n.claimed_at, seenAt)}
                selected={selectedIds.includes(n.id)}
                onToggleSelect={toggleSelect}
              />
            ))}
          </div>
        )}
      </div>

      <GenerateNotesModal
        open={genOpen}
        onClose={() => setGenOpen(false)}
        onGenerated={onGenerated}
        selectedNoteIds={selectedIds}
        allNotes={notes}
      />
    </AppLayout>
  );
}
