import { useEffect, useState, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { WORKER_API } from '../utils/api';
import { useShare } from '../hooks/useShare';
import PageMeta from '@/components/seo/PageMeta';
import ContinueLearning from '@/components/content/ContinueLearning';
import TrustpilotReviewsSection from '@/components/content/TrustpilotReviewsSection';
import AdSlot from '@/components/ads/AdSlot';
// Quge5 multitag removed in Task #347 — AdSense is the sole monetised network.
import useAdsenseAutoAds from '@/components/ads/useAdsenseAutoAds';
import { MobileNavSwitch } from '@/components/layout/MobileNavSwitch';
import { useLibraryBundle } from '@/hooks/useContent';

/**
 * Best-effort parse of a PYQ slug like "ahsec-class-12-physics-2024" into
 * the shape `pyqDatasetSchema(meta, url)` expects. Worst case we return
 * just `{ slug }` and the schema falls back to its own defaults — never
 * throws. Combines the simple board/year heuristic with class detection
 * and a richer subject reconstruction for cleaner Dataset titles.
 */
function deriveMetaFromSlug(slug, title, description) {
  if (!slug) return { slug: '' };
  const parts = slug.split('-').filter(Boolean);
  const yearMatch = slug.match(/(19|20)\d{2}/);
  const year = yearMatch ? yearMatch[0] : null;
  const boardToken = ['ahsec', 'seba', 'cbse', 'icse'].find((b) => parts.includes(b));
  const board = boardToken ? boardToken.toUpperCase() : null;
  const classMatch = slug.match(/class[-_]?(\d{1,2})/i);
  const educationalLevel = classMatch
    ? `Class ${classMatch[1]}`
    : (board ? board : 'Higher Secondary');
  const subjectTokens = parts.filter((p) =>
    !/^(ahsec|seba|cbse|icse|class|set|paper)$/i.test(p)
    && !/^\d+$/.test(p)
    && !/^(19|20)\d{2}$/.test(p)
  );
  const subject = subjectTokens.length
    ? subjectTokens.map((t) => t.charAt(0).toUpperCase() + t.slice(1)).join(' ')
    : null;
  const fallbackTitle = `${subject ? subject + ' ' : ''}Previous Year Question Paper`
    + `${year ? ' ' + year : ''}${board ? ' — ' + board : ''}`.trim();
  const fallbackDesc = `${board || 'Assam Board'} previous year question paper`
    + `${subject ? ' for ' + subject : ''}${year ? ', ' + year : ''}.`
    + ' Free download and practice on Syrabit.ai.';
  return {
    slug,
    title: title || fallbackTitle,
    description: description || fallbackDesc,
    board,
    subject,
    year,
    educationalLevel,
    inLanguage: 'en-IN',
  };
}

export default function PYQReplicaPage() {
  useAdsenseAutoAds();
  const { slug } = useParams();
  const navigate = useNavigate();
  const [html, setHtml]         = useState('');
  const [title, setTitle]       = useState('');
  const [description, setDescription] = useState('');
  const [loading, setLoading]   = useState(true);
  const [notFound, setNotFound] = useState(false);
  // Real worker-backfilled metadata (Task #338) — null until /meta resolves.
  const [serverMeta, setServerMeta] = useState(null);
  const { sharing, share } = useShare();

  const pyqUrl = `https://syrabit.ai/pyq/${slug || ''}`;
  const pyqMeta = useMemo(() => {
    const fallback = deriveMetaFromSlug(slug, title, description);
    if (!serverMeta) return fallback;
    // Real values from the worker take precedence; slug-derived values fill
    // any gaps so the schema stays well-formed even on partial responses.
    return {
      ...fallback,
      slug: serverMeta.slug || fallback.slug,
      title: serverMeta.title || title || fallback.title,
      description: serverMeta.description || description || fallback.description,
      board: serverMeta.board || fallback.board,
      subject: serverMeta.subject || fallback.subject,
      year: serverMeta.year != null ? String(serverMeta.year) : fallback.year,
      class_name: serverMeta.class_name || undefined,
      educationalLevel: serverMeta.educational_level || fallback.educationalLevel,
      paper_type: serverMeta.paper_type || undefined,
      totalQuestions: serverMeta.total_questions || undefined,
      author: serverMeta.author || undefined,
      license: serverMeta.license || undefined,
      published_at: serverMeta.published_at || undefined,
      updated_at: serverMeta.updated_at || undefined,
      inLanguage: serverMeta.language || fallback.inLanguage,
    };
  }, [slug, title, description, serverMeta]);

  // Resolve a real subject hub path from the library bundle when possible.
  // The PYQ slug parser is best-effort; never invent a path that may 404.
  const { data: libraryBundle } = useLibraryBundle();
  const pyqSubjectPath = useMemo(() => {
    const sub = pyqMeta?.subject?.toLowerCase().trim();
    const board = pyqMeta?.board?.toLowerCase().trim();
    const cls = (pyqMeta?.class_name || '').toString().toLowerCase().trim();
    const subjects = libraryBundle?.subjects || [];
    if (!subjects.length || !sub) return '/library';
    const match = subjects.find((s) => {
      const sname = (s.name || '').toLowerCase();
      const bslug = (s.boardSlug || '').toLowerCase();
      const cslug = (s.classSlug || '').toLowerCase();
      const nameOk = sname === sub || sname.startsWith(sub) || sub.startsWith(sname);
      const boardOk = !board || bslug === board || bslug.includes(board);
      const classOk = !cls || cslug.includes(cls.replace(/\D/g, ''));
      return nameOk && boardOk && classOk && s.slug;
    }) || subjects.find((s) => (s.name || '').toLowerCase() === sub && s.slug);
    if (match && match.boardSlug && match.classSlug && match.slug) {
      return `/${match.boardSlug}/${match.classSlug}/${match.slug}`;
    }
    return '/library';
  }, [libraryBundle, pyqMeta]);

  const handleShare = useCallback(() => {
    const pyqTitle = title || `PYQ — ${slug}`;
    share(pyqTitle, `/pyq/${slug}`);
  }, [slug, title, share]);

  useEffect(() => {
    if (!slug) return;
    // Reset eagerly so a slow / failing meta fetch on a new slug can't
    // bleed stale JSON-LD from the previously-viewed paper into the next
    // page during client-side navigation.
    setServerMeta(null);
    let cancelled = false;
    // Fire-and-forget: real metadata is a progressive enhancement for the
    // JSON-LD schema. The HTML render path doesn't depend on it.
    fetch(`${WORKER_API}/pyq/${slug}/meta`, { method: 'GET' })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (!cancelled && data) setServerMeta(data); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [slug]);

  useEffect(() => {
    if (!slug) return;
    setLoading(true);
    setNotFound(false);
    setHtml('');

    fetch(`${WORKER_API}/pyq/${slug}`, { method: 'GET' })
      .then(async (res) => {
        if (res.status === 404) { setNotFound(true); return; }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const rawHtml = await res.text();
        const titleMatch = rawHtml.match(/<title>([^<]*)<\/title>/i);
        if (titleMatch) setTitle(titleMatch[1]);
        const descMatch  = rawHtml.match(/<meta[^>]+name=["']description["'][^>]+content=["']([^"']+)["']/i)
                        || rawHtml.match(/<meta[^>]+content=["']([^"']+)["'][^>]+name=["']description["']/i);
        if (descMatch) setDescription(descMatch[1]);
        setHtml(rawHtml);
      })
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [slug]);

  useEffect(() => {
    if (title) document.title = title;
  }, [title]);

  if (loading) {
    return (
      <div style={{
        minHeight: '100vh', display: 'flex', alignItems: 'center',
        justifyContent: 'center', background: '#fff', color: '#333',
        fontFamily: '"Times New Roman", Times, serif',
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ width: 32, height: 32, border: '3px solid #e5e7eb', borderTopColor: '#6366f1', borderRadius: '50%', animation: 'spin 0.8s linear infinite', margin: '0 auto 12px' }} />
          <p>Loading question paper…</p>
        </div>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  if (notFound) {
    return (
      <div style={{
        minHeight: '100vh', display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', background: '#fff',
        color: '#333', fontFamily: '"Times New Roman", Times, serif',
        gap: '12px', padding: '20px',
      }}>
        <h1 style={{ fontSize: '1.4em' }}>Question Paper Not Found</h1>
        <p style={{ fontSize: '0.95em', color: '#666', textAlign: 'center' }}>
          The page <code>/pyq/{slug}</code> does not exist.
        </p>
        <a href="/" style={{ color: '#6366f1', fontSize: '0.9em', textDecoration: 'none' }}>← Back to Syrabit.ai</a>
      </div>
    );
  }

  return (
    <div style={{ background: '#fff', minHeight: '100vh', position: 'relative' }}>
      <PageMeta
        title={pyqMeta.title}
        description={pyqMeta.description}
        url={pyqUrl}
        type="article"
        pageType="pyq"
        pageData={{ meta: pyqMeta, doc: pyqMeta }}
      />
      <div
        className="pyq-toolbar"
        style={{
          position: 'fixed', top: 0, left: 0, right: 0, zIndex: 1000,
          background: 'rgba(255,255,255,0.95)', backdropFilter: 'blur(8px)',
          borderBottom: '1px solid #e5e7eb', padding: '8px 16px',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}
      >
        <button
          onClick={() => navigate(-1)}
          style={{
            background: 'none', border: 'none', color: '#6366f1',
            fontSize: '14px', fontWeight: 500, cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: '4px',
            fontFamily: 'system-ui, sans-serif',
          }}
        >
          ← Back
        </button>
        <button
          onClick={handleShare}
          disabled={sharing}
          style={{
            background: '#6366f1', color: '#fff', border: 'none', borderRadius: '8px',
            padding: '6px 14px', fontSize: '13px', fontWeight: 500,
            cursor: sharing ? 'wait' : 'pointer', fontFamily: 'system-ui, sans-serif',
          }}
        >
          {sharing ? 'Sharing…' : 'Share'}
        </button>
      </div>
      {/* Top-of-content ad — first paint slot, sits between the share bar
          and the answer sheet. PYQ pages monetise aggressively (Task #542):
          this is the only route besides /learn that ships ads at all. */}
      <div style={{ maxWidth: 880, margin: '8px auto 0', padding: '48px 16px 0' }}>
        <AdSlot placement="pyq.topOfContent" />
      </div>

      <div style={{ paddingTop: '16px' }}>
        <div dangerouslySetInnerHTML={{ __html: html }} />
      </div>

      {/* In-content ad — premium AdPushup/Magnite demand. The PYQ body is
          opaque server-rendered HTML (dangerouslySetInnerHTML), so we cannot
          reliably splice a slot after the "first answer block" without
          mutating the HTML string. The closest stable placement is the
          natural break between the answer sheet and the ContinueLearning
          rail — students reach this point after scrolling at least one
          answer block, which approximates the intended mid-content position. */}
      <div style={{ maxWidth: 880, margin: '24px auto 0', padding: '0 16px' }}>
        <AdSlot placement="pyq.inContent" />
      </div>

      <div style={{ maxWidth: 880, margin: '24px auto 0', padding: '0 16px' }}>
        <ContinueLearning
          related={[]}
          subjectName={pyqMeta.subject || ''}
          subjectPath={pyqSubjectPath}
          chatHref={pyqMeta.subject
            ? `/chat?prompt=${encodeURIComponent('Help me solve this ' + pyqMeta.subject + ' previous year question paper')}`
            : '/chat'}
        />
      </div>

      {/* End-of-content ad — second AdPushup/Magnite slot. */}
      <div style={{ maxWidth: 880, margin: '24px auto 0', padding: '0 16px' }}>
        <AdSlot placement="pyq.endOfContent" />
      </div>

      <TrustpilotReviewsSection
        subheading={`Finding Syrabit.ai useful${pyqMeta.subject ? ' for ' + pyqMeta.subject : ''}? Share your experience and help other students.`}
        subjectName={pyqMeta.subject || ''}
        boardName={pyqMeta.board || ''}
        className={pyqMeta.class_name || ''}
      />

      <div
        aria-hidden="true"
        className="md:hidden"
        style={{ height: 'calc(4rem + env(safe-area-inset-bottom, 0px))' }}
      />
      <MobileNavSwitch />
    </div>
  );
}
