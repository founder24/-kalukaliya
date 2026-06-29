import { useMemo, useState, useCallback, memo } from 'react';
import { Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import {
  Bookmark, BookmarkCheck, BookOpen, Layers, Sparkles,
  Share2, ExternalLink, Lock, Loader2, ChevronDown,
} from './icons';
import { useShare } from '@/hooks/useShare';
import { prefetchSubjectData } from '@/hooks/useContent';
import { useContentLang } from '@/context/LanguageContext';

const THUMB_GRADIENTS = {
  math:      ['#4f46e5', '#7c3aed'],
  physics:   ['#2563eb', '#0891b2'],
  chemistry: ['#059669', '#0d9488'],
  biology:   ['#16a34a', '#15803d'],
  arts:      ['#d97706', '#b45309'],
  science:   ['#7c3aed', '#4f46e5'],
};

const SubjectCard = memo(function SubjectCard({ sub, chapters = [], isSaved, onToggleSave, onAskAI, index }) {
  const queryClient = useQueryClient();
  const { contentLang } = useContentLang();
  const isAs = contentLang === 'as';
  const thumbColors = useMemo(() => THUMB_GRADIENTS[sub.gradient] || THUMB_GRADIENTS.math, [sub.gradient]);
  const tags = useMemo(() => Array.isArray(sub.tags) ? sub.tags : [], [sub.tags]);
  const visibleTags = useMemo(() => tags.slice(0, 3), [tags]);
  const chapterCount = useMemo(() => chapters.length || sub.chapter_count || sub.chapterCount || 0, [chapters.length, sub.chapter_count, sub.chapterCount]);
  const hasDocument = useMemo(() => sub.has_document === true, [sub.has_document]);

  const subjectLandingPath = useMemo(() =>
    sub.boardSlug && sub.classSlug && sub.slug
      ? `/${sub.boardSlug}/${sub.classSlug}/${sub.slug}`
      : `/subject/${sub.id}`,
    [sub.boardSlug, sub.classSlug, sub.slug, sub.id]
  );

  const displayUrl = useMemo(() => {
    return sub.boardSlug && sub.classSlug && sub.slug
      ? `syrabit.ai/${sub.boardSlug}/${sub.classSlug}/${sub.slug}`
      : `syrabit.ai/subject/${sub.id?.slice(0, 8)}`;
  }, [sub.boardSlug, sub.classSlug, sub.slug, sub.id]);

  const { sharing, share } = useShare();

  const handleShare = useCallback((e) => {
    e.preventDefault();
    const parts = [sub.name];
    if (sub.description) parts.push(sub.description);
    const meta = [sub.board_name || sub.boardName, sub.class_name || sub.className, sub.stream_name || sub.streamName].filter(Boolean).join(' · ');
    if (meta) parts.push(meta);
    const chCount = chapters.length || sub.chapter_count || sub.chapterCount || 0;
    if (chCount > 0) parts.push(`${chCount} chapters`);
    if (sub.tags?.length) parts.push(`Topics: ${sub.tags.join(', ')}`);
    parts.push('Study on Syrabit.ai');
    share(sub.name, subjectLandingPath, { text: parts.join('\n') });
  }, [sub, chapters.length, subjectLandingPath, share]);

  const handlePrefetch = useCallback(() => {
    if (sub.boardSlug && sub.classSlug && sub.slug) {
      prefetchSubjectData(queryClient, sub.boardSlug, sub.classSlug, sub.slug);
    }
  }, [queryClient, sub.boardSlug, sub.classSlug, sub.slug]);

  const SECTIONS = useMemo(() => {
    const notesChs = chapters.filter(ch => !ch.content_type || (ch.content_type !== 'qa' && ch.content_type !== 'question_paper'));
    const qaChs = chapters.filter(ch => ch.content_type === 'qa');
    const pyqChs = chapters.filter(ch => ch.content_type === 'question_paper');
    return [
      { key: 'notes', label: isAs ? 'টোকা' : 'Notes', chapters: notesChs, accent: '#7c3aed', bg: 'rgba(139,92,246,0.08)' },
      { key: 'qa', label: isAs ? 'প্ৰশ্ন' : 'Questions', chapters: qaChs, accent: '#2563eb', bg: 'rgba(37,99,235,0.08)' },
      { key: 'question_paper', label: isAs ? 'পিৱাইকিউ' : 'PYQs', chapters: pyqChs, accent: '#d97706', bg: 'rgba(217,119,6,0.08)' },
    ];
  }, [chapters, isAs]);

  const [expandedSection, setExpandedSection] = useState('notes');
  const activeSection = expandedSection ?? 'notes';
  const [showAllInSection, setShowAllInSection] = useState(false);

  return (
    <div
      className="w-full rounded-2xl overflow-hidden transition-all duration-300 group/card hover:-translate-y-0.5 relative cursor-pointer"
      style={{
        background: 'var(--card)',
        border: isSaved
          ? '1px solid rgba(139,92,246,0.40)'
          : '1px solid rgba(139,92,246,0.10)',
        boxShadow: isSaved
          ? '0 0 32px rgba(139,92,246,0.15), 0 8px 32px rgba(0,0,0,0.08)'
          : '0 2px 12px rgba(0,0,0,0.06)',
        animationDelay: `${index * 50}ms`,
        minHeight: '420px',
        contain: 'layout style',
      }}
      data-testid="library-subject-card"
      data-subject-id={sub.id}
    >
      {/* Header bar — same gradient style as Degree cards */}
      <div
        className="flex items-center justify-between px-3.5 py-2.5 relative z-[2]"
        style={{
          background: `linear-gradient(135deg, ${thumbColors[0]}22, ${thumbColors[1]}14)`,
          borderBottom: `1px solid ${thumbColors[0]}28`,
        }}
      >
        <div className="flex items-center gap-2">
          <div
            className="w-5 h-5 rounded-md flex items-center justify-center"
            style={{ background: `linear-gradient(135deg, ${thumbColors[0]}, ${thumbColors[1]})`, boxShadow: `0 0 8px ${thumbColors[0]}50` }}
          >
            <Layers size={10} className="text-white" />
          </div>
          <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: thumbColors[0] }}>
            {sub.streamName || sub.boardName || 'Subject'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {hasDocument && (
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold"
              style={{ background: 'rgba(16,185,129,0.10)', color: '#059669', border: '1px solid rgba(16,185,129,0.20)' }}>
              <Lock size={7} /> Doc
            </span>
          )}
          {isSaved && (
            <BookmarkCheck size={13} className="text-violet-400" style={{ filter: 'drop-shadow(0 0 4px rgba(139,92,246,0.5))' }} />
          )}
        </div>
      </div>

      {/* Subject info */}
      <div className="px-3 sm:px-4 pt-3 pb-2 relative z-[2]">
        <Link to={subjectLandingPath} className="block group/title static" aria-label={`View ${sub.name}`}>
          <span className="absolute inset-0 z-0" aria-hidden="true" />
          <div className="flex items-start gap-3 mb-2">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center text-xl shrink-0"
              style={{
                background: `linear-gradient(135deg, ${thumbColors[0]}30, ${thumbColors[1]}20)`,
                border: `1px solid ${thumbColors[0]}30`,
              }}
            >
              {sub.icon || '📚'}
            </div>
            <div className="min-w-0 flex-1">
              <h3
                className="font-bold group-hover/title:text-purple-300 transition-colors leading-tight"
                style={{ fontSize: '0.95rem', color: 'hsl(var(--foreground))' }}
              >
                {sub.name}
              </h3>
              <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 mt-0.5">
                <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded" style={{
                  background: 'rgba(139,92,246,0.12)',
                  color: 'hsl(var(--primary))',
                }}>
                  {sub.boardName}
                </span>
                <span className="text-[11px] font-medium" style={{ color: 'hsl(var(--muted-foreground))' }}>
                  {sub.className}
                </span>
                {sub.streamName && (
                  <>
                    <span className="text-[11px]" style={{ color: 'hsl(var(--muted-foreground))' }}>·</span>
                    <span className="text-[11px] font-medium" style={{ color: 'hsl(var(--muted-foreground))' }}>
                      {sub.streamName}
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>
        </Link>

        {sub.description && (
          <p className="text-xs leading-relaxed mb-1.5 sm:mb-2 line-clamp-1 sm:line-clamp-2 font-medium"
            style={{ color: 'hsl(var(--muted-foreground))' }}>
            {sub.description}
          </p>
        )}

        {visibleTags.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-2 sm:mb-3">
            {visibleTags.map((tag) => (
              <span
                key={tag}
                className="px-2 py-0.5 rounded-full text-[10px] font-semibold"
                style={{
                  color: 'hsl(var(--primary) / 0.8)',
                  background: 'rgba(139,92,246,0.06)',
                  border: '1px solid rgba(139,92,246,0.12)',
                }}
              >
                {tag}
              </span>
            ))}
            {tags.length > 3 && (
              <span className="text-[10px] px-1" style={{ color: 'hsl(var(--muted-foreground))' }}>
                +{tags.length - 3}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Chapter sections — Notes | Question & Answer | Question Paper (always 3 tabs) */}
      <div
        className="mx-3 mb-2 sm:mb-3 rounded-xl overflow-hidden relative z-[2]"
        style={{ background: 'rgba(139,92,246,0.03)', border: '1px solid rgba(139,92,246,0.08)' }}
      >
        {/* Section tab pills */}
        <div className="flex items-center gap-1.5 px-2.5 py-1.5 overflow-x-auto scrollbar-none" style={{ borderBottom: '1px solid rgba(139,92,246,0.06)' }}>
          {SECTIONS.map(sec => (
            <button
              key={sec.key}
              onClick={() => { setExpandedSection(sec.key); setShowAllInSection(false); }}
              className="flex-shrink-0 flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wide transition-all whitespace-nowrap"
              style={sec.key === activeSection
                ? { background: sec.bg, color: sec.accent, border: `1px solid ${sec.accent}40` }
                : { background: 'transparent', color: 'hsl(var(--muted-foreground))', border: '1px solid rgba(139,92,246,0.10)' }
              }
            >
              {sec.label}
              {sec.chapters.length > 0 && (
                <span className="ml-0.5 font-semibold" style={{ opacity: 0.7 }}>{sec.chapters.length}</span>
              )}
            </button>
          ))}
        </div>

        {/* Active section content */}
        {SECTIONS.filter(s => s.key === activeSection).map(section => {
          if (section.chapters.length === 0) {
            return (
              <div key={section.key} className="px-3 py-4 text-center text-[11px]" style={{ color: 'hsl(var(--muted-foreground))' }}>
                {section.key === 'qa' && (isAs ? 'প্ৰশ্ন সোনকালে আহিব' : 'Questions coming soon')}
                {section.key === 'question_paper' && (isAs ? 'পিৱাইকিউ সোনকালে আহিব' : 'PYQs coming soon')}
                {section.key === 'notes' && (isAs ? 'টোকা প্ৰস্তুত কৰা হৈছে' : 'Notes being prepared')}
              </div>
            );
          }
          const visChapters = showAllInSection ? section.chapters : section.chapters.slice(0, 3);
          const moreCount = showAllInSection ? 0 : section.chapters.length - 3;
          return (
            <div key={section.key}>
              {visChapters.map((ch, i) => {
                const effectiveSlug = ch.slug || (ch.title ? ch.title.toLowerCase().replace(/[^\p{L}\p{N}\p{M}]+/gu, '-').replace(/-{2,}/g, '-').replace(/^-+|-+$/g, '') : '');
                const hasValidLink = !!(sub.boardSlug && sub.classSlug && sub.slug && effectiveSlug);
                const hasContent = ch.notes_generated !== false;
                const chPath = hasValidLink
                  ? `/${sub.boardSlug}/${sub.classSlug}/${sub.slug}/${effectiveSlug}`
                  : subjectLandingPath;
                return (
                  <div
                    key={ch.id || i}
                    className="flex items-center gap-2 px-3 py-2.5 sm:py-2 text-xs transition-all"
                    style={{ borderBottom: i < visChapters.length - 1 ? '1px solid rgba(139,92,246,0.05)' : 'none' }}
                  >
                    <span
                      className="w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-bold shrink-0"
                      style={{ background: section.bg, color: section.accent }}
                    >
                      {i + 1}
                    </span>
                    <Link
                      to={chPath}
                      className="truncate transition-colors flex-1 font-medium"
                      title={`${ch.title} — ${sub.name}`}
                      style={{ color: section.accent, opacity: (hasValidLink && hasContent) ? 1 : 0.5 }}
                    >
                      {(isAs && ch.title_as) ? ch.title_as : ch.title}
                    </Link>
                    <ExternalLink size={10} className="shrink-0" style={{ color: 'hsl(var(--muted-foreground) / 0.2)' }} />
                  </div>
                );
              })}
              {moreCount > 0 && (
                <button
                  onClick={() => setShowAllInSection(true)}
                  className="flex items-center justify-center gap-1 px-3 py-2 text-[11px] font-medium transition-colors w-full"
                  style={{ borderTop: '1px solid rgba(139,92,246,0.06)', color: section.accent }}
                >
                  +{moreCount} {isAs ? 'আৰু' : 'more'}
                  <ChevronDown size={11} />
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* Action buttons */}
      <div
        className="grid grid-cols-2 gap-1.5 px-3 py-2.5 relative z-[2]"
        style={{ borderTop: '1px solid hsl(var(--border) / 0.3)' }}
      >
        <button
          onClick={() => { onToggleSave(sub.id); try { Analytics.subjectBookmarked(sub.name, !isSaved); } catch {} }}
          aria-label={isSaved ? `Unsave ${sub.name}` : `Save ${sub.name}`}
          className="flex items-center justify-center gap-1.5 h-11 sm:h-9 rounded-lg text-xs font-semibold transition-all duration-200 active:scale-95"
          style={
            isSaved
              ? {
                  color: 'hsl(var(--primary))',
                  background: 'rgba(139,92,246,0.10)',
                  border: '1px solid rgba(139,92,246,0.25)',
                }
              : {
                  color: 'hsl(var(--muted-foreground))',
                  background: 'transparent',
                  border: '1px solid rgba(139,92,246,0.12)',
                }
          }
          data-testid="subject-bookmark-button"
        >
          {isSaved ? <BookmarkCheck size={12} /> : <Bookmark size={12} />}
          {isSaved ? (isAs ? 'সংৰক্ষিত' : 'Saved') : (isAs ? 'সংৰক্ষণ' : 'Save')}
        </button>

        <Link
          to={subjectLandingPath}
          onMouseEnter={handlePrefetch}
          className="flex items-center justify-center gap-1.5 h-11 sm:h-9 rounded-lg text-xs font-semibold transition-all duration-200 active:scale-95 relative z-[3]"
          style={{
            color: 'hsl(var(--muted-foreground))',
            background: 'transparent',
            border: '1px solid rgba(139,92,246,0.12)',
          }}
        >
          <BookOpen size={12} />
          {isAs ? 'চাওক' : 'Browse'}
        </Link>

        <button
          onClick={() => {
            const activeSec = SECTIONS.find(s => s.key === activeSection);
            const firstChapterId = activeSec?.chapters?.[0]?.id || null;
            onAskAI(sub.id, hasDocument, sub.name, activeSection, firstChapterId);
          }}
          aria-label={`Ask AI about ${sub.name}`}
          className="flex items-center justify-center gap-1.5 h-11 sm:h-9 rounded-lg text-xs font-semibold text-white transition-all duration-200 hover:opacity-90 active:scale-95"
          style={{
            background: hasDocument
              ? 'linear-gradient(135deg, #059669, #10b981)'
              : 'linear-gradient(135deg, #7c3aed, #8b5cf6)',
            boxShadow: '0 2px 10px rgba(139,92,246,0.20)',
          }}
          data-testid="subject-ask-ai-button"
        >
          <Sparkles size={12} />
          {isAs ? 'AI সোধক' : 'Ask AI'}
        </button>

        <button
          onClick={handleShare}
          disabled={sharing}
          aria-label={`Share ${sub.name}`}
          className="flex items-center justify-center gap-1.5 h-11 sm:h-9 rounded-lg text-xs font-semibold transition-all duration-200 active:scale-95 disabled:opacity-50"
          style={{
            color: 'hsl(var(--muted-foreground))',
            background: 'transparent',
            border: '1px solid rgba(148,163,184,0.22)',
          }}
          data-testid="subject-share"
        >
          {sharing ? <Loader2 size={12} className="animate-spin" /> : <Share2 size={12} />}
          {isAs ? 'শ্বেয়াৰ' : 'Share'}
        </button>
      </div>
    </div>
  );
});

export default SubjectCard;
