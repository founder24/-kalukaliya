const prefetched = new Set();
const pending = new Map();
const apiWarmed = new Set();

const routeImports = {
  '/library': () => import('@/pages/LibraryPage'),
  '/chat': () => import('@/pages/ChatPage'),
  '/history': () => import('@/pages/HistoryPage'),
  '/profile': () => import('@/pages/ProfilePage'),
  '/pricing': () => import('@/pages/PricingPage'),
  '/curriculum': () => import('@/pages/CurriculumMap'),
  '/exam-routine': () => import('@/pages/ExamRoutinePage'),
  '/login': () => import('@/pages/LoginPage'),
  '/signup': () => import('@/pages/SignupPage'),
};

const apiWarmups = {
  '/library': '/api/content/library-bundle?slim=1',
};

function schedule(fn) {
  if (typeof window === 'undefined') return;
  if ('requestIdleCallback' in window) {
    window.requestIdleCallback(fn, { timeout: 2500 });
  } else {
    setTimeout(fn, 200);
  }
}

function warmApi(path) {
  const url = apiWarmups[path];
  if (!url || apiWarmed.has(url)) return;
  apiWarmed.add(url);
  try {
    fetch(url, { credentials: 'include', cache: 'force-cache', priority: 'low' }).catch(() => {});
  } catch (_) { /* noop */ }
}

function doPrefetch(path) {
  if (prefetched.has(path)) return;
  const loader = routeImports[path];
  if (!loader) return;
  loader()
    .then(() => prefetched.add(path))
    .catch(() => {});
  warmApi(path);
}

if (typeof window !== 'undefined') {
  if (document.readyState === 'complete') {
    schedule(() => doPrefetch('/library'));
  } else {
    window.addEventListener('load', () => schedule(() => doPrefetch('/library')), { once: true });
  }
}

export function prefetchRoute(path) {
  if (prefetched.has(path) || pending.has(path)) return;
  if (!routeImports[path]) return;
  const timer = setTimeout(() => {
    pending.delete(path);
    doPrefetch(path);
  }, 50);
  pending.set(path, timer);
}

function warmLibraryApi() {
  warmApi('/library');
}
