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
import Analytics from '@/utils/analytics';

const THUMB_GRADIENTS = {
  math:      ['#4f46e5', '#7c3aed'],
  physics:   ['#2563eb', '#0891b2'],
  chemistry: ['#059669', '#0d9488'],
  biology:   ['#16a34a', '#15803d'],
  arts:      ['#d97706', '#b45309'],
  science:   ['#7c3aed', '#4f46e5'],
};

export function getSubjectLandingPath(sub = {}) {
  const boardSlug = sub.boardSlug || sub.board_slug;
  const classSlug = sub.classSlug || sub.class_slug;
  const subjectSlug = sub.slug || sub.subject_slug;
  return boardSlug && classSlug && subjectSlug
    ? `/${boardSlug}/${classSlug}/${subjectSlug}`
    : `/subject/${sub.id}`;
}

const MAKERS_SUBJECT_SLUG = 'makers-of-modern-assam-mdc-01013';
const MAKERS_ASSAMESE_TEXT = {
  name: 'আধুনিক অসমৰ নিৰ্মাতা সকল',
  description: 'গুৱাহাটী বিশ্ববিদ্যালয়ৰ NEP 2020-ৰ অধীনৰ চাৰি বছৰীয়া স্নাতক কাৰ্যসূচী (FYUGP), প্ৰথম ষাণ্মাসিকৰ MDC-01013 পাঠ্যক্ৰম: আধুনিক অসমীয়া ভাষা, সাহিত্য, সংগীত-সংস্কৃতি, ৰাজনৈতিক ভিত্তি, জনজাতীয় অধিকাৰ আৰু নাৰী শিক্ষা।',
};
const ASSAMESE_UNIT_TITLES = {
  'unit-1-language-literature-culture': 'গোট ১: ভাষা, সাহিত্য আৰু সংস্কৃতি',
  'unit-2-political-foundations': 'গোট ২: ৰাজনৈতিক ভিত্তিপ্ৰতিষ্ঠাকাৰীসকল',
  'unit-3-women-education-politics-literature': 'গোট ৩: নাৰী শিক্ষা, ৰাজনীতি আৰু সাহিত্য',
};
const ASSAMESE_UNIT_TITLES_BY_ENGLISH_TITLE = {
  'unit i language literature culture': ASSAMESE_UNIT_TITLES['unit-1-language-literature-culture'],
  'unit ii framers of the political foundations': ASSAMESE_UNIT_TITLES['unit-2-political-foundations'],
  'unit iii women s education politics and literature': ASSAMESE_UNIT_TITLES['unit-3-women-education-politics-literature'],
  'unit 1 language literature culture': ASSAMESE_UNIT_TITLES['unit-1-language-literature-culture'],
  'unit 2 framers of the political foundations': ASSAMESE_UNIT_TITLES['unit-2-political-foundations'],
  'unit 3 women s education politics and literature': ASSAMESE_UNIT_TITLES['unit-3-women-education-politics-literature'],
};
const ASSAMESE_CONTENT_TITLES = {
  'question-bank-units-1-3': 'প্ৰশ্নসম্ভাৰ — গোট ১–৩',
  'question bank units i iii': 'প্ৰশ্নসম্ভাৰ — গোট ১–৩',
  'question bank units 1 3': 'প্ৰশ্নসম্ভাৰ — গোট ১–৩',
};
const ASSAMESE_HIERARCHY_LABELS = {
  board: { degree: 'স্নাতক' },
  class: {
    '1st': 'প্ৰথম ষাণ্মাসিক',
    '1st semester': 'প্ৰথম ষাণ্মাসিক',
    '2nd': 'দ্বিতীয় ষাণ্মাসিক',
    '2nd semester': 'দ্বিতীয় ষাণ্মাসিক',
    '3rd': 'তৃতীয় ষাণ্মাসিক',
    '3rd semester': 'তৃতীয় ষাণ্মাসিক',
    '4th': 'চতুৰ্থ ষাণ্মাসিক',
    '4th semester': 'চতুৰ্থ ষাণ্মাসিক',
    '5th': 'পঞ্চম ষাণ্মাসিক',
    '5th semester': 'পঞ্চম ষাণ্মাসিক',
    '6th': 'ষষ্ঠ ষাণ্মাসিক',
    '6th semester': 'ষষ্ঠ ষাণ্মাসিক',
  },
  stream: {
    ba: 'বি.এ.',
    'b.a.': 'বি.এ.',
    bsc: 'বি.এছ.চি.',
    'b.sc': 'বি.এছ.চি.',
    'b.sc.': 'বি.এছ.চি.',
    bcom: 'বি.কম.',
    'b.com': 'বি.কম.',
    'b.com.': 'বি.কম.',
  },
};

export function getSubjectCardName(sub = {}, isAs = false) {
  if (!isAs) return sub.name || '';
  return sub.name_as || (sub.slug === MAKERS_SUBJECT_SLUG ? MAKERS_ASSAMESE_TEXT.name : sub.name) || '';
}

export function getSubjectCardDescription(sub = {}, isAs = false) {
  if (!isAs) return sub.description || '';
  if (sub.slug === MAKERS_SUBJECT_SLUG) {
    if (sub.description_as && !/\bFYUGP\b/i.test(sub.description_as)) return sub.description_as;
    return MAKERS_ASSAMESE_TEXT.description;
  }
  return sub.description_as || sub.description || '';
}

export function getSubjectCardHierarchyLabel(value = '', kind, isAs = false) {
  if (!isAs || !value) return value;
  const translations = ASSAMESE_HIERARCHY_LABELS[kind] || {};
  const normalized = value.trim().toLowerCase().replace(/\s+/g, ' ');
  const withoutPeriods = normalized.replaceAll('.', '');
  return translations[normalized] || translations[withoutPeriods] || value;
}

export function getSubjectCardChapterTitle(chapter = {}, isAs = false) {
  if (!isAs) return chapter.title || '';
  const normalizedTitle = (chapter.title || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  return chapter.title_as
    || ASSAMESE_UNIT_TITLES[chapter.slug]
    || ASSAMESE_UNIT_TITLES_BY_ENGLISH_TITLE[normalizedTitle]
    || ASSAMESE_CONTENT_TITLES[chapter.slug]
    || ASSAMESE_CONTENT_TITLES[normalizedTitle]
    || chapter.title
    || '';
}

export function getSubjectCardPyqTitle(paper = {}, index = 0, isAs = false) {
  if (!isAs) return paper.name || `Paper ${index + 1}`;
  if (paper.name_as) return paper.name_as;
  if (paper.year) return `বিগত বৰ্ষৰ প্ৰশ্নসমূহ — ${paper.year}`;
  return 'প্ৰশ্নকাকত';
}

const SubjectCard = memo(function SubjectCard({ sub, chapters = [], isSaved, onToggleSave, onAskAI, index }) {
  const queryClient = useQueryClient();
  const { contentLang } = useContentLang();
  const isAs = contentLang === 'as';
  const displayName = getSubjectCardName(sub, isAs);
  const displayDescription = getSubjectCardDescription(sub, isAs);
  const displayBoardName = getSubjectCardHierarchyLabel(sub.boardName, 'board', isAs);
  const displayClassName = getSubjectCardHierarchyLabel(sub.className, 'class', isAs);
  const displayStreamName = getSubjectCardHierarchyLabel(sub.streamName, 'stream', isAs);
  const thumbColors = useMemo(() => THUMB_GRADIENTS[sub.gradient] || THUMB_GRADIENTS.math, [sub.gradient]);
  const tags = useMemo(() => Array.isArray(sub.tags) ? sub.tags : [], [sub.tags]);
  const visibleTags = useMemo(() => tags.slice(0, 3), [tags]);
  const chapterCount = useMemo(() => chapters.length || sub.chapter_count || sub.chapterCount || 0, [chapters.length, sub.chapter_count, sub.chapterCount]);
  const hasDocument = useMemo(() => sub.has_document === true, [sub.has_document]);

  const subjectLandingPath = useMemo(
    () => getSubjectLandingPath(sub),
    [sub],
  );

  const displayUrl = useMemo(() => {
    return `syrabit.ai${subjectLandingPath}`;
  }, [subjectLandingPath]);

  const { sharing, share } = useShare();

  const handleShare = useCallback((e) => {
    e.preventDefault();
    const parts = [displayName];
    if (displayDescription) parts.push(displayDescription);
    const meta = [
      getSubjectCardHierarchyLabel(sub.board_name || sub.boardName, 'board', isAs),
      getSubjectCardHierarchyLabel(sub.class_name || sub.className, 'class', isAs),
      getSubjectCardHierarchyLabel(sub.stream_name || sub.streamName, 'stream', isAs),
    ].filter(Boolean).join(' · ');
    if (meta) parts.push(meta);
    const chCount = chapters.length || sub.chapter_count || sub.chapterCount || 0;
    if (chCount > 0) parts.push(isAs ? `${chCount}টা অধ্যায়` : `${chCount} chapters`);
    if (sub.tags?.length) parts.push(`${isAs ? 'বিষয়সমূহ' : 'Topics'}: ${sub.tags.join(', ')}`);
    parts.push(isAs ? 'Syrabit.ai-ত অধ্যয়ন কৰক' : 'Study on Syrabit.ai');
    share(displayName, subjectLandingPath, { text: parts.join('\n') });
  }, [sub, chapters.length, subjectLandingPath, share, displayName, displayDescription, isAs]);

  const handlePrefetch = useCallback(() => {
    if (sub.boardSlug && sub.classSlug && sub.slug) {
      prefetchSubjectData(queryClient, sub.boardSlug, sub.classSlug, sub.slug);
    }
  }, [queryClient, sub.boardSlug, sub.classSlug, sub.slug]);

  const SECTIONS = useMemo(() => {
    const QA_TYPES = new Set(['qa', 'important_questions', 'chapter_question', 'mcqs']);
    const PYQ_TYPES = new Set(['question_paper', 'pyq']);
    // Notes section: chapters that aren't dedicated Q&A-only chapters AND have actual content.
    // notes_generated=false means the ingestion pipeline hasn't produced notes yet — hide those
    // stubs so students never land on a blank page. They auto-appear once ingestion runs.
    const notesChs = chapters.filter(ch =>
      (!ch.content_type || (!QA_TYPES.has(ch.content_type) && !PYQ_TYPES.has(ch.content_type))) && ch.notes_generated
      && (isAs ? ch.has_assamese : true)
    );
    // Questions section: dedicated Q&A chapters (content_type) OR notes chapters that
    // also have Q&A content (has_qa=true, populated by the ingestion pipeline).
    // "Full Book" is a generic ingestion artifact title used when a PDF has no TOC —
    // hide it from the Q section whenever the subject also has properly-named Q&A
    // chapters, so the Questions tab doesn't mirror the Notes tab with "Full Book" ×N.
    const _GENERIC_TITLE = /^full\s+book$/i;
    const _allQaChs = chapters.filter(ch => QA_TYPES.has(ch.content_type) || ch.has_qa);
    const _hasNamedQa = _allQaChs.some(ch => !_GENERIC_TITLE.test((ch.title || '').trim()));
    const qaChs = _hasNamedQa
      ? _allQaChs.filter(ch => !_GENERIC_TITLE.test((ch.title || '').trim()))
      : _allQaChs;
    const pyqChs = chapters.filter(ch =>
      PYQ_TYPES.has(ch.content_type) && ch.notes_generated
      && (isAs ? ch.has_assamese : true)
    );
    // Subject-level PYQ papers — [{id, title, class_name, year, description, pages:[]}]
    const pyqGroups = (sub.pyq_papers || []).map((p, pi) => ({
      id:          p.id || `pyq-${pi}`,
      title:       getSubjectCardPyqTitle(p, pi, isAs),
      class_name:  getSubjectCardHierarchyLabel(p.class_name || '', 'class', isAs),
      year:        p.year || null,
      description: p.description || '',
      pages: (p.pages || []).map((pg, idx) => ({
        _key:    pg.id || `${p.id}-${idx}`,
        url:     pg.url,
        pageNum: idx + 1,
      })),
    })).filter((paper) => paper.pages.length > 0);
    return [
      { key: 'notes',          label: isAs ? 'টোকা' : 'Notes',     chapters: notesChs, pyqGroups: null,  accent: '#7c3aed', bg: 'rgba(139,92,246,0.08)' },
      { key: 'qa',             label: isAs ? 'প্ৰশ্ন' : 'Questions', chapters: qaChs,    pyqGroups: null,  accent: '#2563eb', bg: 'rgba(37,99,235,0.08)' },
      { key: 'question_paper', label: isAs ? 'বিগত বৰ্ষৰ প্ৰশ্ন' : 'PYQs',   chapters: pyqChs,  pyqGroups,        accent: '#d97706', bg: 'rgba(217,119,6,0.08)' },
    ];
  }, [chapters, sub.pyq_papers, isAs]);

  const _lsKey = `syrabit_section_${sub.id}`;
  const [expandedSection, setExpandedSection] = useState(() => {
    try { return localStorage.getItem(_lsKey) || 'notes'; } catch { return 'notes'; }
  });
  const activeSection = expandedSection ?? 'notes';
  const [showAllInSection, setShowAllInSection] = useState(false);
  // Which PYQ chapter row is expanded (by chapter id)
  const [expandedPyqId, setExpandedPyqId] = useState(null);

  const handleSectionChange = (key) => {
    setExpandedSection(key);
    setShowAllInSection(false);
    try { localStorage.setItem(_lsKey, key); } catch {}
  };

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
            {displayStreamName || displayBoardName || (isAs ? 'বিষয়' : 'Subject')}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {hasDocument && (
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold"
              style={{ background: 'rgba(16,185,129,0.10)', color: '#059669', border: '1px solid rgba(16,185,129,0.20)' }}>
              <Lock size={7} /> {isAs ? 'নথি' : 'Doc'}
            </span>
          )}
          {isSaved && (
            <BookmarkCheck size={13} className="text-violet-400" style={{ filter: 'drop-shadow(0 0 4px rgba(139,92,246,0.5))' }} />
          )}
        </div>
      </div>

      {/* Subject info */}
      <div className="px-3 sm:px-4 pt-3 pb-2 relative z-[2]">
        <Link to={subjectLandingPath} className="block group/title static" aria-label={`${isAs ? 'বিষয়টো চাওক' : 'View'} ${displayName}`}>
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
                {displayName}
              </h3>
              <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 mt-0.5">
                <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded" style={{
                  background: 'rgba(139,92,246,0.12)',
                  color: 'hsl(var(--primary))',
                }}>
                  {displayBoardName}
                </span>
                <span className="text-[11px] font-medium" style={{ color: 'hsl(var(--muted-foreground))' }}>
                  {displayClassName}
                </span>
                {sub.streamName && (
                  <>
                    <span className="text-[11px]" style={{ color: 'hsl(var(--muted-foreground))' }}>·</span>
                    <span className="text-[11px] font-medium" style={{ color: 'hsl(var(--muted-foreground))' }}>
                      {displayStreamName}
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>
        </Link>

        {displayDescription && (
          <p className="text-xs leading-relaxed mb-1.5 sm:mb-2 line-clamp-1 sm:line-clamp-2 font-medium"
            style={{ color: 'hsl(var(--muted-foreground))' }}>
            {displayDescription}
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
        <div
          className="flex items-center gap-1.5 px-2.5 py-1.5 overflow-x-auto scrollbar-none"
          style={{ borderBottom: '1px solid rgba(139,92,246,0.06)' }}
          role="group"
          aria-label={`${displayName} ${isAs ? 'বিষয়বস্তুৰ শাখাসমূহ' : 'content sections'}`}
        >
          {SECTIONS.map(sec => (
            <button
              type="button"
              key={sec.key}
              onClick={() => handleSectionChange(sec.key)}
              aria-pressed={sec.key === activeSection}
              className="flex-shrink-0 flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wide transition-all whitespace-nowrap"
              style={sec.key === activeSection
                ? { background: sec.bg, color: sec.accent, border: `1px solid ${sec.accent}40` }
                : { background: 'transparent', color: 'hsl(var(--muted-foreground))', border: '1px solid rgba(139,92,246,0.10)' }
              }
            >
              {sec.label}
              {(() => { const n = (sec.pyqGroups?.length || 0) + (sec.chapters?.length || 0); return n > 0 ? <span className="ml-0.5 font-semibold" style={{ opacity: 0.7 }}>{n}</span> : null; })()}
            </button>
          ))}
        </div>

        {/* Active section content */}
        {SECTIONS.filter(s => s.key === activeSection).map(section => {
          // ── PYQ section — chapter cards each holding page images ─────
          if (section.key === 'question_paper') {
            const groups = section.pyqGroups || [];
            const paperChapters = section.chapters || [];
            if (groups.length === 0 && paperChapters.length === 0) {
              return (
                <div key="question_paper" className="px-3 py-4 text-center text-[11px]" style={{ color: 'hsl(var(--muted-foreground))' }}>
                  {isAs ? 'পিৱাইকিউ সোনকালে আহিব' : 'PYQs coming soon'}
                </div>
              );
            }
            const visGroups = showAllInSection ? groups : groups.slice(0, 3);
            const moreCount = showAllInSection ? 0 : groups.length - 3;
            const visPaperChapters = showAllInSection ? paperChapters : paperChapters.slice(0, 3);
            const moreChapterCount = showAllInSection ? 0 : paperChapters.length - 3;
            return (
              <div key="question_paper">
                {visPaperChapters.map((ch, i) => {
                  const slug = ch.slug || '';
                  const targetSlug = isAs ? (ch.slug_as || slug) : slug;
                  const path = sub.boardSlug && sub.classSlug && sub.slug && targetSlug
                    ? `${isAs ? '/as' : ''}/${sub.boardSlug}/${sub.classSlug}/${sub.slug}/${targetSlug}`
                    : subjectLandingPath;
                  const title = isAs && ch.title_as ? ch.title_as : ch.title;
                  return (
                    <Link
                      key={ch.id || `pyq-chapter-${i}`}
                      to={path}
                      className="flex items-center gap-2 px-3 py-2.5 text-xs transition-colors hover:bg-amber-50/60"
                      style={{ borderBottom: '1px solid rgba(217,119,6,0.07)', color: section.accent }}
                    >
                      <BookOpen size={13} className="shrink-0" />
                      <span className="font-semibold flex-1 min-w-0 truncate">{title}</span>
                      <ExternalLink size={11} className="shrink-0 opacity-60" />
                    </Link>
                  );
                })}
                {visGroups.map((grp, i) => {
                  const isOpen = expandedPyqId === grp.id;
                  return (
                    <div key={grp.id} style={{ borderBottom: i < visGroups.length - 1 ? '1px solid rgba(217,119,6,0.07)' : 'none' }}>
                      {/* Paper row — click to expand images */}
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); setExpandedPyqId(isOpen ? null : grp.id); }}
                        aria-expanded={isOpen}
                        aria-label={`${isOpen ? (isAs ? 'সামৰি থওক' : 'Collapse') : (isAs ? 'মেলক' : 'Expand')} ${grp.title}`}
                        className="w-full flex items-center gap-2 px-3 py-2.5 sm:py-2 text-xs transition-all hover:bg-amber-50/60 active:bg-amber-100/60 text-left cursor-pointer"
                      >
                        <span
                          className="w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-bold shrink-0"
                          style={{ background: section.bg, color: section.accent }}
                        >
                          {i + 1}
                        </span>
                        <div className="flex-1 min-w-0">
                          <span className="font-semibold block truncate" style={{ color: section.accent }}>
                            {grp.title}
                          </span>
                          {(grp.year || grp.class_name) && (
                            <span className="text-[9px] block mt-0.5" style={{ color: `${section.accent}80` }}>
                              {[grp.year, grp.class_name].filter(Boolean).join(' · ')}
                            </span>
                          )}
                        </div>
                        <span className="text-[9px] shrink-0 mr-0.5 font-medium" style={{ color: `${section.accent}70` }}>
                          {isAs ? `${grp.pages.length} পৃষ্ঠা` : `${grp.pages.length}pg`}
                        </span>
                        <ChevronDown
                          size={11}
                          className="shrink-0 transition-transform duration-200"
                          style={{ color: section.accent, transform: isOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}
                        />
                      </button>

                      {/* Expanded: 2-col image grid */}
                      {isOpen && (
                        <div style={{ background: 'rgba(217,119,6,0.03)' }}>
                          {grp.pages.length === 0 ? (
                            <p className="text-center text-[10px] py-3 px-2" style={{ color: `${section.accent}60` }}>
                              {isAs ? 'এতিয়ালৈ কোনো পৃষ্ঠা আপলোড কৰা হোৱা নাই।' : 'No pages uploaded yet.'}
                            </p>
                          ) : (
                            <div className="grid grid-cols-2 gap-1.5 px-2 pb-2">
                              {grp.pages.map(page => (
                                <a
                                  key={page._key}
                                  href={page.url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="relative block rounded-lg overflow-hidden group"
                                  style={{ border: '1px solid rgba(217,119,6,0.14)' }}
                                >
                                  <img
                                    src={page.url}
                                    alt={`${isAs ? 'পৃষ্ঠা' : 'pg'} ${page.pageNum}`}
                                    className="w-full object-cover"
                                    style={{ height: 110 }}
                                    loading="lazy"
                                  />
                                  <div className="absolute inset-0 bg-black/0 group-hover:bg-black/15 transition-colors" />
                                  <span
                                    className="absolute top-1 left-1 text-[9px] font-bold text-white px-1 py-0.5 rounded"
                                    style={{ background: 'rgba(0,0,0,0.45)' }}
                                  >
                                    {isAs ? 'পৃষ্ঠা' : 'p'}{page.pageNum}
                                  </span>
                                </a>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
                {moreCount + moreChapterCount > 0 && (
                  <button
                    type="button"
                    onClick={() => setShowAllInSection(true)}
                    className="flex items-center justify-center gap-1 px-3 py-2 text-[11px] font-medium transition-colors w-full"
                    style={{ borderTop: '1px solid rgba(217,119,6,0.07)', color: section.accent }}
                  >
                    +{moreCount + moreChapterCount} {isAs ? 'আৰু' : 'more'}
                    <ChevronDown size={11} />
                  </button>
                )}
              </div>
            );
          }

          // ── Notes / Q&A sections — chapter links ───────────────────────
          if (section.chapters.length === 0) {
            return (
              <div key={section.key} className="px-3 py-4 text-center text-[11px]" style={{ color: 'hsl(var(--muted-foreground))' }}>
                {section.key === 'qa' && (isAs ? 'প্ৰশ্ন সোনকালে আহিব' : 'Questions coming soon')}
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
                // In Assamese mode, prefer the Assamese URL slug so students land on
                // a readable /as/… address instead of the English slug fallback.
                const asSlug = isAs ? (ch.slug_as || effectiveSlug) : effectiveSlug;
                const hasValidLink = !!(sub.boardSlug && sub.classSlug && sub.slug && effectiveSlug);
                const hasContent = ch.notes_generated !== false;
                const chPath = hasValidLink
                  ? (isAs
                    ? `/as/${sub.boardSlug}/${sub.classSlug}/${sub.slug}/${asSlug}`
                    : `/${sub.boardSlug}/${sub.classSlug}/${sub.slug}/${effectiveSlug}`)
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
                      title={`${getSubjectCardChapterTitle(ch, isAs)} — ${displayName}`}
                      style={{ color: section.accent, opacity: (hasValidLink && hasContent) ? 1 : 0.5 }}
                    >
                      {getSubjectCardChapterTitle(ch, isAs)}
                    </Link>
                    <ExternalLink size={10} className="shrink-0" style={{ color: 'hsl(var(--muted-foreground) / 0.2)' }} />
                  </div>
                );
              })}
              {moreCount > 0 && (
                <button
                  type="button"
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
          type="button"
          onClick={() => { onToggleSave(sub.id); try { Analytics.subjectBookmarked(sub.name, !isSaved); } catch {} }}
          aria-label={isSaved
            ? (isAs ? `${displayName} সংৰক্ষণৰ পৰা আঁতৰাওক` : `Unsave ${displayName}`)
            : (isAs ? `${displayName} সংৰক্ষণ কৰক` : `Save ${displayName}`)}
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
          type="button"
          onClick={() => {
            const activeSec = SECTIONS.find(s => s.key === activeSection);
            const firstChapterId = activeSec?.chapters?.[0]?.id || null;
            onAskAI(sub.id, hasDocument, displayName, activeSection, firstChapterId);
          }}
          aria-label={isAs ? `${displayName} বিষয়ে AI-ৰ পৰা সোধক` : `Ask AI about ${displayName}`}
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
          type="button"
          onClick={handleShare}
          disabled={sharing}
          aria-label={isAs ? `${displayName} শ্বেয়াৰ কৰক` : `Share ${displayName}`}
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
