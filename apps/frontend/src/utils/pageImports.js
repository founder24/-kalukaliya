export const pageImports = {
  library: () => import("@/pages/LibraryPage"),
  chat: () => import("@/pages/ChatPage"),
  history: () => import("@/pages/HistoryPage"),
  profile: () => import("@/pages/ProfilePage"),
  chapter: () => import("@/pages/ChapterPage"),
};

function warmApiCache(paths) {
  const backendUrl = import.meta.env.VITE_BACKEND_URL || '';
  paths.forEach((p) => {
    fetch(backendUrl + p, { priority: 'low' }).catch(() => {});
  });
}

export function prefetchCriticalRoutes() {
  const schedule = (fn, delay) => {
    if (typeof requestIdleCallback === 'function') {
      setTimeout(() => requestIdleCallback(fn, { timeout: 3000 }), delay);
    } else {
      setTimeout(fn, delay);
    }
  };

  const afterInteractive = () => {
    const path = window.location.pathname;
    // Landing/chat: defer aggressively so prefetch never competes with hydration
    // (was: 200ms library + 800ms chapter — caused TBT 1.8s and TTI 14s on slow devices).
    if (path === '/' || path === '/chat') {
      schedule(() => pageImports.library(), 4000);
      schedule(() => warmApiCache(['/api/content/boards']), 2500);
      // Drop chapter prefetch on landing — most landing visitors never reach a chapter,
      // and the chapter chunk transitively pulls the heavy markdown bundle (~458KB).
    } else if (path === '/library') {
      // Task #391: Don't pull non-library chunks on /library. ChatPage and
      // ChapterPage are eagerly imported in App.jsx for hydration safety
      // (Tasks #382/#385/#387) — re-prefetching them here is a no-op for
      // the chunk graph but the network priority hint pulls them sooner
      // than needed and shows up in Lighthouse's TBT trace.
      // Warm the slim library bundle — the actual critical data path
      // for /library (LibraryPage uses useLibraryBundleSlim, not /boards).
      schedule(() => warmApiCache(['/api/content/library-bundle?slim=1']), 4000);
    } else if (path.match(/^\/[a-z]+\/[a-z]/)) {
      // Subject/chapter routes — warm boards (used by sidebar nav) but
      // skip pulling chat/chapter chunks; those routes already have
      // their own page chunk in flight from the route mount.
      schedule(() => warmApiCache(['/api/content/boards']), 4000);
    } else {
      schedule(() => pageImports.chat(), 2500);
      schedule(() => pageImports.library(), 3500);
      schedule(() => warmApiCache(['/api/content/boards']), 2000);
    }
  };

  // Wait for full load + an extra 1.5s so the main thread is idle before we
  // start downloading & parsing additional route chunks. This fixes Lighthouse
  // TTI/TBT regressions caused by eager prefetch on landing.
  if (document.readyState === 'complete') {
    setTimeout(afterInteractive, 1500);
  } else {
    window.addEventListener('load', () => setTimeout(afterInteractive, 1500), { once: true });
  }
}
