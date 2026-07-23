import { useState, useEffect, useMemo, useCallback, useRef, useDeferredValue, useTransition } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { Search, Bookmark, BookOpen } from './library/icons';
import { getRecentChapters } from '@/utils/recentChapters';

import PageMeta from '@/components/seo/PageMeta';
import { Analytics } from '@/utils/analytics';
import { AppLayout } from '@/components/layout/AppLayout';
import { useAuth } from '@/context/AuthContext';
import { useContentLang } from '@/context/LanguageContext';
import {
  useLibraryBundle, useLibraryBundleSlim, useLibraryBundleBoot, useSavedSubjects,
} from '@/hooks/useContent';
import { useToggleSavedSubject } from '@/hooks/useUser';
import SubjectCard from './library/SubjectCard';
import VirtualSubjectGrid from './library/VirtualSubjectGrid';
import LibrarySkeleton from './library/LibrarySkeleton';
import FilterChip from './library/FilterChip';
import ScrollableFilterRow from './library/ScrollableFilterRow';
import CmsDocsSection from './library/CmsDocsSection';
import CmsPostsGrid from './library/CmsPostsGrid';
import QuestionPapersSection from './library/QuestionPapersSection';

// ─────────────────────────────────────────────────────────────────────────────
// AD POLICY: /library and its /browser alias are intentionally AD-FREE in the
// Task #526 rollout. Do NOT import <AdSlot /> or any ad-network script here.
// ─────────────────────────────────────────────────────────────────────────────

const _t = {
  en: {
    heading: 'Educational Browser',
    browse: (s, c) => `Browse ${s} subjects · ${c} chapters`,
    searchPlaceholder: 'Search subjects, topics, chapters...',
    clear: 'Clear',
    all: 'All',
    saved: 'Saved',
    pyq: 'Previous Year Question Papers',
    noResultsTitle: 'No subjects found',
    noResultsDesc: 'Try a different search term or filter',
    savedSignIn: 'Sign in to see saved subjects',
    savedSignInDesc: 'Create a free account to bookmark subjects and track your progress',
    signUpFree: 'Sign Up Free',
    noSaved: 'No saved subjects yet',
    noSavedDesc: 'Tap the bookmark icon on any subject to save it here',
    browseAll: 'Browse all subjects',
    lessons: 'LESSONS',
    notes: 'notes',
  },
  as: {
    heading: 'শৈক্ষিক ব্ৰাউজাৰ',
    browse: (s, c) => `${s} টা বিষয় · ${c} টা অধ্যায় চাওক`,
    searchPlaceholder: 'বিষয়, বিষয়বস্তু, অধ্যায় সন্ধান কৰক...',
    clear: 'মচক',
    all: 'সকলো',
    saved: 'সংৰক্ষিত',
    pyq: 'পূৰ্বৰ বছৰৰ প্ৰশ্নকাকত',
    noResultsTitle: 'কোনো বিষয় পোৱা নগ\'ল',
    noResultsDesc: 'এটা বেলেগ সন্ধান শব্দ বা ফিল্টাৰ চেষ্টা কৰক',
    savedSignIn: 'সংৰক্ষিত বিষয় চাবলৈ চাইন ইন কৰক',
    savedSignInDesc: 'বিষয় বুকমাৰ্ক কৰিবলৈ বিনামূলীয়া একাউণ্ট তৈয়াৰ কৰক',
    signUpFree: 'বিনামূলীয়া চাইন আপ',
    noSaved: 'এতিয়াও কোনো সংৰক্ষিত বিষয় নাই',
    noSavedDesc: 'ইয়াত সংৰক্ষণ কৰিবলৈ যিকোনো বিষয়ত বুকমাৰ্ক আইকন টিপক',
    browseAll: 'সকলো বিষয় চাওক',
    lessons: 'পাঠ',
    notes: 'টোকা',
  },
};

function LazyOnVisible({ children }) {
  const [visible, setVisible] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: '200px' }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={ref}>
      {visible ? children : null}
    </div>
  );
}

const STREAM_CHIPS_EN = [
  { id: 'all', label: 'All' },
  { id: 'saved', label: '★ Saved' },
];
const STREAM_CHIPS_AS = [
  { id: 'all', label: 'সকলো' },
  { id: 'saved', label: '★ সংৰক্ষিত' },
];


function getOnboardingProfile() {
  try { const raw = localStorage.getItem('syrabit:onboarding'); return raw ? JSON.parse(raw) : null; } catch { return null; }
}

export default function LibraryPage() {
  const navigate = useNavigate();
  const { user, authChecked } = useAuth();
  const { contentLang, switchLang } = useContentLang();
  const t = _t[contentLang] || _t.en;

  const [searchQuery, setSearchQuery]   = useState('');
  const [activeFilter, setActiveFilter] = useState('all');
  const deferredQuery = useDeferredValue(searchQuery);
  const [, startTransition] = useTransition();

  // Tier 1 — slim metadata: tiny, always-fetched first paint payload.
  const { data: slimBundle, isLoading: slimLoading, refetch: refetchSlim } = useLibraryBundleSlim();

  // Tier 2 — boot bundle: slim metadata + chapters scoped to the user's
  // active board only. Fetched in parallel with slim when an onboarding
  // board is known. ~150-300KB instead of the ~1MB full bundle, so chapter
  // counts and chapter-search work for the user's own board without
  // dragging LCP. Anonymous visitors with no onboarding skip this tier.
  // Tracked as state (not just a useMemo) so cross-tab onboarding
  // completion via the `storage` event re-fires the boot fetch without a
  // hard refresh.
  const [onboardingProfile, setOnboardingProfile] = useState(getOnboardingProfile);
  useEffect(() => {
    const sync = (e) => {
      if (e && e.key && e.key !== 'syrabit:onboarding') return;
      setOnboardingProfile(getOnboardingProfile());
    };
    window.addEventListener('storage', sync);
    // Also listen for in-tab completion (OnboardingPage doesn't fire a
    // storage event in the same window).
    window.addEventListener('syrabit:onboarding-updated', sync);
    return () => {
      window.removeEventListener('storage', sync);
      window.removeEventListener('syrabit:onboarding-updated', sync);
    };
  }, []);
  const activeBoardId = useMemo(
    () => onboardingProfile?.board_id || user?.board_id || null,
    [onboardingProfile?.board_id, user?.board_id],
  );
  const { data: bootBundle } = useLibraryBundleBoot(activeBoardId, !!activeBoardId);

  // Tier 3 — full bundle (every board's chapters, ~1MB). Required for
  // cross-board chapter search. Deferred behind interaction or a long
  // timeout so it never competes with first paint.
  const [chaptersReady, setChaptersReady] = useState(false);
  useEffect(() => {
    if (!slimBundle || chaptersReady) return;
    const fire = () => { setChaptersReady(true); cleanup(); };
    const events = ['pointerdown', 'keydown', 'scroll', 'touchstart'];
    const opts = { passive: true, capture: true, once: true };
    events.forEach((ev) => window.addEventListener(ev, fire, opts));
    const fallback = setTimeout(fire, 6000);
    function cleanup() {
      events.forEach((ev) => window.removeEventListener(ev, fire, { capture: true }));
      clearTimeout(fallback);
    }
    return cleanup;
  }, [slimBundle, chaptersReady]);
  const { data: fullBundle, isFetching, refetch: refetchBundle } = useLibraryBundle(chaptersReady);

  // Prefer richest available payload for metadata; merge chapters from the
  // tier(s) that have them so the active-board chapter UI lights up as
  // soon as boot lands, even before the full bundle arrives.
  const bundle = fullBundle || bootBundle || slimBundle;
  const bundleLoading = slimLoading;
  const bundleError = !bundle && !slimLoading;
  const subjects    = bundle?.subjects  || [];
  const boards      = bundle?.boards    || [];
  const classes     = bundle?.classes   || [];
  const streams     = bundle?.streams   || [];
  const allChapters = fullBundle?.chapters || bootBundle?.chapters || [];
  // Sum chapter_count from the slim bundle's subjects to show the correct
  // total immediately on first render — before the full chapter list lands.
  // Prevents the "Browse 109 subjects · 0 chapters" → "480 chapters" CLS.
  const slimChapterCount = useMemo(
    () => subjects.reduce((sum, s) => sum + (s.chapter_count || 0), 0),
    [subjects],
  );
  const { data: savedSubjects = [] } = useSavedSubjects(user);
  const toggleSaved = useToggleSavedSubject();
  const handleToggleSave = useCallback((id) => toggleSaved.mutate(id), [toggleSaved]);

  useEffect(() => {
    const handleContentUploaded = () => { refetchBundle(); };
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') refetchBundle();
    };
    window.addEventListener('content-uploaded', handleContentUploaded);
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      window.removeEventListener('content-uploaded', handleContentUploaded);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [refetchBundle]);

  const streamMap = useMemo(() => new Map(streams.map(s => [s.id, s])), [streams]);
  const classMap = useMemo(() => new Map(classes.map(c => [c.id, c])), [classes]);
  const boardMap = useMemo(() => new Map(boards.map(b => [b.id, b])), [boards]);

  const chaptersBySubject = useMemo(() => {
    const map = new Map();
    for (const ch of allChapters) {
      if (!ch.subject_id) continue;
      if (!map.has(ch.subject_id)) map.set(ch.subject_id, []);
      map.get(ch.subject_id).push(ch);
    }
    return map;
  }, [allChapters]);

  const enrichedSubjects = useMemo(() => {
    return subjects.map((sub) => {
      const stream = streamMap.get(sub.stream_id);
      const cls = classMap.get(stream?.class_id);
      const board = boardMap.get(cls?.board_id);
      return {
        ...sub,
        boardName: board?.name || '',
        className: cls?.name || '',
        streamName: stream?.name || '',
        boardSlug: board?.slug || '',
        classSlug: cls?.slug || '',
        streamSlug: stream?.slug || '',
      };
    });
  }, [subjects, streamMap, classMap, boardMap]);

  const savedSubjectsSet = useMemo(() => new Set(savedSubjects), [savedSubjects]);
  const dynamicStreamChips = useMemo(() => {
    const streamSlugs = new Set();
    const chips = [];
    for (const sub of enrichedSubjects) {
      if (sub.streamSlug && !streamSlugs.has(sub.streamSlug)) {
        streamSlugs.add(sub.streamSlug);
        chips.push({ id: sub.streamSlug, label: sub.streamName || sub.streamSlug });
      }
    }
    return chips;
  }, [enrichedSubjects]);

  const baseChips = contentLang === 'as' ? STREAM_CHIPS_AS : STREAM_CHIPS_EN;
  const allStreamChips = useMemo(() => [...baseChips, ...dynamicStreamChips], [baseChips, dynamicStreamChips]);

  const filteredSubjects = useMemo(() => {
    const q = deferredQuery.trim().toLowerCase();
    const seen = new Set();
    return enrichedSubjects.filter((sub) => {
      if (seen.has(sub.id)) return false;
      seen.add(sub.id);
      if (sub.status && !['published', 'active'].includes(sub.status)) return false;
      if (activeFilter === 'saved') {
        if (!savedSubjectsSet.has(sub.id)) return false;
      } else if (activeFilter !== 'all') {
        if (sub.streamSlug !== activeFilter) return false;
      }
      if (q) {
        const subChapters = chaptersBySubject.get(sub.id) || [];
        const inName    = sub.name?.toLowerCase().includes(q);
        const inDesc    = sub.description?.toLowerCase().includes(q);
        const inTags    = Array.isArray(sub.tags) && sub.tags.some((t) => t.toLowerCase().includes(q));
        const inClass   = sub.className?.toLowerCase().includes(q);
        const inStream  = sub.streamName?.toLowerCase().includes(q);
        const inBoard   = sub.boardName?.toLowerCase().includes(q);
        const inChapter = subChapters.some((ch) => ch.title?.toLowerCase().includes(q));
        if (!inName && !inDesc && !inTags && !inClass && !inStream && !inBoard && !inChapter) return false;
      }
      return true;
    });
  }, [enrichedSubjects, activeFilter, deferredQuery, savedSubjectsSet, chaptersBySubject]);

  const totalSeoTopics = useMemo(() => {
    return enrichedSubjects.reduce((sum, s) => sum + (s.seo_stats?.topic_count || 0), 0);
  }, [enrichedSubjects]);

  // JSON-LD now emitted via PageMeta (pageType="library"); see src/lib/jsonld.js

  const handleAskAI = useCallback((subjectId, hasDocument = false, subjectName = '', section = null, chapterId = null) => {
    try { Analytics.chatStart(subjectId, subjectName, 'openai/gpt-oss-20b'); } catch {}
    const params = new URLSearchParams({ subject: subjectId });
    if (hasDocument) params.set('document_id', subjectId);
    if (section) params.set('section', section);
    if (chapterId) params.set('chapter', chapterId);
    const chatContext = {
      subjectId,
      subjectName,
      sourceTitle: subjectName || '',
      contentMode: section || null,
    };
    navigate(`/chat?${params.toString()}`, { state: { chatContext } });
  }, [navigate]);

  const handleResetFilters = useCallback(() => { setSearchQuery(''); setActiveFilter('all'); }, []);

  const searchTimerRef = useRef(null);
  const handleSearchChange = useCallback((e) => {
    const val = e.target.value;
    setSearchQuery(val);
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    if (val.trim().length >= 2) {
      searchTimerRef.current = setTimeout(() => {
        const q = val.trim().toLowerCase();
        const count = enrichedSubjects.filter(s =>
          (s.name || '').toLowerCase().includes(q) ||
          (s.description || '').toLowerCase().includes(q) ||
          (s.topics || []).some(t => (t.name || '').toLowerCase().includes(q))
        ).length;
        Analytics.searchUsed(val.trim(), count);
      }, 1000);
    }
  }, [enrichedSubjects]);
  const handleFilterChange = useCallback((filterId) => {
    startTransition(() => setActiveFilter(filterId));
  }, []);
  const handleSearchClear = useCallback(() => setSearchQuery(''), []);

  // Task #391: drop the chunk-based progressive reveal in favour of always
  // using the true windowed renderer below. Keeps initial DOM bounded to
  // viewport+overscan rows even on small catalogues, which fixes the
  // 2,763 mobile DOM-node count Lighthouse was reporting and removes the
  // forced reflow that the IntersectionObserver sentinel triggered as
  // each new chunk mounted.
  const VIRTUAL_CHUNK = 6;
  const [renderLimit, setRenderLimit] = useState(VIRTUAL_CHUNK);
  const sentinelRef = useRef(null);
  const [scrollContainerEl, setScrollContainerEl] = useState(null);
  // LCP fix: only virtualise once we have a real scroll container. During
  // SSR (scrollContainerEl === null) @tanstack/react-virtual cannot measure
  // rows and renders nothing — the subject grid is blank in the prerendered
  // HTML, so the LCP text element never paints until React finishes hydrating
  // (~3.3 s). By falling through to the non-virtual <SubjectCard> path when
  // the container is null, the first VIRTUAL_CHUNK cards are baked into the
  // SSR snapshot, client initial render matches exactly (no hydration
  // mismatch), and the LCP element paints at FCP time (~2.4 s) not ~3.8 s.
  // After the scroll-container ref fires (post-mount), useVirtualGrid becomes
  // true and the virtualiser takes over for smooth long-list scrolling.
  const useVirtualGrid = filteredSubjects.length > 0 && scrollContainerEl !== null;
  useEffect(() => { setRenderLimit(VIRTUAL_CHUNK); }, [activeFilter, deferredQuery]);
  useEffect(() => {
    // The legacy progressive-reveal sentinel is only needed for the
    // non-virtualized path — when the true virtualizer is active it owns
    // window/scroll behavior, so we skip the IntersectionObserver work
    // entirely to cut redundant effects.
    if (useVirtualGrid) return;
    if (filteredSubjects.length <= renderLimit) return;
    const el = sentinelRef.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) {
        startTransition(() => setRenderLimit((n) => n + VIRTUAL_CHUNK));
      }
    }, { rootMargin: '600px' });
    io.observe(el);
    return () => io.disconnect();
  }, [filteredSubjects.length, renderLimit, useVirtualGrid]);
  // Track hydration so localStorage-dependent ranking is deferred until
  // after React has adopted the SSR DOM. The SSR renders subjects in API
  // order (no localStorage data available). If the client applies user-
  // specific ranking on the very first render, the subject order differs
  // from the SSR HTML → React reconciles the entire card grid → the LCP
  // element is "updated" by React at ~3.3 s even though it was already in
  // the prerendered HTML. Using filteredSubjects directly until hydrated
  // ensures SSR and first client render produce identical output.
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    // Low-priority update — does not block paint or input handling.
    startTransition(() => setHydrated(true));
  }, []);

  // Rank filtered subjects: recently-visited subjects bubble up, then saved,
  // then subjects that appear in the first 8 chapters (trending proxy).
  const rankedSubjects = useMemo(() => {
    if (!filteredSubjects.length) return filteredSubjects;
    // Skip localStorage-based ranking until after hydration (see comment above).
    if (!hydrated) return filteredSubjects;
    const recentChapters = getRecentChapters();
    // paths are /{board}/{class}/{subjectSlug}/{chapter} — slug is index 2
    const recentSubjectSlugs = new Set(
      recentChapters.flatMap((ch) => {
        const parts = (ch.path || '').split('/').filter(Boolean);
        return parts.length >= 3 ? [parts[2]] : [];
      })
    );
    const trendingSubjectIds = new Set(
      allChapters.slice(0, 8).map((ch) => ch.subject_id).filter(Boolean)
    );
    const score = (sub) => {
      let s = 0;
      if (recentSubjectSlugs.has(sub.slug)) s += 100;
      if (savedSubjectsSet.has(sub.id)) s += 20;
      if (trendingSubjectIds.has(sub.id)) s += 10;
      return s;
    };
    const hasBoost = filteredSubjects.some((s) => score(s) > 0);
    if (!hasBoost) return filteredSubjects;
    return [...filteredSubjects].sort((a, b) => score(b) - score(a));
  }, [hydrated, filteredSubjects, allChapters, savedSubjectsSet]);

  const visibleSubjects = useMemo(
    () => rankedSubjects.slice(0, renderLimit),
    [rankedSubjects, renderLimit]
  );

  const seoTitle = 'Assam Board Subject Library — Notes, MCQs, Definitions & Exam Prep';
  const seoDescription = `Explore ${subjects.length || ''} Assam Board subjects (AHSEC, SEBA, Degree) with ${totalSeoTopics || ''} study topics. AI-powered notes, MCQs, definitions, important questions, and exam prep for Assam students.`.replace(/  +/g, ' ').trim();
  const seoKeywords = 'Assam Board study material, AHSEC notes, SEBA notes, Class 11 notes Assam, Class 12 notes Assam, MCQs Assam Board, definitions, important questions, exam preparation Assam, Syrabit';

  if (bundleLoading) {
    return (
      <AppLayout pageTitle="Library" hideNavbar>
        <PageMeta
          title={seoTitle}
          description="Explore Assam Board Class 11-12 and Degree subjects. AI-powered notes, MCQs, definitions, and exam preparation for Assam students."
          url="https://syrabit.ai/library"
          keywords={seoKeywords}
        />
        <LibrarySkeleton />
      </AppLayout>
    );
  }

  if (bundleError && !bundle) {
    return (
      <AppLayout pageTitle="Library" hideNavbar>
        <PageMeta title={seoTitle} url="https://syrabit.ai/library" />
        <div className="flex flex-col items-center justify-center min-h-[60vh] px-4 text-center">
          <div className="w-16 h-16 rounded-2xl flex items-center justify-center mx-auto mb-5" style={{ background: 'rgba(239,68,68,0.1)' }}>
            <BookOpen size={28} className="text-red-400" />
          </div>
          <h2 className="text-lg font-semibold text-foreground mb-2">{contentLang === 'as' ? 'লাইব্ৰেৰী ল\'ড কৰিব পৰা নগ\'ল' : 'Failed to load library'}</h2>
          <p className="text-muted-foreground text-sm mb-6 max-w-xs">
            {contentLang === 'as' ? 'ছাৰ্ভাৰৰ সৈতে সংযোগ কৰিব পৰা নগ\'ল। আপোনাৰ সংযোগ পৰীক্ষা কৰি পুনৰ চেষ্টা কৰক।' : 'We couldn\'t reach the server. Please check your connection and try again.'}
          </p>
          <button
            onClick={() => { refetchSlim(); refetchBundle(); }}
            className="h-11 px-5 rounded-xl text-sm font-medium text-white bg-violet-600 hover:bg-violet-500 transition-all flex items-center gap-2 active:scale-95"
          >
            {contentLang === 'as' ? 'পুনৰ চেষ্টা কৰক' : 'Try Again'}
          </button>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout pageTitle="Library" hideNavbar>
      <PageMeta
        title={seoTitle}
        description={seoDescription}
        url="https://syrabit.ai/library"
        keywords={seoKeywords}
        pageType="library"
        pageData={{ subjects: filteredSubjects }}
      />
      <div className="flex flex-col h-full w-full overflow-hidden">

        <div
          className="sticky top-0 shrink-0 w-full z-20"
          style={{
            background: 'var(--background)',
            backdropFilter: 'blur(16px)',
            WebkitBackdropFilter: 'blur(16px)',
            borderBottom: '1px solid rgba(139,92,246,0.08)',
            contain: 'layout',
          }}
        >
          <div className="w-full max-w-6xl mx-auto px-3 sm:px-4 md:px-6 pt-3 sm:pt-4 pb-2 sm:pb-3">

            {/* Desktop: heading + lang switcher row */}
            <div className="hidden sm:flex items-center justify-between gap-3 mb-3">
              <div className="min-w-0">
                <h1
                  className="text-foreground shimmer-text"
                  style={{ fontSize: 'clamp(1rem, 3.5vw, 1.4rem)', fontWeight: 700, lineHeight: 1.2 }}
                >
                  {t.heading}
                </h1>
                <p className="text-sm text-muted-foreground mt-0.5">
                  {t.browse(subjects.length, allChapters.length || slimChapterCount)}
                </p>
              </div>
              <div className="flex items-center gap-1 shrink-0 rounded-xl p-0.5" style={{ background: 'rgba(139,92,246,0.08)', border: '1px solid rgba(139,92,246,0.12)' }}>
                <button
                  onClick={() => switchLang('en')}
                  className={`h-9 px-3 rounded-lg text-xs font-semibold transition-all ${contentLang === 'en' ? 'text-white bg-violet-600 shadow-sm' : 'text-violet-600 hover:bg-violet-50'}`}
                  aria-label="Switch to English"
                >English</button>
                <button
                  onClick={() => switchLang('as')}
                  className={`h-9 px-3 rounded-lg text-xs font-semibold transition-all ${contentLang === 'as' ? 'text-white bg-violet-600 shadow-sm' : 'text-violet-600 hover:bg-violet-50'}`}
                  aria-label="Switch to Assamese"
                >অসমীয়া</button>
              </div>
            </div>

            {/* Mobile: heading + lang switcher row — h1 is the LCP element on mobile.
                Previously hidden (hidden sm:flex desktop-only), which pushed LCP to a
                card <p> that required React + API data (~6s render delay). Making the
                heading visible on mobile lets the prerendered HTML paint it as the
                first large text block before JS even boots (~0ms render delay). */}
            <div className="flex sm:hidden items-center justify-between gap-2 mb-1">
              <div className="min-w-0">
                <h1
                  className="text-foreground font-bold leading-tight"
                  style={{ fontSize: 'clamp(1.1rem, 4.5vw, 1.25rem)' }}
                >
                  {t.heading}
                </h1>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {t.browse(subjects.length, allChapters.length || slimChapterCount)}
                </p>
              </div>
              <div className="flex items-center gap-0.5 shrink-0 rounded-lg p-0.5" style={{ background: 'rgba(139,92,246,0.08)', border: '1px solid rgba(139,92,246,0.12)' }}>
                <button
                  onClick={() => switchLang('en')}
                  className={`h-8 px-2 rounded-md text-[11px] font-semibold transition-all ${contentLang === 'en' ? 'text-white bg-violet-600 shadow-sm' : 'text-violet-600'}`}
                  aria-label="Switch to English"
                >EN</button>
                <button
                  onClick={() => switchLang('as')}
                  className={`h-8 px-2 rounded-md text-[11px] font-semibold transition-all ${contentLang === 'as' ? 'text-white bg-violet-600 shadow-sm' : 'text-violet-600'}`}
                  aria-label="Switch to Assamese"
                >অস</button>
              </div>
            </div>

            {/* Mobile: search bar (lang switcher moved to heading row above) */}
            <div className="flex sm:hidden items-center mb-2">
              <div className="relative flex-1 group/search">
                <Search
                  className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 pointer-events-none text-muted-foreground group-focus-within/search:text-primary transition-colors"
                  aria-hidden="true"
                />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={handleSearchChange}
                  aria-label="Search subjects"
                  placeholder={t.searchPlaceholder}
                  className="w-full h-10 pl-9 pr-8 rounded-xl text-sm text-foreground outline-none transition-all focus:ring-2 focus:ring-primary/20"
                  style={{ background: 'var(--card)', border: '1px solid rgba(139,92,246,0.15)', color: 'hsl(var(--foreground))' }}
                  data-testid="library-search-input"
                />
                {searchQuery && (
                  <button
                    onClick={handleSearchClear}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground/50 hover:text-foreground text-xs px-1 py-0.5 rounded transition-colors"
                    aria-label="Clear search"
                    data-testid="library-search-clear"
                  >×</button>
                )}
              </div>
            </div>

            {/* Desktop: search bar (standalone row) */}
            <div className="hidden sm:block relative group/search">
              <Search
                className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 pointer-events-none transition-colors text-muted-foreground group-focus-within/search:text-primary"
                aria-hidden="true"
              />
              <input
                type="text"
                value={searchQuery}
                onChange={handleSearchChange}
                aria-label="Search subjects"
                placeholder={t.searchPlaceholder}
                className="w-full h-11 pl-10 pr-4 rounded-xl text-sm text-foreground outline-none transition-all focus:ring-2 focus:ring-primary/20"
                style={{ background: 'var(--card)', border: '1px solid rgba(139,92,246,0.15)', color: 'hsl(var(--foreground))' }}
                data-testid="library-search-input"
              />
              {searchQuery && (
                <button
                  onClick={handleSearchClear}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground/50 hover:text-foreground text-xs px-1.5 py-0.5 rounded transition-colors"
                  aria-label="Clear search"
                  data-testid="library-search-clear"
                >{t.clear}</button>
              )}
            </div>

          </div>
        </div>

        <div className="flex-1 overflow-y-auto" ref={setScrollContainerEl}>
          <div className="w-full max-w-6xl mx-auto px-3 sm:px-4 md:px-6 py-3 sm:py-5">

            <ScrollableFilterRow
              role="group"
              aria-label="Stream filters"
              className="pb-4"
              data-testid="library-filter-chips"
            >
              {allStreamChips.map((chip) => (
                <FilterChip
                  key={chip.id}
                  chip={chip}
                  isActive={chip.id === activeFilter}
                  onClick={() => handleFilterChange(chip.id)}
                />
              ))}
            </ScrollableFilterRow>
            {filteredSubjects.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-center">
                <div
                  className="w-20 h-20 rounded-2xl flex items-center justify-center mb-5"
                  style={{
                    background: 'linear-gradient(135deg, rgba(124,58,237,0.08), rgba(139,92,246,0.04))',
                    border: '1px solid rgba(139,92,246,0.12)',
                  }}
                >
                  {activeFilter === 'saved'
                    ? <Bookmark className="w-9 h-9" style={{ color: 'hsl(var(--primary) / 0.4)' }} />
                    : <BookOpen className="w-9 h-9" style={{ color: 'hsl(var(--muted-foreground) / 0.3)' }} />
                  }
                </div>

                {activeFilter === 'saved' && authChecked && !user ? (
                  <>
                    <h3 className="text-foreground font-semibold text-lg">{t.savedSignIn}</h3>
                    <p className="text-sm text-muted-foreground/60 mt-1.5 max-w-xs">
                      {t.savedSignInDesc}
                    </p>
                    <Link
                      to="/signup"
                      className="mt-5 px-5 py-2 rounded-xl text-sm text-white font-medium transition-all duration-200 active:scale-95"
                      style={{ background: 'hsl(var(--primary))', boxShadow: '0 0 20px hsl(var(--primary)/0.3)' }}
                    >
                      {t.signUpFree}
                    </Link>
                  </>
                ) : activeFilter === 'saved' && user ? (
                  <>
                    <h3 className="text-foreground font-semibold text-lg">{t.noSaved}</h3>
                    <p className="text-sm text-muted-foreground/60 mt-1.5 max-w-xs">
                      {t.noSavedDesc}
                    </p>
                    <button
                      onClick={() => setActiveFilter('all')}
                      className="mt-5 px-5 py-2 rounded-xl text-sm text-white font-medium transition-all duration-200 active:scale-95"
                      style={{ background: 'hsl(var(--primary))', boxShadow: '0 0 20px hsl(var(--primary)/0.3)' }}
                    >
                      {t.browseAll}
                    </button>
                  </>
                ) : (
                  <>
                    <h3 className="text-foreground font-semibold text-lg">{t.noResultsTitle}</h3>
                    <p className="text-sm text-muted-foreground/60 mt-1.5 max-w-xs">
                      {searchQuery
                        ? (contentLang === 'as'
                          ? `"${searchQuery}" ৰ বাবে কোনো ফলাফল নাই — অন্য শব্দ চেষ্টা কৰক বা সন্ধান মচক`
                          : `No results for "${searchQuery}" — try a different term or clear the search`)
                        : (contentLang === 'as'
                          ? 'আৰু বিষয় বিচাৰিবলৈ আপোনাৰ ফিল্টাৰ সালসলনি কৰক'
                          : 'Try adjusting your filters to discover more subjects')}
                    </p>
                    {(searchQuery || activeFilter !== 'all') && (
                      <button
                        onClick={handleResetFilters}
                        className="mt-5 px-4 py-2 rounded-xl text-sm text-primary hover:text-white transition-all duration-200 active:scale-95"
                        style={{ border: '1px solid rgba(139,92,246,0.25)', background: 'rgba(139,92,246,0.06)' }}
                        data-testid="library-reset-filters-button"
                      >
                        {contentLang === 'as' ? 'সকলো ফিল্টাৰ মচক' : 'Reset all filters'}
                      </button>
                    )}
                  </>
                )}
              </div>
            ) : (
              useVirtualGrid ? (
                <VirtualSubjectGrid
                  scrollParent={scrollContainerEl}
                  subjects={rankedSubjects}
                  chaptersBySubject={chaptersBySubject}
                  savedSubjects={savedSubjects}
                  onToggleSave={handleToggleSave}
                  onAskAI={handleAskAI}
                />
              ) : (
              <>
              <div
                className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-5"
                data-testid="library-subject-grid"
                style={{ contain: 'layout style', minHeight: '420px' }}
              >
                {visibleSubjects.flatMap((sub, index) => {
                  const card = (
                    <SubjectCard
                      key={sub.id}
                      sub={sub}
                      chapters={chaptersBySubject.get(sub.id) || []}
                      isSaved={savedSubjects.includes(sub.id)}
                      onToggleSave={handleToggleSave}
                      onAskAI={handleAskAI}
                      index={index}
                    />
                  );
                  return [card];
                })}
              </div>
              {rankedSubjects.length > visibleSubjects.length && (
                <div ref={sentinelRef} aria-hidden="true" style={{ height: 1 }} />
              )}
              </>
              )
            )}
          </div>
          <QuestionPapersSection />
          <LazyOnVisible>
            <CmsDocsSection />
          </LazyOnVisible>
          <LazyOnVisible>
            <CmsPostsGrid />
          </LazyOnVisible>
        </div>

      </div>
    </AppLayout>
  );
}
