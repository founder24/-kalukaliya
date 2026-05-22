import { useState, useMemo, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import PageMeta from '@/components/seo/PageMeta';
import {
  BookOpen, ChevronRight, Home, Sparkles,
  Layers, ArrowLeft, Search,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useResolveSubject, useChapters } from '@/hooks/useContent';
import ContinueLearning from '@/components/content/ContinueLearning';
import TrustpilotReviewsSection from '@/components/content/TrustpilotReviewsSection';
import SubjectTopicIndex from '@/components/subject/SubjectTopicIndex';
import { apiClient } from '@/utils/api';
import { MobileNavSwitch } from '@/components/layout/MobileNavSwitch';
import { useContentLang } from '@/context/LanguageContext';
import { seoRelatedByChapter } from '@/utils/api';
import { siblingsAsRelated } from '@/utils/siblingChapter';

export default function SubjectLandingPage() {
  const { board, classSlug, subjectSlug } = useParams();
  const [searchQuery, setSearchQuery] = useState('');
  const { contentLang } = useContentLang();

  const { data: subject = null, isLoading: subjectLoading, error: subjectError } = useResolveSubject(board, classSlug, subjectSlug);
  const subjectId = subject?.id || subject?._id;
  const { data: chapters = [], isLoading: chaptersLoading } = useChapters(subjectId);
  const loading = subjectLoading || (!!subjectId && chaptersLoading);
  const error = subjectError
    ? (subjectError.response?.status === 404 ? 'Subject not found' : 'Failed to load subject')
    : null;

  const filteredChapters = useMemo(() => {
    if (!searchQuery.trim()) return chapters;
    const q = searchQuery.toLowerCase();
    return chapters.filter((ch) =>
      ch.title?.toLowerCase().includes(q) ||
      ch.description?.toLowerCase().includes(q)
    );
  }, [chapters, searchQuery]);

  const basePath = `/${board}/${classSlug}/${subjectSlug}`;

  // Pull SEO related-topics for the first chapter to seed the
  // ContinueLearning rail, then backfill with sibling chapters until ≥4 links.
  const [seoRelated, setSeoRelated] = useState([]);
  // Topical-mapping (Task: topical mapping + topical authority) —
  // pillar-page topic index for the subject. Seeded from the
  // prerendered preload (`window.__SUBJECT_PRELOAD__.topic_index`)
  // when present so SSR / curl-no-JS already ships the index;
  // otherwise we lazy-fetch on the client once `subjectId` resolves.
  const [topicIndex, setTopicIndex] = useState(() => {
    // SSR path — entry-server.jsx mirrors `seed.subjectPreload` onto
    // `globalThis.__SSR_SUBJECT_PRELOAD__` for the request lifetime so
    // the prerendered HTML carries the topic index in the linear DOM
    // (no client-only fallback). On client hydration the bootstrap
    // script puts the same payload on `window.__SUBJECT_PRELOAD__`.
    if (typeof window === 'undefined') {
      const ssr = globalThis.__SSR_SUBJECT_PRELOAD__?.topic_index;
      if (ssr && Array.isArray(ssr.chapters)) return ssr;
      return { chapters: [], total_topics: 0 };
    }
    const seed = window.__SUBJECT_PRELOAD__?.topic_index;
    if (seed && Array.isArray(seed.chapters)) return seed;
    return { chapters: [], total_topics: 0 };
  });
  useEffect(() => {
    let cancelled = false;
    if (!subjectId) return;
    // Skip refetch when the preload's index belongs to this same
    // subject (avoids a redundant network round-trip on the SSR
    // hydration pass).
    if (
      typeof window !== 'undefined'
      && window.__SUBJECT_PRELOAD__?.subject_id === subjectId
      && Array.isArray(window.__SUBJECT_PRELOAD__?.topic_index?.chapters)
      && (window.__SUBJECT_PRELOAD__.topic_index.chapters.length > 0)
    ) {
      return;
    }
    apiClient()
      .get(`/content/subjects/${subjectId}/topic-index`)
      .then((r) => {
        if (cancelled) return;
        const payload = r.data || {};
        setTopicIndex({
          chapters: Array.isArray(payload.chapters) ? payload.chapters : [],
          total_topics: Number(payload.total_topics || 0),
        });
      })
      .catch(() => {
        if (!cancelled) setTopicIndex({ chapters: [], total_topics: 0 });
      });
    return () => { cancelled = true; };
  }, [subjectId]);
  const seedChapterId = chapters[0]?.id || chapters[0]?._id || null;
  useEffect(() => {
    let cancelled = false;
    if (!seedChapterId) { setSeoRelated([]); return; }
    seoRelatedByChapter(seedChapterId, null, 6)
      .then((rows) => {
        if (cancelled) return;
        const payload = rows?.data ?? rows;
        const list = Array.isArray(payload) ? payload : (payload?.related || payload?.items || []);
        setSeoRelated(list.map((r) => ({
          id: r.id || r.slug,
          title: r.title,
          seo_path: r.seo_path || (r.slug ? `/learn/${r.slug}` : '#'),
        })));
      })
      .catch(() => { if (!cancelled) setSeoRelated([]); });
    return () => { cancelled = true; };
  }, [seedChapterId]);

  const continueRelated = useMemo(() => {
    const out = [...(seoRelated || [])];
    if (out.length < 4) {
      const seen = new Set(out.map((r) => r.seo_path));
      const sibs = siblingsAsRelated(chapters, seedChapterId, null, basePath, 8);
      for (const s of sibs) {
        if (out.length >= 6) break;
        if (!seen.has(s.seo_path)) out.push(s);
      }
    }
    return out.slice(0, 6);
  }, [seoRelated, chapters, seedChapterId, basePath]);

  const subjectName = subject?.name || subjectSlug;
  const boardName = subject?.board_name || board;
  const className = subject?.class_name || classSlug;
  const streamName = subject?.stream_name || '';
  const chapterCount = chapters?.length || 0;

  const contentTypes = useMemo(
    () => new Set(chapters.map((ch) => (ch.content_type || '').toLowerCase()).filter(Boolean)),
    [chapters],
  );

  const faqJsonLd = useMemo(() => {
    if (!subjectName) return null;
    const qa = [];

    qa.push({
      q: `What topics are covered in ${boardName} ${className} ${subjectName}?`,
      a: chapterCount > 0
        ? `Syrabit.ai covers ${chapterCount} chapters for ${boardName} ${className} ${subjectName}, including detailed notes, key concepts, and exam-focused summaries for each chapter.`
        : `Syrabit.ai provides comprehensive study material for ${boardName} ${className} ${subjectName}, covering the full syllabus with notes and AI-powered tutoring.`,
    });

    if (contentTypes.has('pyq') || contentTypes.has('important_questions')) {
      qa.push({
        q: `Where can I find ${boardName} ${subjectName} previous year questions?`,
        a: `You can find previous year questions (PYQs) for ${boardName} ${className} ${subjectName} on Syrabit.ai. Each chapter includes mark-wise important questions from past exams to help you prepare effectively.`,
      });
    }

    qa.push({
      q: `Is ${subjectName} study material on Syrabit.ai free?`,
      a: `Yes, Syrabit.ai offers 30 free AI-powered study credits per day. You can browse ${subjectName} notes, ask questions to the AI tutor, and access chapter summaries without creating an account.`,
    });

    const features = ['AI-powered explanations', 'chapter-wise notes'];
    if (contentTypes.has('mcq')) features.push('MCQs');
    if (contentTypes.has('pyq')) features.push('previous year questions');
    qa.push({
      q: `How does Syrabit.ai help with ${subjectName} exam preparation?`,
      a: `Syrabit.ai provides ${features.join(', ')} for ${boardName} ${className} ${subjectName}. The AI tutor can answer specific questions with source citations from your syllabus.`,
    });

    qa.push({
      q: `Can I study ${subjectName} in Assamese on Syrabit.ai?`,
      a: `Yes, Syrabit.ai supports bilingual study in both English and Assamese. You can switch languages anytime while studying ${subjectName} to better understand concepts in your preferred language.`,
    });

    return {
      '@context': 'https://schema.org',
      '@type': 'FAQPage',
      mainEntity: qa.map(({ q, a }) => ({
        '@type': 'Question',
        name: q,
        acceptedAnswer: { '@type': 'Answer', text: a },
      })),
    };
  }, [subjectName, boardName, className, chapterCount, contentTypes]);

  if (loading) {
    return (
      <div className="min-h-screen bg-background text-foreground">
        <div className="max-w-4xl mx-auto px-4 py-8">
          <Skeleton className="h-4 w-48 mb-6" />
          <Skeleton className="h-10 w-full mb-4" />
          <Skeleton className="h-4 w-64 mb-8" />
          {[...Array(5)].map((_, i) => (
            <Skeleton key={i} className="h-20 w-full mb-3 rounded-xl" />
          ))}
        </div>
      </div>
    );
  }

  if (error || !subject) {
    return (
      <div className="min-h-screen bg-background text-foreground flex items-center justify-center">
        <div className="text-center max-w-md px-6">
          <div className="w-16 h-16 rounded-2xl bg-muted flex items-center justify-center mx-auto mb-5">
            <BookOpen size={28} className="text-muted-foreground" />
          </div>
          <h1 className="text-2xl font-bold mb-3">{error || 'Subject not found'}</h1>
          <p className="text-muted-foreground mb-6">We couldn't find this subject. It may not be available yet.</p>
          <Link to="/library" className="inline-flex items-center gap-2 px-6 py-3 bg-violet-600 hover:bg-violet-500 rounded-xl text-white font-medium transition-colors">
            <ArrowLeft size={16} />
            Back to Browser
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <PageMeta
        title={`${subjectName} — ${boardName} ${className} Notes & Study Material`}
        description={subject.description || `Complete ${subjectName} study material for ${boardName} ${className} students. Notes, MCQs, important questions, and AI-powered tutoring.`}
        url={`https://syrabit.ai${basePath}`}
        pageType="subject"
        pageData={{
          subject: {
            ...subject,
            name: subjectName,
            slug: subjectSlug,
            board_slug: board,
            class_slug: classSlug,
            board_name: boardName,
            class_name: className,
            stream_name: streamName,
            chapters,
          },
        }}
        jsonLd={faqJsonLd ? [faqJsonLd] : undefined}
      />

      <header className="border-b border-border/30" style={{ background: 'rgba(255,255,255,0.80)', backdropFilter: 'blur(12px)' }}>
        <div className="max-w-4xl mx-auto px-4 py-5">
          <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm text-muted-foreground mb-4 flex-wrap">
            <Link to="/" className="hover:text-violet-600 transition-colors flex items-center gap-1">
              <Home size={13} /> Home
            </Link>
            <ChevronRight size={11} className="text-muted-foreground/40" />
            <Link to="/library" className="hover:text-violet-600 transition-colors">Browser</Link>
            <ChevronRight size={11} className="text-muted-foreground/40" />
            <span className="text-foreground font-medium">{subjectName}</span>
          </nav>

          <div className="flex items-start gap-3 sm:gap-4">
            <div className="w-12 h-12 sm:w-14 sm:h-14 rounded-2xl flex items-center justify-center text-xl sm:text-2xl shrink-0" style={{ background: 'linear-gradient(135deg, rgba(124,58,237,0.10), rgba(139,92,246,0.05))', border: '1px solid rgba(139,92,246,0.15)' }}>
              {subject.icon || '📚'}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5 mb-2 flex-wrap">
                <Badge variant="outline" className="text-[11px] text-purple-600 border-purple-500/25 bg-purple-500/5">{boardName}</Badge>
                <Badge variant="outline" className="text-[11px] text-blue-600 border-blue-500/25 bg-blue-500/5">{className}</Badge>
                {streamName && <Badge variant="outline" className="text-[11px] text-emerald-600 border-emerald-500/25 bg-emerald-500/5">{streamName}</Badge>}
              </div>
              <h1 className="text-xl sm:text-2xl md:text-3xl font-bold text-foreground leading-tight">
                {subjectName}
              </h1>
              {subject.description && (
                <p className="text-muted-foreground mt-1.5 text-sm leading-relaxed max-w-2xl line-clamp-2 sm:line-clamp-none">
                  {subject.description}
                </p>
              )}
              <div className="flex items-center gap-3 mt-2.5 text-xs sm:text-sm text-muted-foreground">
                <span className="flex items-center gap-1">
                  <Layers size={12} />
                  {chapters.length} chapters
                </span>
              </div>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-4xl mx-auto px-4 py-6">
        {chapters.length > 4 && (
          <div className="relative mb-6">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search chapters..."
              className="w-full h-10 pl-10 pr-4 rounded-xl text-sm bg-muted/30 border border-border text-foreground placeholder:text-muted-foreground outline-none focus:border-violet-500/30 transition-colors"
            />
          </div>
        )}

        <Link
          to={`/chat?subject=${subject.id || subject._id || ''}`}
          className="flex items-center gap-3 mb-6 px-4 sm:px-5 py-3.5 rounded-2xl transition-all hover:border-violet-500/30"
          style={{
            background: 'linear-gradient(135deg, rgba(124,58,237,0.06), rgba(139,92,246,0.03))',
            border: '1px solid rgba(139,92,246,0.12)',
          }}
        >
          <Sparkles size={16} className="text-violet-600 shrink-0" />
          <div className="flex-1 min-w-0">
            <span className="text-sm font-medium text-foreground">Ask AI about {subjectName}</span>
            <span className="hidden sm:inline text-xs text-muted-foreground ml-2">Get instant answers aligned with your syllabus</span>
          </div>
          <ChevronRight size={16} className="text-muted-foreground shrink-0" />
        </Link>

        <div className="space-y-3">
          {filteredChapters.length === 0 ? (
            <div className="text-center py-12">
              <BookOpen size={32} className="mx-auto mb-3 text-muted-foreground/40" />
              <p className="text-muted-foreground">{searchQuery ? 'No chapters match your search' : 'No chapters available yet'}</p>
            </div>
          ) : (
            filteredChapters.flatMap((ch, i) => {
              const chPath = ch.slug
                ? `${basePath}/${ch.slug}`
                : `${basePath}`;

              const card = (
                <div
                  key={ch.id || i}
                  className="rounded-2xl overflow-hidden transition-all hover:border-violet-500/15 glass-card"
                >
                  <Link
                    to={chPath}
                    className="flex items-center gap-3 px-5 py-4 group/ch hover:bg-violet-500/[0.03] transition-colors"
                  >
                    <span
                      className="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold shrink-0"
                      style={{ background: 'rgba(139,92,246,0.08)', color: 'rgb(124,58,237)' }}
                    >
                      {i + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <h2 className="text-sm font-semibold text-foreground group-hover/ch:text-violet-600 transition-colors">
                        {ch.title}
                      </h2>
                      {ch.description && (
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{ch.description}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {ch.content_type && (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground">{ch.content_type}</span>
                      )}
                      <ChevronRight size={16} className="text-muted-foreground/40 group-hover/ch:text-violet-600 transition-colors" />
                    </div>
                  </Link>
                  {ch.id && (
                    <div className="border-t border-border/30 px-5 py-2">
                      <Link
                        to={`/chat?subject=${subject.id || subject._id || ''}&chapter=${ch.id}`}
                        className="flex items-center gap-1.5 text-xs text-violet-600 hover:text-violet-700 transition-colors"
                      >
                        <Sparkles size={11} /> Ask AI about this chapter
                      </Link>
                    </div>
                  )}
                </div>
              );

              return [card];
            })
          )}
        </div>

        {/* Topical-mapping pillar — surface every citable topic across
            every chapter as a real <a href> graph. Same DOM in SSR /
            prerender / SPA so bots see the full internal-linking
            structure on first byte (no JS required). Renders nothing
            when the index is empty. */}
        <SubjectTopicIndex index={topicIndex} />

        {chapters.length > 0 && (
          <ContinueLearning
            related={continueRelated}
            subjectName={subjectName}
            subjectPath={basePath}
            chatHref={`/chat?subject=${subject.id || subject._id || ''}`}
            contentLang={contentLang}
          />
        )}

        {subject.tags?.length > 0 && (
          <div className="mt-8 p-5 rounded-2xl glass-card">
            <h2 className="text-sm font-semibold text-muted-foreground mb-3">Related Topics</h2>
            <div className="flex flex-wrap gap-2">
              {subject.tags.map((tag) => (
                <span key={tag} className="text-xs px-3 py-1.5 rounded-full bg-violet-500/5 text-violet-600 border border-violet-500/15">
                  {tag}
                </span>
              ))}
            </div>
          </div>
        )}

        <TrustpilotReviewsSection
          subheading={`Finding Syrabit.ai useful for ${boardName} ${className} ${subjectName}? Share your experience and help other students.`}
          subjectName={subjectName}
          boardName={boardName}
          className={className}
        />

        <nav className="mt-10 pt-6 border-t border-border/30" aria-label="Site navigation">
          <div className="flex flex-wrap gap-4 justify-center text-xs text-muted-foreground">
            <Link to="/chat" className="hover:text-violet-600 transition-colors">Ask Syra</Link>
          </div>
          <p className="text-center text-xs text-muted-foreground mt-3">
            Syrabit.ai — AI-powered exam prep for AssamBoard students (AHSEC · DEGREE · SEBA)
          </p>
        </nav>
        <div
          className="md:hidden"
          aria-hidden="true"
          style={{ height: 'calc(4rem + env(safe-area-inset-bottom, 0px))' }}
        />
      </div>
      <MobileNavSwitch />
    </div>
  );
}
