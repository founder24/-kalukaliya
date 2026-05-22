import React from "react";
import ReactDOM from "react-dom/client";
import "./index.css";
import App, { preloadPageForKind } from "./App";
import { initWebVitals } from "./utils/webVitals";
import Analytics from "./utils/analytics";

const rootEl = document.getElementById("root");
const tree = (
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// Prerendered routes (/library, /chat, /:board/:class/:subject,
// /:board/:class/:subject/:chapter) ship a fully prerendered React
// tree inside #root tagged with `data-hydrate="<kind>"`. Hydrate in
// place so React adopts the existing DOM — no remount, no flash.
// Every other route still mounts the SPA via createRoot.
// (Tasks #382, #385, #387, #395)
const PRERENDER_KINDS = new Set(["library", "chat", "subject", "chapter"]);
const hasPrerender =
  rootEl &&
  rootEl.firstElementChild != null &&
  PRERENDER_KINDS.has(rootEl.dataset.hydrate);

if (hasPrerender) {
  // Task #395: Each prerendered page is now its own JS chunk
  // (LibraryPage, ChatPage, SubjectLandingPage, ChapterPage). We MUST
  // wait for the matching chunk to load + prime React.lazy's payload
  // BEFORE invoking hydrateRoot — otherwise React renders a Suspense
  // fallback into #root, blowing away the SSR snapshot and producing
  // a hydration mismatch. The prerendered HTML emits a
  // `<link rel="modulepreload">` for the page chunk so it fetches in
  // parallel with this entry chunk; the await below is just the
  // join point.
  const kind = rootEl.dataset.hydrate;
  // Task #405: when the page-chunk preload import() rejects (chunk
  // 404 from a stale build, network blip, integrity mismatch), don't
  // swallow it. We still proceed to hydrateRoot — React.lazy will
  // fall back to its Suspense boundary, and DeferredFallback's
  // recovery hint will surface a refresh prompt after a few seconds.
  // We also report the failure so we can spot regressions in
  // production.
  function reportPreloadFailure(err) {
    try {
      const detail = {
        kind,
        path: window.location.pathname,
        message: err?.message || String(err),
        name: err?.name || "Error",
      };
      // Mark for the recovery hint + any e2e harness.
      window.__SYRABIT_HYDRATE_PRELOAD_FAILED__ = detail;
      try {
        window.dispatchEvent(
          new CustomEvent("syrabit:hydrate-preload-failed", { detail }),
        );
      } catch {}
      try { Analytics.hydratePreloadFailed?.(detail); } catch {}
      // Always log — preload failures are actionable for ops.
       
      console.warn("[hydrate] page-chunk preload failed", detail);
    } catch {
      /* never let reporting itself break hydration */
    }
  }

  // Task #407: a stale browser tab against a fresh deploy means the
  // cached HTML still references chunks whose hashes no longer exist
  // — the dynamic import 404s. The user shouldn't have to click
  // "Refresh"; we can detect that exact failure mode and reload once.
  // We GATE this on a sessionStorage flag so a genuinely broken new
  // build can never trap the tab in a reload loop, and we ONLY
  // auto-reload for failures that look like missing-chunk imports
  // (network blips, integrity errors, etc. fall through to the
  // existing recovery hint).
  const STALE_RELOAD_KEY = "syrabit_stale_chunk_reload";
  function looksLikeChunk404(err) {
    const msg = (err?.message || String(err || "")).toLowerCase();
    return (
      msg.includes("failed to fetch dynamically imported module") ||
      msg.includes("error loading dynamically imported module") ||
      msg.includes("importing a module script failed") ||
      msg.includes("unable to preload css") ||
      msg.includes("module script failed to load") ||
      // Safari surfaces this as a TypeError without a clear marker
      // when the chunk URL 404s; pair the error name with the path.
      (err?.name === "TypeError" && msg.includes("/assets/"))
    );
  }
  function tryAutoReload(err) {
    try {
      if (!looksLikeChunk404(err)) return false;
      const already = sessionStorage.getItem(STALE_RELOAD_KEY);
      if (already) return false; // loop guard — only reload once per session
      sessionStorage.setItem(STALE_RELOAD_KEY, String(Date.now()));
      try {
        Analytics.hydratePreloadFailed?.({
          kind,
          path: window.location.pathname,
          message: err?.message || String(err),
          name: err?.name || "Error",
          auto_reload: true,
        });
      } catch {}
       
      console.warn(
        "[hydrate] stale-chunk import detected — auto-reloading once",
        { kind, message: err?.message },
      );
      // Force-bypass the bfcache and any HTTP cache so the new HTML
      // (which references the new chunk hashes) is fetched.
      const url = new URL(window.location.href);
      url.searchParams.set("_r", Date.now().toString(36));
      window.location.replace(url.toString());
      return true;
    } catch {
      return false;
    }
  }

  Promise.resolve(preloadPageForKind(kind))
    .catch((err) => {
      if (tryAutoReload(err)) {
        // Reload in flight — return a never-settling promise so we
        // don't proceed to hydrateRoot in this doomed page instance.
        return new Promise(() => {});
      }
      reportPreloadFailure(err);
    })
    .then(() => {
      ReactDOM.hydrateRoot(rootEl, tree, {
        onRecoverableError: (error, info) => {
          try {
            window.__HYDRATE_REC__ = window.__HYDRATE_REC__ || [];
            window.__HYDRATE_REC__.push({
              message: String((error && error.message) || error),
              stack: String((error && error.stack) || '').slice(0, 4000),
              componentStack: String((info && info.componentStack) || '').slice(0, 4000),
            });
          } catch {}
        },
      });
      if (typeof window !== "undefined") {
        window.__SYRABIT_HYDRATED__ = true;
        // If this hydration is the result of a Task #407 auto-reload,
        // emit a `hydrate_recovered` event BEFORE clearing the flag
        // so we can measure auto-reload success rate (recoveries /
        // attempts) and spot false positives.
        try {
          const reloadAt = sessionStorage.getItem(STALE_RELOAD_KEY);
          if (reloadAt) {
            try {
              Analytics.hydrateRecovered?.({
                kind,
                path: window.location.pathname,
                reload_at: Number(reloadAt) || null,
                ms_since_reload: Date.now() - (Number(reloadAt) || Date.now()),
              });
            } catch {}
          }
        } catch {}
        // Now clear the loop-guard flag so the NEXT stale-chunk hit
        // (after a future deploy) is allowed its one auto-reload.
        try { sessionStorage.removeItem(STALE_RELOAD_KEY); } catch {}
      }
    });
} else {
  ReactDOM.createRoot(rootEl).render(tree);
}

// Remove the pre-hydration shell once React has painted its first frame.
// This only runs for routes that did not get a real prerendered tree
// (i.e. everything except /library on the static build).
function removeShell() {
  const shell = document.getElementById("__shell");
  if (shell && shell.parentNode) shell.parentNode.removeChild(shell);
}

if (!hasPrerender) {
  requestAnimationFrame(() => {
    requestAnimationFrame(removeShell);
  });
} else {
  // Belt & braces: a stale build of /library could still have a shell
  // sibling around #root — drop it on the next frame.
  requestAnimationFrame(removeShell);
}


if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    // Task #498: capture controller existence BEFORE the registration
    // resolves. PageSpeed traces showed every audited route losing
    // 3.6–7.5s (simulated) to a "redirect chain" that was actually a
    // duplicate same-URL Document fetch fired ~850ms after the initial
    // navigation. Root cause: on FIRST visit the SW's `activate` handler
    // calls `clients.claim()`, which triggers `controllerchange` on the
    // currently-controlled-by-nobody page; the listener below then ran
    // `window.location.reload()` and Lighthouse recorded the reload as
    // a redirect of `/<route>` → `/<route>` worth ~5s of simulated
    // savings. The reload is only meaningful when an UPDATED SW takes
    // over from a previously-controlling SW (so the user sees the new
    // assets immediately). On first install there is nothing to refresh
    // — the page already loaded with the latest HTML.
    const hadInitialController = !!navigator.serviceWorker.controller;

    navigator.serviceWorker
      .register("/sw.js", { updateViaCache: "none" })
      .then((reg) => {
        reg.update();
        setInterval(() => reg.update(), 60 * 60 * 1000);

        if (navigator.serviceWorker.controller) {
          navigator.serviceWorker.controller.postMessage("precacheApi");
        }

        reg.addEventListener("updatefound", () => {
          const worker = reg.installing;
          if (worker) {
            worker.addEventListener("statechange", () => {
              if (worker.state === "installed" && navigator.serviceWorker.controller) {
                worker.postMessage("skipWaiting");
              }
              if (worker.state === "activated") {
                worker.postMessage("precacheApi");
              }
            });
          }
        });
      })
      .catch(() => {});

    let refreshing = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (refreshing) return;
      // Skip the first-install controllerchange (null → SW). Reloading
      // there fires a duplicate Document fetch that Lighthouse counts
      // as a multi-second redirect chain on every cold visit.
      if (!hadInitialController) return;
      refreshing = true;
      window.location.reload();
    });
  });
} else if ("serviceWorker" in navigator && !import.meta.env.PROD) {
  navigator.serviceWorker.getRegistrations().then((regs) => {
    regs.forEach((r) => r.unregister());
  });
}

initWebVitals();
