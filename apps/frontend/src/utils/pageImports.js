export const pageImports = {
  library:   () => import("@/pages/LibraryPage"),
  chat:      () => import("@/pages/ChatPage"),
  history:   () => import("@/pages/HistoryPage"),
  profile:   () => import("@/pages/ProfilePage"),
  chapter:   () => import("@/pages/ChapterPage"),
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

    // library-bundle?slim=1 is the single request backing useBoards, useClasses,
    // useStreams and useSubjects on every page (navbar, breadcrumbs, subject pages).
    // Warm it everywhere so the backend has a hot response ready for the SW v17 cache
    // fill and for first-visit visitors. Delay 2 s so it never competes with hydration.
    schedule(() => warmApiCache(['/api/content/library-bundle?slim=1']), 2000);

    // Landing/chat: defer aggressively so prefetch never competes with hydration
    // (was: 200ms library + 800ms chapter — caused TBT 1.8s and TTI 14s on slow devices).
    if (path === '/' || path === '/chat') {
      schedule(() => pageImports.library(), 4000);
      // Drop chapter prefetch on landing — most landing visitors never reach a chapter,
      // and the chapter chunk transitively pulls the heavy markdown bundle (~458KB).
    } else if (path === '/library') {
      // Task #391: Don't pull non-library chunks on /library. ChatPage and
      // ChapterPage are eagerly imported in App.jsx for hydration safety
      // (Tasks #382/#385/#387) — re-prefetching them here is a no-op for
      // the chunk graph but the network priority hint pulls them sooner
      // than needed and shows up in Lighthouse's TBT trace.
      // library-bundle warm-up handled by the universal schedule() call above.
    } else if (path.match(/^\/[a-z]+\/[a-z]/)) {
      // Subject/chapter routes — library-bundle warm-up already scheduled above.
      // Skip pulling chat/chapter chunks; those routes already have their own
      // page chunk in flight from the route mount.
    } else {
      schedule(() => pageImports.chat(), 2500);
      schedule(() => pageImports.library(), 3500);
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
