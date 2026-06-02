import { BrowserRouter, Routes, Route, Navigate, useParams } from "react-router-dom";
import { lazy, Suspense, useEffect, useState } from "react";
import { PageTracker } from "@/utils/usePageTracking";
import { AuthProvider } from "@/context/AuthContext";
import { LanguageProvider } from "@/context/LanguageContext";
import { AuthGuard } from "@/components/AuthGuard";
import { AdminGuard } from "@/components/AdminGuard";
import { StaffGuard } from "@/components/StaffGuard";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "./queryClient";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { HelmetProvider } from "react-helmet-async";
import Analytics from "@/utils/analytics";
const PWAInstallPrompt = lazy(() => import("@/components/PWAInstallPrompt"));
const CookieConsent = lazy(() => import("@/components/CookieConsent"));
const ReviewPrompt = lazy(() => import("@/components/ReviewPrompt"));
const LazyToaster = lazy(() => import("sonner").then(m => ({ default: m.Toaster })));
// Only GlobalSeo is lazy — HelmetProvider must wrap the entire app so that
// per-page <PageMeta>/Helmet usage on Library, Chapter, Pricing, etc. still
// works. (Task #381 fix after architect review.)
const LazyGlobalSeo = lazy(() => import("@/components/seo/GlobalSeo"));
// Task #727 — emit the Trustpilot aggregate-rating JSON-LD on every
// route so FAQ/About/Pricing/Learn/blog and any other indexable page
// becomes eligible for the Google review-stars rich snippet, not just
// the 5 content pages that render <TrustpilotReviewsSection />.
const LazyGlobalTrustpilotJsonLd = lazy(() => import("@/components/seo/GlobalTrustpilotJsonLd"));
import { apiClient } from "@/utils/api";
import { probeImageResizer } from "@/utils/imageCdn";

// ── React Query client ────────────────────────────────────────────────────────
// `queryClient` lives in its own leaf module (`./queryClient`) so it has no
// outgoing edges in the dependency graph. This avoids a Rollup SSR-build
// failure where named-export resolution from `App.jsx` would intermittently
// see the symbol as missing due to a circular-import chain via
// AuthContext/LanguageContext/ErrorBoundary etc. Re-exported for any
// existing imports that still pull it from `./App`.
// NOTE: This re-export is intentional for backward compatibility. External
// consumers (tests, legacy code) may import { queryClient } from './App'.
// Do not remove without updating all import sites.
export { queryClient };

// Seed React Query from data baked into the prerendered HTML so the
// first render on the client matches the server-rendered markup
// exactly. `__SSR_QUERIES__` is the generalised list used by the
// prerendered library / subject / chapter routes — each entry is
// `{ key: [...queryKey], data: <payload> }`.
if (typeof window !== "undefined" && Array.isArray(window.__SSR_QUERIES__)) {
  for (const q of window.__SSR_QUERIES__) {
    try {
      if (q && Array.isArray(q.key)) queryClient.setQueryData(q.key, q.data);
    } catch {}
  }
}


import { pageImports, prefetchCriticalRoutes } from "@/utils/pageImports";
import { lazyPreload } from "@/utils/lazyPreload";

// ── React.lazy() code splitting — all pages ────────────────────────────────
const LandingPage        = lazy(() => import("@/pages/LandingPage"));
const LoginPage          = lazy(() => import("@/pages/LoginPage"));
const SignupPage         = lazy(() => import("@/pages/SignupPage"));
const ResetPasswordPage  = lazy(() => import("@/pages/ResetPasswordPage"));
const OnboardingPage     = lazy(() => import("@/pages/OnboardingPage"));
// LibraryPage, SubjectLandingPage, ChapterPage, and ChatPage are the
// four prerendered routes. They are split into their own chunks via
// `lazyPreload`, and `index.jsx` calls `preloadPageForKind(kind)` to
// fetch + prime the matching chunk BEFORE `hydrateRoot()` runs. That
// way each prerendered route only ships the JS for the page it's
// actually rendering (e.g. /library no longer pulls ChatPage,
// ChapterPage, MarkdownContent, StickyToc, etc. on first load — Task
// #395), while hydration stays byte-identical to the SSR snapshot
// because the lazy wrapper resolves synchronously after preload.
// Replaces the eager-import workaround used in Tasks #382 / #385 / #387.
const LibraryPage        = lazyPreload(() => import("@/pages/LibraryPage"));
const SubjectLandingPage = lazyPreload(() => import("@/pages/SubjectLandingPage"));
const ChapterPage        = lazyPreload(() => import("@/pages/ChapterPage"));
const ChatPage           = lazyPreload(() => import("@/pages/ChatPage"));
const SubjectPage        = lazy(() => import("@/pages/SubjectPage"));

// Preload the page chunk for a prerendered route's hydration kind.
// `kind` matches the `data-hydrate` attribute baked into the SSR HTML
// by the prerender scripts ("library" | "chat" | "subject" | "chapter").
// Resolves to the loaded module so the caller can `await` it before
// invoking `hydrateRoot()` — guarantees React.lazy resolves
// synchronously on first render. Returns `null` for unknown kinds so
// the caller can no-op without branching.
const PRERENDER_KIND_LOADERS = {
  library: () => LibraryPage.preload(),
  chat:    () => ChatPage.preload(),
  subject: () => SubjectLandingPage.preload(),
  chapter: () => ChapterPage.preload(),
};
export function preloadPageForKind(kind) {
  const loader = PRERENDER_KIND_LOADERS[kind];
  return loader ? loader() : null;
}
const HistoryPage        = lazy(pageImports.history);
const ProfilePage        = lazy(pageImports.profile);
const MyMemoriesPage     = lazy(() => import("@/pages/MyMemoriesPage"));
const PricingPage        = lazy(() => import("@/pages/PricingPage"));
const TermsPage          = lazy(() => import("@/pages/TermsPage"));
const PrivacyPage        = lazy(() => import("@/pages/PrivacyPage"));
const NotFoundPage       = lazy(() => import("@/pages/NotFoundPage"));
const AdminLoginPage     = lazy(() => import("@/pages/AdminLoginPage"));
const AdminPage          = lazy(() => import("@/pages/AdminPage"));
const StaffDashboard     = lazy(() => import("@/pages/staff/StaffDashboard"));
const ExamRoutinePage    = lazy(() => import("@/pages/ExamRoutinePage"));
const CurriculumMap      = lazy(() => import("@/pages/CurriculumMap"));
const PaymentSuccessPage = lazy(() => import("@/pages/PaymentSuccessPage"));
const PaymentCancelPage  = lazy(() => import("@/pages/PaymentCancelPage"));
const StatusPage         = lazy(() => import("@/pages/StatusPage"));
const LearnPage              = lazy(() => import("@/pages/LearnPage"));
const PYQReplicaPage         = lazy(() => import("@/pages/PYQReplicaPage"));
const PersonalizedCmsPage    = lazy(() => import("@/pages/PersonalizedCmsPage"));
const AboutPage              = lazy(() => import("@/pages/AboutPage"));
const TechnologyPage         = lazy(() => import("@/pages/TechnologyPage"));
const BrowsePage             = lazy(() => import("@/pages/BrowsePage"));
const BrowserPage            = lazy(() => import("@/pages/BrowserPage"));
const NotebookPage           = lazy(() => import("@/pages/NotebookPage"));
const FlashcardsPage         = lazy(() => import("@/pages/FlashcardsPage"));
const GuardianPage           = lazy(() => import("@/pages/GuardianPage"));
const StudyTestHarnessPage   = lazy(() => import("@/pages/StudyTestHarnessPage"));

// ── Page loading fallback (boot splash) ──────────────────────────────────────
const PageFallbackContent = () => (
  <div
    className="min-h-screen flex items-center justify-center bg-background"
    role="status"
    aria-label="Loading Syrabit.ai"
  >
    <div className="flex flex-col items-center gap-4">
      <div className="relative">
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center pulse-glow overflow-hidden"
          aria-hidden="true"
        >
          <img src="/logo-144.webp" alt="" width="56" height="56" fetchpriority="high" className="w-14 h-14 object-cover" />
        </div>
        <div
          className="absolute orbit-ring"
          style={{
            inset: "-5px",
            borderRadius: "1rem",
            border: "1px solid hsl(var(--primary) / 0.25)",
          }}
          aria-hidden="true"
        />
      </div>
      <svg className="w-5 h-5 animate-spin text-primary" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M21 12a9 9 0 1 1-6.219-8.56" />
      </svg>
      <span className="sr-only">Loading page…</span>
    </div>
  </div>
);

// Task #405: if hydration's Suspense fallback sticks around for an
// unusually long time (slow chunk fetch, page-chunk preload that
// rejected, etc.) we surface a recovery hint with a one-click
// reload, instead of leaving users staring at a spinner. We also
// emit one analytics event so we can track regressions in
// production.
function StalledRecoveryHint({ kind, onReload }) {
  return (
    <div
      className="min-h-screen flex items-center justify-center bg-background px-6"
      role="status"
      aria-live="polite"
    >
      <div className="max-w-sm w-full text-center flex flex-col items-center gap-4">
        <div className="w-12 h-12 rounded-2xl flex items-center justify-center overflow-hidden">
          {/* Use the 144px variant; the 485x486 master is wasteful at 48px display. */}
          <img
            src="/logo-144.webp"
            srcSet="/logo-56.webp 1x, /logo-144.webp 2x"
            alt=""
            width="48"
            height="48"
            decoding="async"
            className="w-12 h-12 object-cover"
          />
        </div>
        <div className="text-base font-medium text-foreground">
          This is taking longer than usual.
        </div>
        <div className="text-sm text-muted-foreground">
          A page resource didn’t load. Refreshing usually fixes it.
        </div>
        <button
          type="button"
          onClick={onReload}
          className="mt-1 inline-flex items-center justify-center px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
        >
          Refresh page
        </button>
        {kind ? (
          <div className="text-xs text-muted-foreground/70">page: {kind}</div>
        ) : null}
      </div>
    </div>
  );
}

// Only treat a Suspense fallback as a "hydration stall" when the
// fallback shows up BEFORE initial hydration completes on a
// prerendered route. Lazy chunks that suspend during later SPA
// navigations are normal route loads, not hydration regressions.
function isInitialHydrationContext() {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return false;
  }
  if (window.__SYRABIT_HYDRATED__) return false;
  const rootEl = document.getElementById("root");
  return Boolean(rootEl?.dataset?.hydrate);
}

function DeferredFallback({ delay = 150, recoveryDelay = 5000 }) {
  const [show, setShow] = useState(false);
  const [stalled, setStalled] = useState(false);
  useEffect(() => {
    const showTimer = setTimeout(() => setShow(true), delay);
    let stalledTimer = null;
    if (isInitialHydrationContext()) {
      stalledTimer = setTimeout(() => {
        // Re-check at fire time — hydration may have completed in the
        // intervening window.
        if (!isInitialHydrationContext()) return;
        setStalled(true);
        try {
          const rootEl = document.getElementById("root");
          const kind = rootEl?.dataset?.hydrate || null;
          const preloadFailed = Boolean(
            window.__SYRABIT_HYDRATE_PRELOAD_FAILED__,
          );
          Analytics.hydrateStalled?.({
            kind,
            path: window.location.pathname,
            ms: recoveryDelay,
            preload_failed: preloadFailed,
          });
        } catch {}
      }, recoveryDelay);
    }
    return () => {
      clearTimeout(showTimer);
      if (stalledTimer) clearTimeout(stalledTimer);
    };
  }, [delay, recoveryDelay]);

  if (stalled) {
    const rootEl =
      typeof document !== "undefined"
        ? document.getElementById("root")
        : null;
    const kind = rootEl?.dataset?.hydrate || null;
    return (
      <StalledRecoveryHint
        kind={kind}
        onReload={() => {
          try { window.location.reload(); } catch {}
        }}
      />
    );
  }
  return show ? <PageFallbackContent /> : null;
}

function LegacyTopicRedirect() {
  const { board, classSlug, subjectSlug, chapterSlug } = useParams();
  return <Navigate to={`/${board}/${classSlug}/${subjectSlug}/${chapterSlug}`} replace />;
}

// ── Routes (extracted so SSR can render them inside a StaticRouter) ───────
export function AppRoutes() {
  return (
    <>
      <Routes>
      {/* ── Public routes ── */}
      <Route path="/"         element={<Navigate to="/library" replace />} />
      <Route path="/home"     element={<LandingPage />} />
      <Route path="/pricing"  element={<PricingPage />} />
      <Route path="/terms"    element={<TermsPage />} />
      <Route path="/privacy"       element={<PrivacyPage />} />
      <Route path="/about"         element={<AboutPage />} />
      <Route path="/technology"   element={<TechnologyPage />} />
      <Route path="/status"        element={<StatusPage />} />
      <Route path="/exam-routine" element={<ExamRoutinePage />} />
      <Route path="/payment/success" element={<PaymentSuccessPage />} />
      <Route path="/payment/cancel" element={<PaymentCancelPage />} />

      {/* ── Auth routes ── */}
      <Route path="/login"          element={<LoginPage />} />
      <Route path="/signup"         element={<SignupPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />

      {/* ── Onboarding (self-guarded) ── */}
      <Route path="/onboarding" element={<OnboardingPage />} />

      {/* ── Public content routes (no auth) ── */}
      <Route path="/library"           element={<LibraryPage />} />
      {/* /browser is an alias for /library — marketing/PageSpeed URL.
          Both routes render the same component so neither produces a
          404, and prerender-library.mjs writes static HTML for both. */}
      <Route path="/browser"           element={<LibraryPage />} />
      {/* Task #577 — Educational web browser (curated, reader-mode).
          /browse renders BrowsePage (simple reader + ask panel).
          /browser-tabs is the multi-tab BrowserPage for power users. */}
      <Route path="/browse"            element={<BrowsePage />} />
      <Route path="/browser-tabs"      element={<BrowserPage />} />
      <Route path="/curriculum"        element={<CurriculumMap />} />
      <Route path="/subject/:subjectId" element={<SubjectPage />} />

      {/* ── CMS Learn pages ── */}
      <Route path="/learn/:slug" element={<LearnPage />} />

      {/* ── Personalized CMS (private, paid) ── */}
      <Route path="/cms/:userId/:slug" element={<AuthGuard><PersonalizedCmsPage /></AuthGuard>} />

      {/* /subscribe → pricing */}
      <Route path="/subscribe" element={<PricingPage />} />

      {/* ── PYQ HTML Replica pages ── */}
      <Route path="/pyq/:slug" element={<PYQReplicaPage />} />

      {/* ── SEO routes: /{board}/{class}/{subject} and /{board}/{class}/{subject}/{chapter} ── */}
      <Route path="/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug" element={<ChapterPage />} />
      <Route path="/:board/:classSlug/:subjectSlug/:chapterSlug" element={<ChapterPage />} />
      {/* Task #914 Step 2 — topic deep-link URLs. Mounted BEFORE the
          legacy `/:pageType` redirect below so the literal `topic`
          segment doesn't get swallowed and re-routed. ChapterPage
          treats `topicSlug` as scroll-to-anchor + canonical override
          context; markup is identical to the chapter URL (no
          cloaking — same React tree, same DOM). */}
      <Route path="/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug/topic/:topicSlug" element={<ChapterPage />} />
      <Route path="/:board/:classSlug/:subjectSlug/:chapterSlug/topic/:topicSlug" element={<ChapterPage />} />

      {/* Task #295 — Assamese-language chapter routes. The /as/* prefix
          is the path-based hreflang="as-IN" target (replaces ?lang=as).
          ChapterPage detects the prefix, forces contentLang='as' for
          the duration of the page, and fetches via the dedicated
          /content/chapter-by-slug-as resolver which matches against
          chapters.slug_as (with English-slug fallback). */}
      <Route path="/as/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug" element={<ChapterPage />} />
      <Route path="/as/:board/:classSlug/:subjectSlug/:chapterSlug" element={<ChapterPage />} />
      <Route path="/as/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug/topic/:topicSlug" element={<ChapterPage />} />
      <Route path="/as/:board/:classSlug/:subjectSlug/:chapterSlug/topic/:topicSlug" element={<ChapterPage />} />
      <Route path="/:board/:classSlug/:subjectSlug/:chapterSlug/:pageType" element={<LegacyTopicRedirect />} />
      <Route path="/:board/:classSlug/:subjectSlug" element={<SubjectLandingPage />} />

      {/* ── Protected routes (require login) ── */}
      <Route path="/chat"              element={<ChatPage />} />
      <Route path="/read"              element={<AuthGuard><BrowsePage /></AuthGuard>} />
      <Route path="/history"           element={<HistoryPage />} />
      <Route path="/profile"           element={<AuthGuard><ProfilePage /></AuthGuard>} />
      <Route path="/profile/memories"  element={<AuthGuard><MyMemoriesPage /></AuthGuard>} />

      {/* ── Educational Browser Phase 3 — study tools ── */}
      <Route path="/notebook"          element={<AuthGuard><NotebookPage /></AuthGuard>} />
      <Route path="/flashcards"        element={<AuthGuard><FlashcardsPage /></AuthGuard>} />
      <Route path="/guardian"          element={<AuthGuard><GuardianPage /></AuthGuard>} />

      {/* ── Test-only harness for the Phase-3 study e2e suite (Task #594).
            Gated on `import.meta.env.DEV` so it ships only in the dev
            (Vite) bundle that Playwright targets — not in production
            builds. This keeps the test surface invisible to real
            users while still letting the e2e suite drive the
            highlight + quiz flow against a deterministic fixture. ── */}
      {import.meta.env.DEV && (
        <Route path="/__test/study-harness" element={<StudyTestHarnessPage />} />
      )}

      {/* ── Admin routes ── */}
      <Route path="/admin/login" element={<AdminLoginPage />} />
      <Route path="/admin"       element={<AdminGuard><AdminPage /></AdminGuard>} />

      {/* ── Staff routes ── */}
      <Route path="/staff" element={<StaffGuard><StaffDashboard /></StaffGuard>} />

      {/* ── 404 ── */}
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
    </>
  );
}

// ── Provider shell (no router, no client-only effects) ────────────────────
// Used by both the client (wrapped with BrowserRouter) and the SSR entry
// (wrapped with StaticRouter) so the prerendered DOM matches React's
// first client render. (Task #382)
export function AppShell({ children, ssr = false, helmetContext }) {
  // The four lazy presentational/effects components below are gated on a
  // post-hydration `mounted` flag — NOT on `ssr` — so the FIRST render
  // tree is identical on the server and on the client. Gating on `ssr`
  // alone created a structural fiber-tree mismatch (server: null,
  // client first render: <Suspense><Lazy /></Suspense>) which React 18
  // hydration reports as error #418 even though both branches emit no
  // DOM. After `useEffect` runs, `mounted` flips true and the deferred
  // Suspense subtrees mount client-only. (Tasks #382, #506)
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);
  const showDeferred = mounted && !ssr;
  return (
    <HelmetProvider context={helmetContext}>
      {showDeferred ? <Suspense fallback={null}><LazyGlobalSeo /></Suspense> : null}
      {showDeferred ? <Suspense fallback={null}><LazyGlobalTrustpilotJsonLd /></Suspense> : null}
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <LanguageProvider>
              {children}
              {showDeferred ? <Suspense fallback={null}><LazyToaster richColors position="top-center" closeButton /></Suspense> : null}
            </LanguageProvider>
          </AuthProvider>
          {showDeferred ? <Suspense fallback={null}><PWAInstallPrompt /></Suspense> : null}
          {showDeferred ? <Suspense fallback={null}><CookieConsent /></Suspense> : null}
          {showDeferred ? <Suspense fallback={null}><ReviewPrompt /></Suspense> : null}
        </QueryClientProvider>
      </ErrorBoundary>
    </HelmetProvider>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────
function App() {
  // Probe Cloudflare Image Resizing once on mount. If the plan add-on is not
  // active, probeImageResizer() calls markImageResizerUnavailable() so every
  // subsequent cdnImage() / cdnSrcSet() call silently returns the original URL
  // instead of a broken /cdn-cgi/image/ path. No-op when VITE_DISABLE_IMAGE_CDN=1.
  useEffect(() => { probeImageResizer('/logo-56.webp'); }, []); // eslint-disable-line

  useEffect(() => { prefetchCriticalRoutes(); }, []); // eslint-disable-line

  useEffect(() => {
    const prefetchBundle = () => {
      queryClient.prefetchQuery({
        queryKey: ['library-bundle'],
        queryFn: () => apiClient().get('/content/library-bundle').then((r) => r.data),
        staleTime: 30 * 60 * 1000,
      });
    };

    const isOnLibrary = window.location.pathname === '/library' || window.location.pathname === '/browser' || window.location.pathname.match(/^\/[a-z]+\/[a-z]/);
    if (isOnLibrary) {
      // Task #496: defer the full (non-slim) library-bundle prefetch to
      // idle so it doesn't compete with React hydration on the main
      // thread for /library and the prerendered subject + chapter
      // routes. The slim bundle is already inlined into the SSR HTML
      // (window.__LIBRARY_BUNDLE__ / __SSR_QUERIES__), so first render
      // doesn't need this full payload — it's only used for later
      // interactions (search across all subjects, filter chips, etc.).
      // Firing it immediately on mount was the dominant TBT contributor
      // on /library (3990 ms in the 2026-04-18 audit) because both
      // network parse + JSON-decode of the larger bundle landed on the
      // hydration critical path.
      const idle = window.requestIdleCallback || ((cb) => setTimeout(cb, 1));
      const handle = idle(() => prefetchBundle(), { timeout: 4000 });
      return () => {
        if (window.cancelIdleCallback) window.cancelIdleCallback(handle);
      };
    }

    let done = false;
    const onHoverLibrary = (e) => {
      if (!e.target || typeof e.target.closest !== 'function') return;
      const link = e.target.closest('a[href="/library"], a[href*="/library"]');
      if (link && !done) {
        done = true;
        prefetchBundle();
        detach();
      }
    };

    document.addEventListener('mouseenter', onHoverLibrary, { capture: true, passive: true });
    document.addEventListener('touchstart', onHoverLibrary, { capture: true, passive: true });

    const detach = () => {
      document.removeEventListener('mouseenter', onHoverLibrary, { capture: true });
      document.removeEventListener('touchstart', onHoverLibrary, { capture: true });
    };

    const fallback = setTimeout(() => {
      if (!done) { done = true; prefetchBundle(); detach(); }
    }, 4000);
    return () => { clearTimeout(fallback); detach(); };
  }, []);

  return (
    <AppShell>
      <BrowserRouter>
        <PageTracker />
        <Suspense fallback={<DeferredFallback />}>
          <AppRoutes />
        </Suspense>
      </BrowserRouter>
    </AppShell>
  );
}

export default App;
