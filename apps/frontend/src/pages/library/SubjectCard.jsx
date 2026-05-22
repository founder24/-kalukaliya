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
import { cdnImage, cdnSrcSet } from '@/utils/imageCdn';

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

  const hasWP = !!sub.thumbnailUrl;
  const [showAllChapters, setShowAllChapters] = useState(false);
  const visibleChapters = useMemo(() => showAllChapters ? chapters : chapters.slice(0, 3), [chapters, showAllChapters]);
  const moreChapters = showAllChapters ? 0 : chapters.length - 3;

  return (
    <div
      className="w-full rounded-2xl overflow-hidden transition-all duration-300 group/card hover:-translate-y-0.5 relative cursor-pointer"
      style={{
        background: sub.thumbnailUrl ? '#0a0518' : 'var(--card)',
        border: isSaved
          ? '1px solid rgba(139,92,246,0.40)'
          : '1px solid rgba(139,92,246,0.10)',
        boxShadow: isSaved
          ? '0 0 32px rgba(139,92,246,0.15), 0 8px 32px rgba(0,0,0,0.08)'
          : '0 2px 12px rgba(0,0,0,0.06)',
        animationDelay: `${index * 50}ms`,
        // Task #391: lock card height to skeleton (420px) until content
        // settles so swapping skeleton → card produces zero CLS.
        minHeight: '420px',
        contain: 'layout style',
      }}
      data-testid="library-subject-card"
      data-subject-id={sub.id}
    >
      {sub.thumbnailUrl && (
        <div className="absolute inset-0 pointer-events-none overflow-hidden rounded-2xl" style={{ zIndex: 1, aspectRatio: '4 / 3' }}>
          <img
            src={cdnImage(sub.thumbnailUrl, { width: 640 })}
            srcSet={cdnSrcSet(sub.thumbnailUrl, [320, 640, 960])}
            alt=""
            loading={index === 0 ? 'eager' : 'lazy'}
            fetchpriority={index === 0 ? 'high' : 'low'}
            decoding="async"
            width="400"
            height="300"
            sizes="(max-width: 768px) 100vw, (max-width: 1280px) 50vw, 33vw"
            className="absolute inset-0 w-full h-full object-cover"
            style={{ opacity: 0.25 }}
          />
          <div
            className="absolute inset-0"
            style={{ background: 'linear-gradient(180deg, rgba(10,5,25,0.35) 0%, rgba(10,5,25,0.50) 50%, rgba(10,5,25,0.40) 100%)' }}
          />
        </div>
      )}
      <div
        className="flex items-center justify-between px-3.5 py-2.5 relative z-[2]"
        style={{
          background: sub.thumbnailUrl ? 'transparent' : `linear-gradient(135deg, ${thumbColors[0]}22, ${thumbColors[1]}14)`,
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
          <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: hasWP ? '#ffffff' : thumbColors[0] }}>
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
                style={{
                  fontSize: '0.95rem',
                  color: hasWP ? '#ffffff' : 'hsl(var(--foreground))',
                  textShadow: hasWP ? '0 1px 4px rgba(0,0,0,0.7)' : 'none',
                }}
              >
                {sub.name}
              </h3>
              <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 mt-0.5">
                <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded" style={{
                  background: hasWP ? 'rgba(255,255,255,0.15)' : 'rgba(139,92,246,0.12)',
                  color: hasWP ? '#ffffff' : 'hsl(var(--primary))',
                }}>
                  {sub.boardName}
                </span>
                <span className="text-[11px] font-medium" style={{ color: hasWP ? 'rgba(255,255,255,0.85)' : 'hsl(var(--muted-foreground))' }}>
                  {sub.className}
                </span>
                {sub.streamName && (
                  <>
                    <span className="text-[11px]" style={{ color: hasWP ? 'rgba(255,255,255,0.70)' : 'hsl(var(--muted-foreground))' }}>·</span>
                    <span className="text-[11px] font-medium" style={{ color: hasWP ? 'rgba(255,255,255,0.85)' : 'hsl(var(--muted-foreground))' }}>
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
            style={{
              color: hasWP ? 'rgba(255,255,255,0.80)' : 'hsl(var(--muted-foreground))',
              textShadow: hasWP ? '0 1px 3px rgba(0,0,0,0.5)' : 'none',
            }}>
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
                  color: hasWP ? '#ffffff' : 'hsl(var(--primary) / 0.8)',
                  background: hasWP ? 'rgba(255,255,255,0.12)' : 'rgba(139,92,246,0.06)',
                  border: hasWP ? '1px solid rgba(255,255,255,0.20)' : '1px solid rgba(139,92,246,0.12)',
                }}
              >
                {tag}
              </span>
            ))}
            {tags.length > 3 && (
              <span className="text-[10px] px-1" style={{ color: hasWP ? 'rgba(255,255,255,0.85)' : 'hsl(var(--muted-foreground))' }}>
                +{tags.length - 3}
              </span>
            )}
          </div>
        )}

      </div>

      {visibleChapters.length > 0 && (
        <div
          className="mx-3 mb-2 sm:mb-3 rounded-xl overflow-hidden relative z-[2]"
          style={{
            background: hasWP ? 'rgba(0,0,0,0.30)' : 'rgba(139,92,246,0.03)',
            border: hasWP ? '1px solid rgba(255,255,255,0.12)' : '1px solid rgba(139,92,246,0.08)',
            backdropFilter: hasWP ? 'blur(8px)' : 'none',
          }}
        >
          <div className="relative z-10">
            <div className="flex items-center justify-between gap-1.5 px-3 py-1.5" style={{ borderBottom: hasWP ? '1px solid rgba(255,255,255,0.08)' : '1px solid rgba(139,92,246,0.06)' }}>
              <div className="flex items-center gap-1.5">
                <Layers size={11} style={{ color: hasWP ? 'rgba(255,255,255,0.70)' : undefined }} className={hasWP ? '' : 'text-purple-400/60'} />
                <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: hasWP ? 'rgba(255,255,255,0.90)' : 'hsl(var(--muted-foreground))' }}>
                  {chapterCount} {isAs ? 'পাঠ' : 'LESSONS'}
                </span>
              </div>
              {(sub.notes_count > 0 || sub.pyq_count > 0 || sub.flash_count > 0) && (
                <div className="flex items-center gap-1">
                  {sub.notes_count > 0 && chapterCount > 0 && (
                    <span
                      className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full"
                      style={{
                        background: sub.notes_pct >= 100 ? 'rgba(16,185,129,0.15)' : 'rgba(245,158,11,0.12)',
                        color: sub.notes_pct >= 100 ? '#047857' : '#92400e',
                        border: `1px solid ${sub.notes_pct >= 100 ? 'rgba(16,185,129,0.25)' : 'rgba(245,158,11,0.20)'}`,
                      }}
                    >
                      {sub.notes_count}/{chapterCount} {isAs ? 'টোকা' : 'notes'}
                    </span>
                  )}
                  {sub.pyq_count > 0 && (
                    <span
                      className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full"
                      style={{ background: 'rgba(99,102,241,0.10)', color: '#4f46e5', border: '1px solid rgba(99,102,241,0.20)' }}
                    >
                      {sub.pyq_count} {isAs ? 'পূৰ্বৰ প্ৰশ্ন' : 'PYQs'}
                    </span>
                  )}
                  {sub.flash_count > 0 && (
                    <span
                      className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full"
                      style={{ background: 'rgba(139,92,246,0.10)', color: 'hsl(var(--primary))', border: '1px solid rgba(139,92,246,0.20)' }}
                    >
                      {sub.flash_count} {isAs ? 'ফ্লেশ' : 'Flash'}
                    </span>
                  )}
                </div>
              )}
            </div>
            {visibleChapters.map((ch, i) => {
              const effectiveSlug = ch.slug || (ch.title ? ch.title.toLowerCase().replace(/[^\p{L}\p{N}\p{M}]+/gu, '-').replace(/-{2,}/g, '-').replace(/^-+|-+$/g, '') : '');
              const hasValidLink = !!(sub.boardSlug && sub.classSlug && sub.slug && effectiveSlug);
              const hasContent = ch.notes_generated !== false;
              const chPath = hasValidLink
                ? `/${sub.boardSlug}/${sub.classSlug}/${sub.slug}/${effectiveSlug}`
                : subjectLandingPath;
              return (
                <div key={ch.id || i}>
                  <div
                    className="flex items-center gap-2 px-3 py-2.5 sm:py-2 text-xs transition-all group/lesson"
                    style={{
                      borderBottom: i < visibleChapters.length - 1 ? `1px solid ${hasWP ? 'rgba(255,255,255,0.06)' : 'rgba(139,92,246,0.05)'}` : 'none',
                    }}
                  >
                    <span
                      className="w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-bold shrink-0"
                      style={{
                        background: hasWP ? 'rgba(59,130,246,0.20)' : 'rgba(139,92,246,0.10)',
                        color: hasWP ? '#60a5fa' : 'hsl(var(--primary))',
                      }}
                    >
                      {i + 1}
                    </span>
                    <Link
                      to={chPath}
                      className="truncate transition-colors flex-1 font-medium"
                      title={`${ch.title} — ${sub.name}`}
                      style={{
                        color: hasWP ? '#93c5fd' : 'hsl(var(--primary))',
                        textShadow: hasWP ? '0 1px 3px rgba(0,0,0,0.5)' : 'none',
                        opacity: (hasValidLink && hasContent) ? 1 : 0.5,
                      }}
                    >
                      {ch.title}
                    </Link>
                    <ExternalLink
                      size={10}
                      className="shrink-0 transition-colors"
                      style={{ color: hasWP ? 'rgba(147,197,253,0.40)' : 'hsl(var(--muted-foreground) / 0.2)' }}
                    />
                  </div>
                </div>
              );
            })}
            {moreChapters > 0 && (
              <button
                onClick={() => setShowAllChapters(true)}
                className="flex items-center justify-center gap-1 px-3 py-2 text-[11px] font-medium transition-colors w-full"
                style={{
                  borderTop: `1px solid ${hasWP ? 'rgba(255,255,255,0.06)' : 'rgba(139,92,246,0.06)'}`,
                  color: hasWP ? 'rgba(147,197,253,0.95)' : 'hsl(var(--primary))',
                }}
              >
                +{moreChapters} {isAs ? 'আৰু পাঠ' : 'more lessons'}
                <ChevronDown size={11} />
              </button>
            )}
          </div>
        </div>
      )}

      <div
        className="grid grid-cols-2 gap-1.5 px-3 py-2.5 relative z-[2]"
        style={{ borderTop: `1px solid ${hasWP ? 'rgba(255,255,255,0.08)' : 'hsl(var(--border) / 0.3)'}` }}
      >
        <button
          onClick={() => { onToggleSave(sub.id); try { Analytics.subjectBookmarked(sub.name, !isSaved); } catch {} }}
          aria-label={isSaved ? `Unsave ${sub.name}` : `Save ${sub.name}`}
          className="flex items-center justify-center gap-1.5 h-11 sm:h-9 rounded-lg text-xs font-semibold transition-all duration-200 active:scale-95"
          style={
            isSaved
              ? {
                  color: hasWP ? '#ffffff' : 'hsl(var(--primary))',
                  background: hasWP ? 'rgba(139,92,246,0.25)' : 'rgba(139,92,246,0.10)',
                  border: hasWP ? '1px solid rgba(139,92,246,0.50)' : '1px solid rgba(139,92,246,0.25)',
                  backdropFilter: hasWP ? 'blur(6px)' : 'none',
                }
              : {
                  color: hasWP ? 'rgba(255,255,255,0.85)' : 'hsl(var(--muted-foreground))',
                  background: hasWP ? 'rgba(255,255,255,0.08)' : 'transparent',
                  border: hasWP ? '1px solid rgba(255,255,255,0.18)' : '1px solid rgba(139,92,246,0.12)',
                  backdropFilter: hasWP ? 'blur(6px)' : 'none',
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
            color: hasWP ? 'rgba(255,255,255,0.85)' : 'hsl(var(--muted-foreground))',
            background: hasWP ? 'rgba(255,255,255,0.08)' : 'transparent',
            border: hasWP ? '1px solid rgba(255,255,255,0.18)' : '1px solid rgba(139,92,246,0.12)',
            backdropFilter: hasWP ? 'blur(6px)' : 'none',
          }}
        >
          <BookOpen size={12} />
          {isAs ? 'চাওক' : 'Browse'}
        </Link>

        <button
          onClick={() => onAskAI(sub.id, hasDocument, sub.name)}
          aria-label={`Ask AI about ${sub.name}`}
          className="flex items-center justify-center gap-1.5 h-11 sm:h-9 rounded-lg text-xs font-semibold text-white transition-all duration-200 hover:opacity-90 active:scale-95"
          style={{
            background: hasDocument
              ? 'linear-gradient(135deg, #059669, #10b981)'
              : 'linear-gradient(135deg, #7c3aed, #8b5cf6)',
            boxShadow: hasWP
              ? '0 2px 12px rgba(139,92,246,0.40)'
              : '0 2px 10px rgba(139,92,246,0.20)',
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
            color: hasWP ? 'rgba(255,255,255,0.85)' : 'hsl(var(--muted-foreground))',
            background: hasWP ? 'rgba(255,255,255,0.08)' : 'transparent',
            border: hasWP ? '1px solid rgba(255,255,255,0.18)' : '1px solid rgba(148,163,184,0.22)',
            backdropFilter: hasWP ? 'blur(6px)' : 'none',
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
