import { useState, useEffect, useMemo, useRef } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import DOMPurify from 'dompurify';
import { Helmet } from 'react-helmet-async';
import {
  BookOpen, ChevronRight, Clock, BarChart3, Share2,
  ArrowLeft, List, Loader2, AlertCircle, ExternalLink,
  Globe, CheckCircle, GraduationCap, Layers, HelpCircle,
  FlipHorizontal, ChevronDown, ChevronUp,
} from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import { apiClient, API_BASE, seoRelatedByChapter } from '@/utils/api';
import { useLibraryBundle, useLibraryBundleSlim } from '@/hooks/useContent';
import { findSiblingChapters, siblingsAsRelated } from '@/utils/siblingChapter';
import { useShare } from '@/hooks/useShare';
import StickyToc from '@/components/ui/StickyToc';
import { learnArticleSchema } from '@/lib/jsonld';
import ContinueLearning from '@/components/content/ContinueLearning';
import AdSlot from '@/components/ads/AdSlot';
// Quge5 multitag removed in Task #347 — AdSense is the sole monetised network.
import useAdsenseAutoAds from '@/components/ads/useAdsenseAutoAds';

function buildToc(headingsJson) {
  try {
    return JSON.parse(headingsJson || '[]');
  } catch {
    return [];
  }
}

function SchemaOrg({ doc }) {
  const pageUrl = `https://syrabit.ai/learn/${doc.seo_slug}`;
  const graph = learnArticleSchema(doc, pageUrl);
  if (!graph) return null;
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(graph) }}
    />
  );
}


function injectHeadingIds(html) {
  return html.replace(/<(h[1-3])>(.*?)<\/h[1-3]>/gi, (_, tag, text) => {
    const plain = text.replace(/<[^>]+>/g, '').trim();
    const id = plain.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    return `<${tag} id="${id}">${text}</${tag}>`;
  });
}

export default function LearnPage() {
  useAdsenseAutoAds();
  const { slug } = useParams();
  const navigate = useNavigate();
  const [doc, setDoc]             = useState(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);
  const [activeId, setActiveId]   = useState('');
  const [pyqs, setPyqs]               = useState([]);
  const [markWise, setMarkWise]       = useState({});
  const [flashcards, setFlashcards]   = useState([]);
  const [relatedTopics, setRelatedTopics] = useState([]);
  const [showAllPyqs, setShowAllPyqs] = useState(false);
  const [flippedCards, setFlippedCards] = useState(new Set());
  const articleRef = useRef(null);
  const { data: libraryBundle } = useLibraryBundle();

  useEffect(() => {
    setLoading(true);
    setError(null);
    setPyqs([]);
    setMarkWise({});
    setFlashcards([]);
    setRelatedTopics([]);
    setShowAllPyqs(false);
    setFlippedCards(new Set());
    apiClient().get(`/content/cms-documents/${slug}`)
      .then(r => {
        setDoc(r.data);
        const chId = r.data?.linked_chapter_id;
        if (chId) {
          apiClient().get(`/content/chapters/${chId}/topic-pyqs?limit=20`)
            .then(pr => {
              setPyqs(pr.data?.pyqs || []);
              setMarkWise(pr.data?.mark_wise || {});
            })
            .catch(() => {});
          apiClient().get(`/content/chapters/${chId}/flashcards?limit=10`)
            .then(fr => setFlashcards(fr.data?.flashcards || []))
            .catch(() => {});
          seoRelatedByChapter(chId, r.data?.linked_topic_id || null, 6)
            .then(rr => setRelatedTopics(rr.data?.related || rr.data?.items || []))
            .catch(() => {});
        }
      })
      .catch(e => {
        setError(e.response?.status === 404 ? 'not-found' : 'error');
      })
      .finally(() => setLoading(false));
  }, [slug]);

  const toggleFlip = (idx) => setFlippedCards(prev => {
    const next = new Set(prev);
    next.has(idx) ? next.delete(idx) : next.add(idx);
    return next;
  });

  useEffect(() => {
    if (!doc) return;
    const obs = new IntersectionObserver(
      entries => {
        for (const e of entries) {
          if (e.isIntersecting) setActiveId(e.target.id);
        }
      },
      { rootMargin: '-10% 0% -80% 0%', threshold: 0 }
    );
    const headings = articleRef.current?.querySelectorAll('h1, h2, h3') || [];
    headings.forEach(h => h.id && obs.observe(h));
    return () => obs.disconnect();
  }, [doc]);

  const toc = useMemo(() => buildToc(doc?.headings), [doc]);

  const readTime = useMemo(() => {
    if (!doc) return 0;
    const wpm = 200;
    return Math.max(1, Math.ceil((doc.word_count || 0) / wpm));
  }, [doc]);

  const processedHtml = useMemo(() => {
    if (!doc?.content_html) return '';
    return injectHeadingIds(doc.content_html);
  }, [doc]);

  const { sharing, share: handleShare } = useShare();

  if (loading) {
    return (
      <AppLayout>
        <div className="min-h-screen flex items-center justify-center">
          <Loader2 size={28} className="animate-spin text-violet-400" />
        </div>
      </AppLayout>
    );
  }

  if (error === 'not-found') {
    return (
      <AppLayout>
        <div className="min-h-screen flex flex-col items-center justify-center gap-4">
          <AlertCircle size={40} className="text-amber-400" />
          <h1 className="text-xl font-bold text-foreground">Page Not Found</h1>
          <p className="text-muted-foreground text-sm">This study resource doesn't exist or hasn't been published yet.</p>
          <Link to="/library" className="mt-2 h-9 px-4 rounded-xl bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium flex items-center gap-2">
            <ArrowLeft size={14} /> Back to Browser
          </Link>
        </div>
      </AppLayout>
    );
  }

  if (error) {
    return (
      <AppLayout>
        <div className="min-h-screen flex flex-col items-center justify-center gap-4">
          <AlertCircle size={40} className="text-red-400" />
          <p className="text-muted-foreground text-sm">Failed to load content. Please try again.</p>
          <button onClick={() => window.location.reload()} className="h-9 px-4 rounded-xl bg-accent hover:bg-accent/80 text-foreground text-sm">
            Retry
          </button>
        </div>
      </AppLayout>
    );
  }

  const tags = doc.seo_tags ? doc.seo_tags.split(',').map(t => t.trim()).filter(Boolean) : [];

  return (
    <AppLayout>
      {doc && (
        <>
          <Helmet>
            <title>{doc.title} | Syrabit.ai</title>
            <meta name="description" content={doc.meta_description || doc.description || doc.title} />
            <meta property="og:title" content={`${doc.title} | Syrabit.ai`} />
            <meta property="og:description" content={doc.meta_description || doc.description || ''} />
            <meta property="og:type" content="article" />
            <meta property="og:url" content={`https://syrabit.ai/learn/${doc.seo_slug}`} />
            {doc.thumbnail_url && <meta property="og:image" content={doc.thumbnail_url} />}
            <meta name="keywords" content={doc.seo_tags || ''} />
            <link rel="canonical" href={`https://syrabit.ai/learn/${doc.seo_slug}`} />
          </Helmet>
          <SchemaOrg doc={doc} />
        </>
      )}

      <div className="min-h-screen pb-20">
        <div className="max-w-6xl mx-auto px-3 sm:px-4 md:px-6 pt-6">

          {/* Breadcrumb */}
          <nav className="flex items-center gap-1 text-xs text-muted-foreground/50 mb-6" aria-label="Breadcrumb">
            <Link to="/" className="hover:text-foreground/70 transition-colors">Home</Link>
            <ChevronRight size={11} className="flex-shrink-0" />
            <Link to="/library" className="hover:text-foreground/70 transition-colors">Library</Link>
            <ChevronRight size={11} className="flex-shrink-0" />
            <span className="text-foreground/60 truncate max-w-xs">{doc?.title}</span>
          </nav>

          {/* Hero header */}
          <div className="mb-8">
            {/* Syllabus type banner */}
            {doc?.type === 'syllabus' && (
              <div className="flex items-center gap-2 mb-4 px-3 py-2 rounded-xl border border-emerald-500/20 bg-emerald-500/[0.06] w-fit">
                <GraduationCap size={14} className="text-emerald-400" />
                <span className="text-xs font-semibold text-emerald-300">Official Syllabus</span>
                <span className="text-muted-foreground/30 text-xs">·</span>
                <span className="text-xs text-muted-foreground/50">AI-assisted learning available</span>
              </div>
            )}
            {tags.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-4">
                {tags.slice(0, 5).map(tag => (
                  <span
                    key={tag}
                    className="px-2.5 py-1 rounded-full text-[11px] font-medium border"
                    style={tag.toLowerCase() === 'syllabus'
                      ? { borderColor: 'rgba(16,185,129,0.25)', background: 'rgba(16,185,129,0.08)', color: '#6ee7b7' }
                      : { borderColor: 'rgba(139,92,246,0.2)', background: 'rgba(139,92,246,0.08)', color: '#a78bfa' }
                    }
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
            <h1 className="text-2xl md:text-3xl font-bold text-foreground leading-tight mb-3">{doc?.title}</h1>
            {(doc?.meta_description || doc?.description) && (
              <p className="text-muted-foreground text-base leading-relaxed max-w-3xl">
                {doc.meta_description || doc.description}
              </p>
            )}
            <div className="flex items-center gap-4 mt-4 flex-wrap">
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground/50">
                <Clock size={12} />
                <span>{readTime} min read</span>
              </div>
              {doc?.word_count > 0 && (
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground/50">
                  <BarChart3 size={12} />
                  <span>{doc.word_count.toLocaleString()} words</span>
                </div>
              )}
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground/50">
                <CheckCircle size={12} className="text-emerald-400" />
                <span>Published · Syrabit.ai</span>
              </div>
              <button
                onClick={() => handleShare(doc?.title || 'Study on Syrabit.ai', `/learn/${slug}`)}
                disabled={sharing}
                className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-muted-foreground/50 hover:text-foreground/70 border border-border/20 hover:border-border/40 transition-colors disabled:opacity-50"
              >
                {sharing ? <Loader2 size={11} className="animate-spin" /> : <Share2 size={11} />} Share
              </button>
            </div>
          </div>

          {/* Subject name card — context pill above the lesson */}
          {(doc?.subject_name || doc?.subject_id) && (
            <div className="flex items-center gap-2 mb-4">
              <div
                className="flex items-center gap-2 px-3 py-1.5 rounded-xl w-fit"
                style={{ background: 'rgba(139,92,246,0.08)', border: '1px solid rgba(139,92,246,0.18)' }}
              >
                <BookOpen size={12} className="text-violet-400 shrink-0" />
                <span className="text-[11px] font-semibold text-violet-300/70 uppercase tracking-wider">Subject</span>
                <span className="text-muted-foreground/30 text-xs">·</span>
                {doc.subject_id ? (
                  <Link
                    to={`/subject/${doc.subject_id}`}
                    className="text-[12px] font-semibold text-violet-300 hover:text-violet-200 transition-colors"
                  >
                    {doc.subject_name || doc.subject_id}
                  </Link>
                ) : (
                  <span className="text-[12px] font-semibold text-violet-300">{doc.subject_name}</span>
                )}
              </div>
            </div>
          )}

          {/* Top-of-content ad — Adsterra slot, sits between the subject pill
              and the article body. First of the maximised /learn placements
              (Task #542). Collapses to nothing when the env var is unset. */}
          <div className="mb-6">
            <AdSlot placement="learn.topOfContent" />
          </div>

          {/* Main layout: content + TOC + (desktop) sidebar ad */}
          <div className="flex gap-8 items-start">
            {/* Article */}
            <article
              ref={articleRef}
              className="flex-1 min-w-0 rounded-2xl overflow-hidden blog-view-tab"
              style={{ background: '#ffffff', border: '1px solid #e5e7eb', boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}
            >
              {doc?.thumbnail_url && (
                <div className="w-full aspect-video overflow-hidden">
                  <img
                    src={doc.thumbnail_url}
                    alt={doc.alt_text || doc.title}
                    className="w-full h-full object-cover"
                    loading="lazy"
                    width="800"
                    height="450"
                  />
                </div>
              )}
              <div className="p-4 sm:p-6 md:p-10">
                {processedHtml ? (
                  <div
                    className="learn-article max-w-none"
                    dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(processedHtml) }}
                  />
                ) : doc?.content ? (
                  <div className="learn-article max-w-none">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{doc.content}</ReactMarkdown>
                  </div>
                ) : (
                  <p className="italic text-center py-12" style={{ color: '#9ca3af' }}>No content available.</p>
                )}
              </div>
            </article>

            {/* TOC + sidebar ad (desktop only). The sidebar slot is one of
                the maximised /learn placements (Task #542) and is
                deliberately gated to `lg:` so mobile never reserves the
                600px column. */}
            <aside className="hidden lg:flex flex-col gap-4 w-[260px] shrink-0 sticky top-24 self-start">
              <StickyToc
                headings={toc}
                activeId={activeId}
                variant="card"
                labelIcon={<List size={13} className="text-primary" />}
                minItems={1}
              />
              <AdSlot placement="learn.sidebar" />
            </aside>
            {/* Mobile/tablet — TOC only (no sidebar ad to keep CLS < 0.1) */}
            <div className="lg:hidden">
              <StickyToc
                headings={toc}
                activeId={activeId}
                variant="card"
                labelIcon={<List size={13} className="text-primary" />}
                minItems={1}
              />
            </div>
          </div>

          {/* In-content ad — Adsterra slot, sits between the article body
              and the Important Questions section. Reserves height even
              when disabled-but-mounted? No — disabled slots render nothing. */}
          <div className="mt-6">
            <AdSlot placement="learn.inContent" />
          </div>

          {/* Important Questions (mark-wise) */}
          {pyqs.length > 0 && (
            <div className="mt-8 rounded-2xl border border-amber-500/15 overflow-hidden">
              <div className="px-5 py-3.5 border-b border-amber-500/10 flex items-center gap-2"
                style={{ background: 'rgba(245,158,11,0.05)' }}>
                <HelpCircle size={15} className="text-amber-400" />
                <span className="text-sm font-bold text-foreground">Important Questions</span>
                <span className="ml-1 text-xs text-muted-foreground/50">— mark-wise for exam</span>
                <span className="ml-auto px-2 py-0.5 rounded-full text-[10px] font-semibold"
                  style={{ background: 'rgba(245,158,11,0.15)', color: '#fbbf24', border: '1px solid rgba(245,158,11,0.25)' }}>
                  {pyqs.length} questions
                </span>
              </div>
              <div style={{ background: 'hsl(var(--card))' }}>
                {/* Mark-wise grouped display when mark_wise data is available */}
                {Object.keys(markWise).length > 0
                  ? (() => {
                      const markOrder = ['1','2','3','5','10'];
                      const markLabels = { '1':'1 Mark','2':'2 Marks','3':'3 Marks','5':'5 Marks','10':'10 Marks' };
                      const shown = showAllPyqs ? markOrder : markOrder.slice(0, 3);
                      return shown.map(mk => {
                        const qs = markWise[mk];
                        if (!qs || qs.length === 0) return null;
                        return (
                          <div key={mk}>
                            <div className="px-5 py-2 flex items-center gap-2"
                              style={{ background: 'rgba(245,158,11,0.04)', borderBottom: '1px solid rgba(245,158,11,0.07)' }}>
                              <span className="text-[10px] font-bold uppercase tracking-widest"
                                style={{ color: '#fbbf24' }}>{markLabels[mk] || `${mk} Marks`}</span>
                              <span className="text-[9px] text-muted-foreground/40">{qs.length} questions</span>
                            </div>
                            {qs.map((q, i) => {
                              const qText = typeof q === 'string' ? q : (q.question || '');
                              const qAns  = typeof q === 'object' ? q.answer : '';
                              return (
                                <div key={i} className="px-5 py-3.5 border-b last:border-0"
                                  style={{ borderColor: 'rgba(245,158,11,0.06)' }}>
                                  <div className="flex items-start gap-3">
                                    <span className="flex-shrink-0 w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-bold mt-0.5"
                                      style={{ background: 'rgba(245,158,11,0.12)', color: '#fbbf24' }}>
                                      {i + 1}
                                    </span>
                                    <div className="min-w-0 flex-1">
                                      <p className="text-sm font-medium text-foreground/85 leading-relaxed">{qText}</p>
                                      {qAns && (
                                        <div className="mt-2 rounded-lg px-3 py-2 text-xs text-muted-foreground leading-relaxed"
                                          style={{ background: 'rgba(245,158,11,0.05)', border: '1px solid rgba(245,158,11,0.09)' }}>
                                          {qAns}
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        );
                      });
                    })()
                  : /* Flat list fallback */
                    (showAllPyqs ? pyqs : pyqs.slice(0, 5)).map((q, i) => (
                      <div key={q.id || i}
                        className="px-5 py-4 border-b last:border-0"
                        style={{ borderColor: 'rgba(245,158,11,0.07)' }}>
                        <div className="flex items-start gap-3">
                          <span className="flex-shrink-0 w-6 h-6 rounded-lg flex items-center justify-center text-[11px] font-bold mt-0.5"
                            style={{ background: 'rgba(245,158,11,0.12)', color: '#fbbf24' }}>
                            {i + 1}
                          </span>
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-medium text-foreground/85 leading-relaxed mb-2">{q.question}</p>
                            {q.answer && (
                              <div className="rounded-lg px-3 py-2.5 text-sm text-muted-foreground leading-relaxed"
                                style={{ background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.10)' }}>
                                {q.answer}
                              </div>
                            )}
                            {q.marks && (
                              <span className="inline-block mt-1.5 text-[10px] px-1.5 py-0.5 rounded"
                                style={{ background: 'rgba(245,158,11,0.10)', color: '#fcd34d' }}>
                                {q.marks} marks
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    ))
                }
                {(Object.keys(markWise).length > 0
                  ? Object.values(markWise).reduce((a, b) => a + (b?.length || 0), 0)
                  : pyqs.length) > 5 && (
                  <button
                    onClick={() => setShowAllPyqs(v => !v)}
                    className="w-full py-3 flex items-center justify-center gap-1.5 text-xs font-medium text-amber-400/70 hover:text-amber-300 transition-colors"
                    style={{ background: 'rgba(245,158,11,0.04)', borderTop: '1px solid rgba(245,158,11,0.07)' }}
                  >
                    {showAllPyqs
                      ? <><ChevronUp size={13} /> Show less</>
                      : <><ChevronDown size={13} /> Show all {pyqs.length} questions</>}
                  </button>
                )}
              </div>
            </div>
          )}

          {/* After-PYQs ad — PropellerAds slot, sits between the Important
              Questions block and the Flashcards block. Only mounts (and
              therefore only reserves height) when there were questions to
              show; rendered unconditionally is fine because <AdSlot />
              collapses to nothing when its env var is unset. */}
          {pyqs.length > 0 && (
            <div className="mt-6">
              <AdSlot placement="learn.afterPyqs" />
            </div>
          )}

          {/* Flashcards */}
          {flashcards.length > 0 && (
            <div className="mt-6 rounded-2xl border border-emerald-500/15 overflow-hidden">
              <div className="px-5 py-3.5 border-b border-emerald-500/10 flex items-center gap-2"
                style={{ background: 'rgba(16,185,129,0.05)' }}>
                <FlipHorizontal size={15} className="text-emerald-400" />
                <span className="text-sm font-bold text-foreground">Memory Tricks & Flashcards</span>
                <span className="ml-1 text-xs text-muted-foreground/50">— tap to flip</span>
                <span className="ml-auto px-2 py-0.5 rounded-full text-[10px] font-semibold"
                  style={{ background: 'rgba(16,185,129,0.12)', color: '#6ee7b7', border: '1px solid rgba(16,185,129,0.20)' }}>
                  {flashcards.length} cards
                </span>
              </div>
              <div className="p-4 grid grid-cols-1 sm:grid-cols-2 gap-3"
                style={{ background: 'hsl(var(--card))' }}>
                {flashcards.map((fc, i) => {
                  const flipped = flippedCards.has(i);
                  return (
                    <button
                      key={fc.id || i}
                      onClick={() => toggleFlip(i)}
                      className="text-left rounded-xl p-4 transition-all cursor-pointer select-none"
                      style={{
                        background: flipped
                          ? 'rgba(16,185,129,0.10)'
                          : 'hsl(var(--muted) / 0.3)',
                        border: flipped
                          ? '1px solid rgba(16,185,129,0.25)'
                          : '1px solid hsl(var(--border) / 0.3)',
                      }}
                    >
                      <div className="text-[10px] font-semibold uppercase tracking-wider mb-1.5"
                        style={{ color: flipped ? '#059669' : 'hsl(var(--muted-foreground) / 0.4)' }}>
                        {flipped ? 'Answer' : 'Question'}
                      </div>
                      <p className="text-sm leading-relaxed"
                        style={{ color: flipped ? '#065f46' : 'hsl(var(--foreground) / 0.8)' }}>
                        {flipped ? (fc.back || fc.answer || '—') : (fc.front || fc.question)}
                      </p>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* After-flashcards ad — Adsterra slot, sits between the
              Flashcards block and the ContinueLearning rail. Mounts
              only when there were flashcards (matches the parent
              `flashcards.length > 0` block visually). */}
          {flashcards.length > 0 && (
            <div className="mt-6">
              <AdSlot placement="learn.afterFlashcards" />
            </div>
          )}

          {(() => {
            // Chapter-order prev/next derived from the library bundle for the
            // doc's parent chapter. Falls back gracefully when bundle is not
            // yet loaded.
            const subjChapters = (libraryBundle?.chapters || []).filter(
              (ch) => ch.subject_id && doc?.subject_id && ch.subject_id === doc.subject_id
            );
            const sub = (libraryBundle?.subjects || []).find((s) => s.id === doc?.subject_id) || null;
            const canonicalSubjectPath = (sub && sub.boardSlug && sub.classSlug && sub.slug)
              ? `/${sub.boardSlug}/${sub.classSlug}/${sub.slug}`
              : null;
            // For the "All chapters" CTA: link back to subject hub if known,
            // otherwise just send the user to the library index.
            const subjectBasePath = canonicalSubjectPath || '/library';

            const { prev: pCh, next: nCh } = findSiblingChapters(
              subjChapters,
              doc?.linked_chapter_id,
              null,
            );
            // Prev/Next must point to real chapter URLs. Hide them entirely
            // when we can't construct a canonical /board/class/subject path.
            const prevLink = (canonicalSubjectPath && pCh && pCh.slug)
              ? { title: pCh.title || pCh.slug, path: `${canonicalSubjectPath}/${pCh.slug}` }
              : null;
            const nextLink = (canonicalSubjectPath && nCh && nCh.slug)
              ? { title: nCh.title || nCh.slug, path: `${canonicalSubjectPath}/${nCh.slug}` }
              : null;

            // Related: prefer endpoint result, backfill with sibling chapters
            // until at least 4 (target 4–6) so the rail is never sparse.
            const seedRelated = (relatedTopics || []).map((rt) => ({
              id: rt.id,
              title: rt.title,
              seo_path: rt.seo_path || `/learn/${rt.slug}`,
            }));
            const siblings = canonicalSubjectPath
              ? siblingsAsRelated(subjChapters, doc?.linked_chapter_id, null, canonicalSubjectPath, 8)
              : [];
            const related = (() => {
              const out = [...seedRelated];
              if (out.length < 4) {
                const seen = new Set(out.map((r) => r.seo_path));
                for (const s of siblings) {
                  if (out.length >= 6) break;
                  if (!seen.has(s.seo_path)) out.push(s);
                }
              }
              return out.slice(0, 6);
            })();

            return (
              <ContinueLearning
                prev={prevLink}
                next={nextLink}
                related={related}
                subjectName={doc?.subject_name || sub?.name || ''}
                subjectPath={subjectBasePath}
                chatHref={doc?.subject_id ? `/chat?subject=${doc.subject_id}${doc?.linked_chapter_id ? `&chapter=${doc.linked_chapter_id}` : ''}` : '/chat'}
              />
            );
          })()}

          {/* End-of-content ad — PropellerAds slot. */}
          <div className="mt-6">
            <AdSlot placement="learn.endOfContent" />
          </div>
        </div>
      </div>
    </AppLayout>
  );
}
