import {
  Sparkles, Loader2, Eye, Edit2, Trash2,
  CheckCircle, FileText, Layers, Globe, AlertTriangle,
  BookOpen, Hash, Search, ChevronDown, ChevronUp,
} from 'lucide-react';
import { useState } from 'react';
import StatusBadge, { STATUS_FILTER_OPTIONS } from './StatusBadge';
import StatusQuickToggle from './StatusQuickToggle';

const MARK_COLORS = {
  '1': { bg: 'rgba(59,130,246,0.12)', text: '#93c5fd', border: 'rgba(59,130,246,0.20)' },
  '2': { bg: 'rgba(16,185,129,0.12)', text: '#6ee7b7', border: 'rgba(16,185,129,0.20)' },
  '3': { bg: 'rgba(14,165,233,0.12)', text: '#7dd3fc', border: 'rgba(14,165,233,0.20)' },
  '5': { bg: 'rgba(245,158,11,0.12)', text: '#fcd34d', border: 'rgba(245,158,11,0.20)' },
  '10': { bg: 'rgba(236,72,153,0.12)', text: '#f9a8d4', border: 'rgba(236,72,153,0.18)' },
};

const SEO_TYPE_LABELS = { notes: 'Notes', definition: 'Defs', 'important-questions': 'ImpQ', mcqs: 'MCQs', examples: 'Ex', faq: 'FAQ' };

export default function ChapterList({
  chapters, totalChapters, chapterAssets,
  statusFilter = 'all', setStatusFilter,
  sortByStatus = false, setSortByStatus,
  generatingNotes,
  onGenerateNotes, onDeleteChapter, onChangeChapterStatus,
  onViewChapter, onEditChapter,
  selSubject, subjectData, onCreateNew,
  selectedIds, onToggleSelect, onToggleSelectAll,
}) {
  const total = typeof totalChapters === 'number' ? totalChapters : chapters.length;
  const [expandedCard, setExpandedCard] = useState(null);
  const selectionEnabled = !!onToggleSelect;
  const visibleIds = chapters.map(c => c.id);
  const allVisibleSelected = selectionEnabled && visibleIds.length > 0 && visibleIds.every(id => selectedIds?.has(id));
  const someVisibleSelected = selectionEnabled && visibleIds.some(id => selectedIds?.has(id));
  return (
    <>
      <button
        onClick={onCreateNew}
        className="w-full p-5 rounded-xl border border-dashed border-violet-500/30 hover:border-violet-500/60 bg-violet-500/5 hover:bg-violet-500/10 text-center transition-colors"
      >
        <BookOpen size={28} className="mx-auto text-violet-400 mb-2" />
        <p className="text-sm font-bold text-gray-900">Create New Chapter</p>
        <p className="text-[11px] text-gray-400 mt-1">Add chapter content with Markdown — slug auto-generated</p>
      </button>

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            {selectionEnabled && (
              <input
                type="checkbox"
                checked={allVisibleSelected}
                ref={el => { if (el) el.indeterminate = !allVisibleSelected && someVisibleSelected; }}
                onChange={() => onToggleSelectAll && onToggleSelectAll(visibleIds, !allVisibleSelected)}
                className="h-3.5 w-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-400 cursor-pointer"
                title={allVisibleSelected ? 'Clear selection' : 'Select all visible chapters'}
                data-testid="chapter-select-all"
              />
            )}
            <p className="text-sm font-semibold text-gray-900">Chapters ({chapters.length}{chapters.length !== total ? ` of ${total}` : ''})</p>
          </div>
          {(setStatusFilter || setSortByStatus) && (
            <div className="flex items-center gap-2">
              {setStatusFilter && (
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="h-8 px-2 rounded-lg text-xs text-gray-700 bg-white border border-gray-200 outline-none focus:border-violet-400"
                  data-testid="chapter-status-filter"
                >
                  {STATUS_FILTER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              )}
              {setSortByStatus && (
                <button
                  onClick={() => setSortByStatus(!sortByStatus)}
                  className={`h-8 px-2 rounded-lg text-xs border transition-colors ${sortByStatus ? 'bg-violet-50 text-violet-600 border-violet-200' : 'bg-white text-gray-500 border-gray-200 hover:text-gray-700'}`}
                  title="Sort by status (drafts/unpublished first)"
                  data-testid="chapter-sort-status"
                >
                  Sort: status
                </button>
              )}
            </div>
          )}
        </div>

        {chapters.length === 0 && <p className="text-xs text-gray-400 py-4 text-center">No chapters yet — create the first one above</p>}
        {chapters.map(ch => {
          const assets   = chapterAssets[ch.id] || {};
          const hasNotes = assets.notesGenerated || (ch.content && ch.content.trim().length > 50);
          const isQP = ch.content_type === 'question_paper';
          const hasAssamese = !isQP && !!(ch.content_as && ch.content_as.trim().length > 10);
          const preview  = ch.content ? ch.content.replace(/#{1,6}\s?/g, '').replace(/\*+/g, '').replace(/\n+/g, ' ').trim().slice(0, 130) : '';
          const wordCount = ch.content ? ch.content.split(/\s+/).filter(Boolean).length : 0;
          const hasPyqs   = (assets.pyqCount || 0) > 0;
          const hasFc     = (assets.flashcardCount || 0) > 0;
          const hasBlogs  = (assets.blogCount || 0) > 0;
          const hasSeoTopics = (assets.seoTopicCount || 0) > 0;
          const hasSeoPages  = (assets.seoPagesPublished || 0) > 0;
          const markWise  = assets.markWiseCounts || {};
          const seoTypes  = assets.seoPageTypes || {};
          return (
            <div key={ch.id}
              className="rounded-xl border transition-all"
              style={{
                borderColor: hasNotes ? 'rgba(16,185,129,0.18)' : '#e5e7eb',
                background:  '#f9fafb',
              }}>
              <div className="flex items-start gap-2 p-3 pb-2">
                {selectionEnabled && (
                  <input
                    type="checkbox"
                    checked={selectedIds?.has(ch.id) || false}
                    onChange={() => onToggleSelect(ch.id)}
                    className="mt-1 h-3.5 w-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-400 cursor-pointer flex-shrink-0"
                    title="Select chapter for bulk actions"
                    data-testid={`chapter-select-${ch.id}`}
                  />
                )}
                <div className="flex-shrink-0 mt-1">
                  {hasNotes
                    ? <div className="w-2 h-2 rounded-full bg-emerald-400" title="Notes generated" />
                    : <div className="w-2 h-2 rounded-full bg-white/15" title="No notes yet" />}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="text-sm font-semibold text-gray-900 truncate">{ch.title}</p>
                    {onChangeChapterStatus
                      ? <StatusQuickToggle
                          status={ch.status}
                          onChange={(next) => onChangeChapterStatus(ch.id, next)}
                          testIdPrefix={`chapter-status-toggle-${ch.id}`}
                        />
                      : <StatusBadge status={ch.status} />}
                    {ch.content_type === 'question_paper' && (
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide" style={{ background: 'rgba(245,158,11,0.15)', color: '#d97706', border: '1px solid rgba(245,158,11,0.25)' }}>Question Paper</span>
                    )}
                    {ch.content_type && ch.content_type !== 'notes' && ch.content_type !== 'question_paper' && (
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-gray-100 text-gray-400 uppercase tracking-wide">{ch.content_type}</span>
                    )}
                    {wordCount > 0 && (
                      <span className="text-[9px] text-gray-300 font-mono">{wordCount.toLocaleString()} words</span>
                    )}
                    {hasAssamese && (
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide" style={{ background: 'rgba(139,92,246,0.12)', color: '#8b5cf6', border: '1px solid rgba(139,92,246,0.20)' }}>অসমীয়া</span>
                    )}
                  </div>
                  {ch.description && !preview && (
                    <p className="text-xs text-white/35 mt-0.5 truncate">{ch.description}</p>
                  )}
                  {preview && (
                    <p className="text-[11px] text-gray-400 mt-1 leading-relaxed line-clamp-2">{preview}{preview.length >= 130 ? '…' : ''}</p>
                  )}
                  {!hasNotes && !preview && (
                    <p className="text-[11px] text-gray-300 mt-1 italic">No notes yet — generate with AI or edit manually</p>
                  )}
                </div>
                <div className="flex gap-0.5 flex-shrink-0 ml-1">
                  <button
                    onClick={() => onGenerateNotes(ch.id, ch.title)}
                    disabled={generatingNotes.has(ch.id)}
                    className="flex items-center gap-1 h-6 px-2 rounded-lg text-[10px] font-semibold disabled:opacity-40 transition-all hover:brightness-110"
                    style={hasNotes
                      ? { background: 'rgba(16,185,129,0.15)', color: '#6ee7b7', border: '1px solid rgba(16,185,129,0.25)' }
                      : { background: 'linear-gradient(135deg,rgba(124,58,237,0.35),rgba(79,70,229,0.35))', color: '#7c3aed', border: '1px solid rgba(139,92,246,0.35)' }}
                    title={hasNotes ? 'Regenerate AI notes for this chapter' : 'Generate AI notes for this chapter'}
                  >
                    {generatingNotes.has(ch.id)
                      ? <><Loader2 size={10} className="animate-spin" /> Generating…</>
                      : <><Sparkles size={10} /> {hasNotes ? 'Regen' : 'AI ⚡'}</>}
                  </button>
                  <button onClick={() => onViewChapter(ch)} className="p-1.5 rounded-lg hover:bg-emerald-500/10 text-gray-400 hover:text-emerald-400" title="Preview lesson" data-testid={`open-chapter-${ch.id}`}><Eye size={13} /></button>
                  <button onClick={() => onEditChapter(ch)}
                    className="p-1.5 rounded-lg hover:bg-violet-500/10 text-gray-400 hover:text-violet-400" title="Edit chapter"><Edit2 size={13} /></button>
                  <button onClick={() => onDeleteChapter(ch.id)} className="p-1.5 rounded-lg hover:bg-red-500/10 text-gray-400 hover:text-red-400" title="Delete chapter"><Trash2 size={13} /></button>
                </div>
              </div>
              {(hasNotes || hasPyqs || hasFc || hasBlogs || hasSeoTopics || hasSeoPages || ch.slug) && (
                <div className="px-3 pb-2.5 space-y-1.5">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {hasNotes && (
                      <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-semibold"
                        style={{ background: 'rgba(16,185,129,0.12)', color: '#6ee7b7', border: '1px solid rgba(16,185,129,0.20)' }}>
                        <CheckCircle size={9} /> Notes
                      </span>
                    )}
                    {hasPyqs && (
                      <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-semibold"
                        style={{ background: 'rgba(245,158,11,0.12)', color: '#fcd34d', border: '1px solid rgba(245,158,11,0.20)' }}>
                        <FileText size={9} /> {assets.pyqCount} PYQs
                      </span>
                    )}
                    {hasFc && (
                      <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-semibold"
                        style={{ background: 'rgba(16,185,129,0.10)', color: '#6ee7b7', border: '1px solid rgba(16,185,129,0.18)' }}>
                        <Layers size={9} /> {assets.flashcardCount} FC
                      </span>
                    )}
                    {hasBlogs && (
                      <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-semibold"
                        style={{ background: 'rgba(59,130,246,0.12)', color: '#93c5fd', border: '1px solid rgba(59,130,246,0.20)' }}>
                        <Globe size={9} /> {assets.blogCount} Blogs
                      </span>
                    )}
                    {hasSeoTopics && (
                      <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-semibold"
                        style={{ background: 'rgba(139,92,246,0.12)', color: '#c4b5fd', border: '1px solid rgba(139,92,246,0.20)' }}>
                        <Search size={9} /> {assets.seoTopicCount} SEO Topics
                      </span>
                    )}
                    {hasSeoPages && (
                      <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-semibold"
                        style={{ background: 'rgba(99,102,241,0.12)', color: '#a5b4fc', border: '1px solid rgba(99,102,241,0.20)' }}>
                        <Hash size={9} /> {assets.seoPagesPublished} SEO Pages
                      </span>
                    )}
                    {ch.coverage_score != null && (
                      <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-semibold"
                        style={ch.coverage_score < 60
                          ? { background: 'rgba(239,68,68,0.12)', color: '#fca5a5', border: '1px solid rgba(239,68,68,0.25)' }
                          : ch.coverage_score < 80
                          ? { background: 'rgba(245,158,11,0.12)', color: '#fcd34d', border: '1px solid rgba(245,158,11,0.20)' }
                          : { background: 'rgba(16,185,129,0.12)', color: '#6ee7b7', border: '1px solid rgba(16,185,129,0.20)' }
                        }
                        title={ch.coverage_score < 60 ? 'Low syllabus coverage — consider regenerating' : `${ch.coverage_score}% of syllabus topics covered`}
                      >
                        {ch.coverage_score < 60 && <AlertTriangle size={9} />}
                        {ch.coverage_score >= 60 && <CheckCircle size={9} />}
                        {ch.coverage_score}% Coverage
                      </span>
                    )}
                    {(hasPyqs || hasSeoTopics || hasSeoPages) && (
                      <button
                        onClick={(e) => { e.stopPropagation(); setExpandedCard(expandedCard === ch.id ? null : ch.id); }}
                        className="ml-auto flex items-center gap-0.5 text-[9px] text-gray-300 hover:text-gray-500 transition"
                      >
                        {expandedCard === ch.id ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
                        Details
                      </button>
                    )}
                  </div>

                  {expandedCard === ch.id && (
                    <div className="rounded-lg p-2.5 space-y-2"
                      style={{ background: '#f9fafb', border: '1px solid #e5e7eb' }}>

                      {Object.keys(markWise).length > 0 && (
                        <div>
                          <p className="text-[9px] text-gray-400 font-semibold uppercase tracking-wider mb-1">Mark-wise Questions</p>
                          <div className="flex items-center gap-1.5 flex-wrap">
                            {Object.entries(markWise).sort(([a], [b]) => Number(a) - Number(b)).map(([mark, count]) => {
                              const mc = MARK_COLORS[mark] || MARK_COLORS['1'];
                              return (
                                <span key={mark} className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-semibold"
                                  style={{ background: mc.bg, color: mc.text, border: `1px solid ${mc.border}` }}>
                                  {mark}-mark: {count}
                                </span>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {assets.linkedTopics?.length > 0 && (
                        <div>
                          <p className="text-[9px] text-gray-400 font-semibold uppercase tracking-wider mb-1">Linked SEO Topics</p>
                          <div className="flex flex-wrap gap-1">
                            {assets.linkedTopics.map(t => (
                              <span key={t.id} className="flex items-center gap-1 px-2 py-0.5 rounded text-[9px]"
                                style={{
                                  background: t.status === 'published' ? 'rgba(139,92,246,0.10)' : '#f9fafb',
                                  color: t.status === 'published' ? '#c4b5fd' : '#9ca3af',
                                  border: `1px solid ${t.status === 'published' ? 'rgba(139,92,246,0.20)' : '#e5e7eb'}`,
                                }}>
                                {t.status === 'published' && <CheckCircle size={8} />}
                                {t.title}
                                {t.primary_keyword && <span className="text-gray-300 ml-0.5">({t.primary_keyword})</span>}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {Object.keys(seoTypes).length > 0 && (
                        <div>
                          <p className="text-[9px] text-gray-400 font-semibold uppercase tracking-wider mb-1">SEO Pages by Type</p>
                          <div className="flex items-center gap-1.5 flex-wrap">
                            {Object.entries(seoTypes).map(([type, count]) => (
                              <span key={type} className="px-2 py-0.5 rounded text-[9px] font-medium"
                                style={{ background: 'rgba(99,102,241,0.10)', color: '#a5b4fc', border: '1px solid rgba(99,102,241,0.15)' }}>
                                {SEO_TYPE_LABELS[type] || type}: {count}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}
