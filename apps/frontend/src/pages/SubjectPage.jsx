import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import PageMeta from '@/components/seo/PageMeta';
import {
  BookOpen, Loader2, ArrowLeft, FileText, Calculator,
  BookMarked, HelpCircle, List, ChevronRight, BookText,
  Layers, Hash, Share2, Clock, RefreshCw, ChevronDown,
  Sparkles, CheckCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import StickyToc from '@/components/ui/StickyToc';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';
import DOMPurify from 'dompurify';
import { Skeleton } from '@/components/ui/skeleton';
import { AppLayout } from '@/components/layout/AppLayout';
import { getChunks, getChapterTopicSummary, apiClient } from '@/utils/api';
import { useShare } from '@/hooks/useShare';
import { useSubject, useChapters } from '@/hooks/useContent';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { toast } from 'sonner';


const CONTENT_TYPE_ICONS = {
  notes:   FileText,
  example: BookMarked,
  formula: Calculator,
  pyq:     HelpCircle,
  summary: List,
};

// Article JSON-LD for the merged blog-view of a subject. Course / FAQPage /
// CollectionPage / BreadcrumbList are emitted by `subjectHubSchema` via
// `<PageMeta pageType="subject">` so this only contributes the Article node
// to avoid duplicating types in the page's structured-data graph.
function ArticleJsonLd({ subject, title, url, wordCount }) {
  useEffect(() => {
    const eduLevel = ((subject?.class_name || '') + ' ' + (subject?.board_name || '') + ' ' + (subject?.stream_name || '')).replace(/\s+/g, ' ').trim() || 'FYUGP';
    const description = subject?.description || `Complete ${subject?.name || title} notes and study material for ${eduLevel} students.`;
    const datePublished = subject?.published_at || subject?.created_at || new Date().toISOString();
    const dateModified = subject?.updated_at || subject?.modified_at || datePublished;
    const author = subject?.author_name
      ? { '@type': 'Person', name: subject.author_name, affiliation: { '@type': 'Organization', name: 'Syrabit.ai', url: 'https://syrabit.ai' } }
      : { '@type': 'Organization', name: 'Syrabit.ai Editorial Team', url: 'https://syrabit.ai' };

    const graphNodes = [
      {
        '@type': 'Article',
        headline: title,
        name: title,
        description,
        url,
        author,
        publisher: {
          '@type': 'Organization', name: 'Syrabit.ai', url: 'https://syrabit.ai',
          logo: { '@type': 'ImageObject', url: 'https://syrabit.ai/icons/icon-192x192.png' },
        },
        datePublished,
        dateModified,
        educationalLevel: eduLevel,
        about: { '@type': 'Thing', name: subject?.name || title },
        wordCount,
        inLanguage: 'en-IN',
        mainEntityOfPage: { '@type': 'WebPage', '@id': url },
        isPartOf: { '@type': 'WebSite', '@id': 'https://syrabit.ai', name: 'Syrabit.ai' },
        image: 'https://syrabit.ai/opengraph.jpg',
      },
    ];

    const script = document.createElement('script');
    script.type = 'application/ld+json';
    script.id = 'subject-article-jsonld';
    script.text = JSON.stringify({ '@context': 'https://schema.org', '@graph': graphNodes });
    document.getElementById('subject-article-jsonld')?.remove();
    document.head.appendChild(script);
    return () => document.getElementById('subject-article-jsonld')?.remove();
  }, [title, url, wordCount, subject]);
  return null;
}

function useCmsPost(subjectId, enabled) {
  const [post,    setPost]    = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);
  useEffect(() => {
    if (!subjectId || !enabled) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiClient().get(`/cms/post/${subjectId}`)
      .then(r => { if (!cancelled) setPost(r.data); })
      .catch(e => { if (!cancelled) setError(e); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [subjectId, enabled]);
  return { post, loading, error };
}

function BlogView({ subject, subjectId }) {
  const { post, loading, error } = useCmsPost(subjectId, true);
  const articleRef = useRef(null);
  const [activeId,  setActiveId]  = useState('');
  const [merging,   setMerging]   = useState(false);
  const { sharing, share: handleShare } = useShare();

  const headings = useMemo(() => {
    if (!post?.headings) return [];
    try { return JSON.parse(post.headings); } catch { return []; }
  }, [post]);

  const _siteOrigin = 'https://syrabit.ai';
  const subjectUrl = subject?.board_slug && subject?.class_slug && subject?.stream_slug && subject?.slug
    ? `${_siteOrigin}/${subject.board_slug}/${subject.class_slug}/${subject.stream_slug}/${subject.slug}`
    : `${_siteOrigin}/subject/${subjectId}`;

  const readMins = post?.word_count ? Math.max(1, Math.ceil(post.word_count / 200)) : null;

  useEffect(() => {
    if (!articleRef.current || headings.length === 0) return;
    const observer = new IntersectionObserver(
      entries => {
        for (const entry of entries) {
          if (entry.isIntersecting) { setActiveId(entry.target.id); break; }
        }
      },
      { rootMargin: '-80px 0px -70% 0px', threshold: 0 }
    );
    headings.forEach(h => {
      const el = document.getElementById(h.anchor);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [headings]);

  const handleMerge = async () => {
    if (!subjectId) return;
    setMerging(true);
    try {
      await apiClient().post(`/admin/cms/merge/${subjectId}`);
      toast.success('Merged & published — reload to see Blog View');
    } catch { toast.error('Merge failed (admin access needed)'); }
    finally { setMerging(false); }
  };

  if (loading) return (
    <div className="space-y-5 max-w-3xl mx-auto px-4">
      {[...Array(8)].map((_, i) => <Skeleton key={i} className="h-5 w-full" style={{ width: `${60 + (i % 3) * 15}%` }} />)}
    </div>
  );

  if (error || !post) return (
    <div className="flex flex-col items-center py-16 text-center gap-4 max-w-md mx-auto px-4">
      <BookText size={36} className="opacity-20" />
      <p className="text-sm" style={{ color: 'rgba(232,232,232,0.50)' }}>
        Blog view not yet generated for this subject.
      </p>
      <button
        onClick={handleMerge}
        disabled={merging}
        className="h-9 px-4 rounded-xl text-sm font-medium text-white flex items-center gap-2 disabled:opacity-50"
        style={{ background: 'rgba(149,117,224,0.20)', border: '1px solid rgba(149,117,224,0.30)' }}
      >
        {merging ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
        Generate Blog View (admin)
      </button>
    </div>
  );

  const htmlContent = post.subject_merged_html || '';

  return (
    <div className="blog-view-tab w-full" style={{ background: '#f0f0f1', padding: '1.5rem 0 4rem' }}>
      {subject && (
        <ArticleJsonLd subject={subject} title={post.title || subject.name} url={subjectUrl} wordCount={post.word_count} />
      )}
      <div className="flex gap-8 max-w-4xl mx-auto px-4 sm:px-6">
        <article ref={articleRef} className="flex-1 min-w-0 pb-16 min-w-0">

          {/* Hero info bar — gray header strip */}
          <div className="blog-hero-bar flex flex-wrap items-center gap-3 text-[11px]"
            style={{ background: '#f8f9fa', borderBottom: '1px solid #e2e2e2', color: '#777', padding: '0.6rem 2rem', marginBottom: 0, borderRadius: '4px 4px 0 0' }}>
            {readMins && (
              <span className="flex items-center gap-1"><Clock size={11} />{readMins} min read</span>
            )}
            {post.word_count > 0 && (
              <span>{post.word_count.toLocaleString()} words</span>
            )}
            {headings.length > 0 && (
              <span className="flex items-center gap-1"><Hash size={11} />{headings.filter(h => h.level === 2).length} sections</span>
            )}
            <button
              className="ml-auto flex items-center gap-1 transition-colors text-emerald-600 hover:text-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed"
              disabled={sharing}
              onClick={() => handleShare(post.title, `/subject/${subjectId}`)}
            >
              {sharing ? <Loader2 size={11} className="animate-spin" /> : <Share2 size={11} />} Share
            </button>
          </div>

          {/* White content card */}
          {htmlContent ? (
            <div
              className="learn-article"
              style={{ background: '#ffffff', color: '#1a1a1a', fontSize: '15px', lineHeight: '1.7', padding: 'clamp(1rem, 4vw, 2.5rem) clamp(0.75rem, 4vw, 2.5rem) 2.5rem', boxShadow: '0 1px 12px rgba(0,0,0,0.09)', borderRadius: '0 0 4px 4px', maxWidth: 'none' }}
              dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(htmlContent) }}
            />
          ) : (
            <div className="learn-article"
              style={{ background: '#ffffff', color: '#1a1a1a', fontSize: '15px', lineHeight: '1.7', padding: 'clamp(1rem, 4vw, 2.5rem) clamp(0.75rem, 4vw, 2.5rem) 2.5rem', boxShadow: '0 1px 12px rgba(0,0,0,0.09)', borderRadius: '0 0 4px 4px', maxWidth: 'none' }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
                {post.merged_md || ''}
              </ReactMarkdown>
            </div>
          )}

          {/* AI Tutor CTA */}
          <div className="blog-ai-cta" style={{ background: '#f5f0ff', border: '1px solid #c4b0f0', marginTop: '2.5rem', padding: '1.25rem 1.5rem', borderRadius: '12px' }}>
            <p className="text-sm mb-1" style={{ color: '#5b35a8', fontWeight: 600 }}>Have a question about this topic?</p>
            <p className="text-xs mb-3" style={{ color: '#7c5cbf' }}>Get Assam Board-aligned answers instantly from Syra.</p>
            <Link to={`/chat?subject=${subjectId}`}>
              <Button size="sm" style={{ background: 'hsl(258 60% 68%)', color: 'white' }}>Ask Syra</Button>
            </Link>
          </div>
        </article>

        <StickyToc headings={headings} activeId={activeId} />
      </div>
    </div>
  );
}

function LegacyAccordion({ subject, subjectId, chapters }) {
  const [topicSummaries, setTopicSummaries] = useState({});
  const [loadingTopics, setLoadingTopics] = useState({});
  const [chunks, setChunks] = useState({});
  const [loadingChapter, setLoadingChapter] = useState(null);

  const loadChapterData = useCallback(async (chapterId) => {
    if (topicSummaries[chapterId] || loadingTopics[chapterId]) return;
    setLoadingTopics(prev => ({ ...prev, [chapterId]: true }));
    try {
      const [topicRes, chunkRes] = await Promise.allSettled([
        getChapterTopicSummary(chapterId),
        getChunks(chapterId),
      ]);
      if (topicRes.status === 'fulfilled') {
        setTopicSummaries(prev => ({ ...prev, [chapterId]: topicRes.value.data }));
      }
      if (chunkRes.status === 'fulfilled') {
        setChunks(prev => ({ ...prev, [chapterId]: chunkRes.value.data }));
      }
    } finally {
      setLoadingTopics(prev => ({ ...prev, [chapterId]: false }));
    }
  }, [topicSummaries, loadingTopics]);

  if (chapters.length === 0) return (
    <div className="text-center py-8" style={{ color: '#999' }}>
      <BookOpen size={32} className="mx-auto mb-2 opacity-30" />
      <p>No chapters available yet</p>
    </div>
  );

  return (
    <Accordion type="multiple" className="space-y-2 max-w-4xl mx-auto">
      {chapters.map(chapter => {
        const chapterSeoPath = subject?.board_slug && subject?.class_slug && subject?.slug && chapter.slug
          ? `/${subject.board_slug}/${subject.class_slug}/${subject.slug}/${chapter.slug}`
          : null;
        return (
          <AccordionItem key={chapter.id} value={chapter.id} className="chapter-textbook rounded-xl border-0 px-4">
            <AccordionTrigger className="hover:no-underline py-4" onClick={() => loadChapterData(chapter.id)}>
              <div className="flex items-center gap-3">
                <span className="w-7 h-7 rounded-lg flex items-center justify-center text-xs font-semibold flex-shrink-0" style={{ background: 'rgba(124,58,237,0.08)', color: '#7c3aed' }}>
                  {chapter.chapter_number}
                </span>
                <div className="text-left min-w-0">
                  <span className="text-sm font-medium block" style={{ color: '#1a1a1a', fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" }}>{chapter.title}</span>
                  {chapter.topics?.length > 0 && (
                    <span className="text-[11px]" style={{ color: '#888' }}>{chapter.topics.length} topics</span>
                  )}
                </div>
              </div>
            </AccordionTrigger>
            <AccordionContent className="pb-4">
              <article
                itemScope
                itemType="https://schema.org/LearningResource"
                aria-label={`${chapter.title} — ${subject.name} study material`}
              >
                <meta itemProp="name" content={chapter.title} />
                <meta itemProp="educationalLevel" content={subject.class_name || ''} />
                <meta itemProp="learningResourceType" content="Study Notes" />
                <meta itemProp="inLanguage" content="en-IN" />
                {chapter.description && <meta itemProp="description" content={chapter.description} />}

                {chapter.description && (
                  <p className="text-xs mb-3 px-1 leading-relaxed" style={{ color: '#666' }}>{chapter.description}</p>
                )}

                {chapter.topics?.length > 0 && (
                  <div className="mb-3">
                    <div className="flex items-center gap-2 mb-2 px-1">
                      <List size={12} className="text-primary" />
                      <span className="text-xs font-semibold" style={{ color: '#1a1a1a' }}>
                        Syllabus Topics ({chapter.topics.length})
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-1.5 px-1">
                      {chapter.topics.map((t, i) => (
                        <span
                          key={i}
                          className="text-[11px] px-2.5 py-1 rounded-lg"
                          style={{ background: '#f3f4f6', color: '#555', border: '1px solid #e5e7eb' }}
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {chapterSeoPath && (
                  <Link
                    to={chapterSeoPath}
                    className="block mb-3 px-4 py-3 rounded-xl transition-all group/ch"
                    style={{ background: 'rgba(124,58,237,0.04)', border: '1px solid rgba(124,58,237,0.12)' }}
                    title={`${chapter.title} — ${subject.name} Notes & Study Material`}
                    itemProp="url"
                  >
                    <div className="flex items-center gap-2">
                      <FileText size={14} className="text-primary flex-shrink-0" />
                      <span className="text-sm font-medium group-hover/ch:text-primary transition-colors" style={{ color: '#1a1a1a' }}>
                        {chapter.title} — Full Notes
                      </span>
                      <ChevronRight size={14} className="ml-auto group-hover/ch:text-primary flex-shrink-0 transition-colors" style={{ color: '#aaa' }} />
                    </div>
                  </Link>
                )}

                {chapter.content && (
                  <details className="group/details" open>
                    <summary className="cursor-pointer flex items-center gap-2 text-xs py-2 px-1" style={{ color: '#888' }}>
                      <BookText size={12} />
                      <span>Chapter Notes</span>
                      <ChevronDown size={12} className="ml-auto group-open/details:rotate-180 transition-transform" />
                    </summary>
                    <div className="px-1 py-2">
                      <div className="chapter-textbook text-sm" style={{ boxShadow: 'none' }} itemProp="text">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{chapter.content}</ReactMarkdown>
                      </div>
                    </div>
                  </details>
                )}

                {!chapter.content && !chapter.topics?.length && (
                  <div className="text-center py-4">
                    <p className="text-sm" style={{ color: '#999' }}>No content added yet</p>
                  </div>
                )}

                <div className="mt-3 flex items-center gap-2">
                  <Link to={`/chat?subject=${subjectId}&chapter=${chapter.id}`}>
                    <Button size="sm" className="text-xs bg-primary hover:bg-primary/90 text-primary-foreground">
                      <Sparkles size={12} className="mr-1" /> Ask AI about this chapter
                    </Button>
                  </Link>
                </div>
              </article>
            </AccordionContent>
          </AccordionItem>
        );
      })}
    </Accordion>
  );
}

export default function SubjectPage() {
  const { subjectId }          = useParams();

  const { data: subject, isLoading: subjectLoading, isError: subjectError, refetch: refetchSubject } = useSubject(subjectId);
  const { data: chapters = [], isLoading: chaptersLoading } = useChapters(subjectId);
  const loading = subjectLoading || chaptersLoading;


  if (subjectError && !subjectLoading) return (
    <AppLayout>
      <div className="p-6 text-center space-y-4">
        <div className="mx-auto w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center">
          <BookOpen className="w-6 h-6 text-red-500" />
        </div>
        <p className="text-muted-foreground">Failed to load subject. Please check your connection and try again.</p>
        <Button onClick={() => refetchSubject()} variant="outline">
          <RefreshCw className="w-4 h-4 mr-2" /> Retry
        </Button>
        <div>
          <Link to="/library"><Button variant="ghost" className="mt-2">Back to Browser</Button></Link>
        </div>
      </div>
    </AppLayout>
  );

  if (loading) return (
    <AppLayout>
      <div className="p-4 sm:p-6 space-y-5 animate-pulse">
        <div className="flex items-center gap-3">
          <div className="h-5 w-5 rounded bg-violet-500/10" />
          <div className="h-4 w-24 rounded bg-violet-500/10" />
        </div>
        <div className="space-y-2">
          <div className="h-8 w-2/3 rounded-lg bg-violet-500/10" />
          <div className="h-4 w-1/2 rounded bg-violet-500/8" />
        </div>
        <div className="flex gap-2">
          <div className="h-10 w-28 rounded-xl bg-violet-500/10" />
          <div className="h-10 w-28 rounded-xl bg-violet-500/8" />
        </div>
        <div className="space-y-3 pt-2">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="rounded-xl border border-violet-500/8 p-4 space-y-2" style={{ background: 'rgba(139,92,246,0.03)' }}>
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-violet-500/10 flex-shrink-0" />
                <div className="flex-1 space-y-1.5">
                  <div className="h-4 rounded w-3/4 bg-violet-500/10" />
                  <div className="h-3 rounded w-1/3 bg-violet-500/6" />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </AppLayout>
  );

  if (!subject) return (
    <AppLayout>
      <div className="p-6 text-center">
        <p className="text-muted-foreground">Subject not found</p>
        <Link to="/library"><Button className="mt-4">Back to Browser</Button></Link>
      </div>
    </AppLayout>
  );

  const SITE_ORIGIN = 'https://syrabit.ai';
  const BACKEND_ORIGIN = import.meta.env.VITE_BACKEND_URL || '';
  const subjectUrl  = subject.board_slug && subject.class_slug && subject.stream_slug && subject.slug
    ? `${SITE_ORIGIN}/${subject.board_slug}/${subject.class_slug}/${subject.stream_slug}/${subject.slug}`
    : `${SITE_ORIGIN}/subject/${subjectId}`;
  const boardLabel = subject.board_name || 'Assam Board';
  const classLabel = subject.class_name || '';
  const subjectTitle = (subject.name + ' Notes — ' + (classLabel || boardLabel) + ' ' + (subject.stream_name || '')).trim();
  const subjectDesc  = subject.description
    || ('Complete ' + subject.name + ' notes, chapters, and AI explanations for ' + (classLabel || boardLabel) + ' ' + (subject.stream_name || '') + ' students.');
  const ogImage = (subject.thumbnailUrl || subject.thumbnail_url)
    ? `${BACKEND_ORIGIN || SITE_ORIGIN}/api/content/subjects/${subjectId}/og-image.png`
    : undefined;


  return (
    <AppLayout pageTitle={subject.name}>
      <PageMeta
        title={subjectTitle}
        description={subjectDesc.trim()}
        url={subjectUrl}
        image={ogImage}
        pageType="subject"
        pageData={{ subject: { ...subject, chapters } }}
      />
      <div className="p-4 sm:p-6 space-y-6" data-testid="subject-detail">
        {/* Back */}
        <Link to="/library" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft size={14} /> Browser
        </Link>

        {/* Header */}
        <div className="chapter-textbook rounded-2xl p-6">
          <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
            <div className="flex items-center gap-4 min-w-0">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center text-3xl flex-shrink-0" style={{ background: 'rgba(124,58,237,0.08)' }}>
                {subject.icon || '📚'}
              </div>
              <div className="min-w-0">
                <h1 className="text-xl font-semibold truncate" style={{ color: '#111', fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif", border: 'none', margin: 0, padding: 0 }}>{subject.name}</h1>
                <p className="text-sm mt-0.5" style={{ color: '#555' }}>{subject.description}</p>
                <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 mt-2">
                  {subject.board_name && (
                    <span className="text-[11px] font-medium px-1.5 py-0.5 rounded" style={{ background: 'rgba(124,58,237,0.08)', color: '#7c3aed' }}>
                      {subject.board_name}
                    </span>
                  )}
                  {subject.board_name && subject.class_name && (
                    <span className="text-[11px]" style={{ color: '#ccc' }}>·</span>
                  )}
                  {subject.class_name && (
                    <span className="text-[11px] font-medium px-1.5 py-0.5 rounded" style={{ background: 'rgba(59,130,246,0.08)', color: '#2563eb' }}>
                      {subject.class_name}
                    </span>
                  )}
                  {subject.class_name && subject.stream_name && (
                    <span className="text-[11px]" style={{ color: '#ccc' }}>·</span>
                  )}
                  {subject.stream_name && (
                    <span className="text-[11px] font-medium px-1.5 py-0.5 rounded" style={{ background: 'rgba(16,185,129,0.08)', color: '#059669' }}>
                      {subject.stream_name}
                    </span>
                  )}
                  <span className="text-[11px]" style={{ color: '#ccc' }}>·</span>
                  <span className="text-xs" style={{ color: '#888' }}>
                    <BookOpen size={12} className="inline mr-1" />{chapters.length} chapters
                  </span>
                </div>
              </div>
            </div>
            <Link to={`/chat?subject=${subjectId}`} className="flex-shrink-0">
              <Button className="bg-primary hover:bg-primary/90 text-primary-foreground w-full sm:w-auto min-h-[44px]">Ask AI</Button>
            </Link>
          </div>
          {subject.tags?.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-4">
              {subject.tags.map(tag => (
                <span key={tag} className="text-xs px-2.5 py-1 rounded-full" style={{ background: 'rgba(124,58,237,0.06)', color: '#7c3aed', border: '1px solid rgba(124,58,237,0.15)' }}>
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Chapters */}
        <LegacyAccordion subject={subject} subjectId={subjectId} chapters={chapters} />
      </div>
    </AppLayout>
  );
}
