// Bumped to v15 (2026-04-24) to drop the 13-line PropellerAds-disabled
// header block that shipped dead bytes to every device on every SW update.
// The PropellerAds push integration was removed 2026-04-19; this is the
// final sweep of associated commentary. First-party Web Push handlers
// (`push`, `notificationclick`) below are unaffected.
const CACHE_VERSION = '16';
const STATIC_CACHE = 'syrabit-static-v' + CACHE_VERSION;
const RUNTIME_CACHE = 'syrabit-runtime-v' + CACHE_VERSION;
const API_CACHE = 'syrabit-api-v' + CACHE_VERSION;

const PRECACHE_URLS = [
  '/offline.html',
  '/manifest.json',
];

const CACHED_API_PATTERNS = [
  /^\/api\/content\/boards$/,
  /^\/api\/content\/classes/,
  /^\/api\/content\/streams/,
  /^\/api\/content\/subjects/,
  /^\/api\/content\/chapters/,
  /^\/api\/content\/topics/,
  /^\/api\/seo\//,
];

// Paths the SW MUST NOT serve from cache, even though they sit under
// `/api/seo/`. These flow through the Pages worker proxy which sets
// long edge TTLs already, and serving a stale sitemap from a service
// worker breaks crawler discovery (a sitemap from yesterday can hide
// today's new chapters from Googlebot until the SW's 1h TTL expires).
const SW_BYPASS_PATTERNS = [
  /^\/api\/seo\/sitemap[a-z0-9_-]*\.xml$/i,
  /^\/(sitemap[a-z0-9_-]*\.xml|sitemap-index\.xml|feed\.xml|rss\.xml)$/i,
  /^\/feed\/[a-z0-9_-]+\.xml$/i,
  /^\/llms(-full)?\.txt$/i,
  /^\/robots\.txt$/i,
  /^\/\.well-known\//i,
];

const API_CACHE_TTL = 3600 * 1000;
const RUNTIME_CACHE_MAX = 200;
const API_CACHE_MAX = 100;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) =>
      cache.addAll(PRECACHE_URLS).then(() =>
        fetch(new URL('precache-manifest.json', self.registration.scope).href)
          .then((res) => res.ok ? res.json() : [])
          .then((urls) => Promise.allSettled(
            urls.map((u) => cache.add(new Request(new URL(u, self.registration.scope).href)))
          ))
          .catch(() => {})
      )
    )
  );
  self.skipWaiting();
});

// Task #557 — drop a legacy push subscription that does NOT carry a full
// W3C `applicationServerKey` (i.e. the old Firebase/FCM-issued one) so the
// next page load triggers usePushNotifications -> pushManager.subscribe
// against the new VAPID public key. Idempotent: a fresh VAPID-shaped sub
// is left untouched.
async function dropLegacyFcmSubscription() {
  try {
    const sub = await self.registration.pushManager.getSubscription();
    if (!sub) return;
    const opts = (typeof sub.options === 'object' && sub.options) || {};
    const ask = opts.applicationServerKey;
    // FCM-vintage subs were created with no applicationServerKey at all
    // (gcm_sender_id flow). VAPID subs always have a non-empty
    // ArrayBuffer here. Treat anything falsy/zero-length as legacy.
    const isLegacy = !ask || (ask.byteLength === 0);
    if (isLegacy) {
      await sub.unsubscribe();
    }
  } catch (e) {
    // Non-fatal — the user can still re-subscribe manually from settings.
  }
}

self.addEventListener('activate', (event) => {
  const keep = new Set([STATIC_CACHE, RUNTIME_CACHE, API_CACHE]);
  event.waitUntil(
    Promise.all([
      caches.keys().then((keys) =>
        Promise.all(keys.filter((k) => !keep.has(k)).map((k) => caches.delete(k)))
      ),
      self.registration.navigationPreload && self.registration.navigationPreload.enable().catch(() => {}),
      dropLegacyFcmSubscription(),
    ])
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  if (url.origin !== self.location.origin) {
    if (url.hostname.includes('fonts.googleapis.com') || url.hostname.includes('fonts.gstatic.com')) {
      event.respondWith(cacheFirst(request, RUNTIME_CACHE));
      return;
    }
    return;
  }

  // Always bypass the SW for /admin* — admin pages must never serve a stale
  // cached HTML that references chunk hashes from a previous deploy. Letting
  // the browser hit the network directly guarantees fresh HTML + fresh chunks.
  // Symptom this prevents: "login succeeds, then bounced back / page doesn't
  // load" caused by SW returning yesterday's index.html with dead chunk URLs.
  if (url.pathname === '/admin' || url.pathname.startsWith('/admin/')) {
    return;
  }

  // Sitemaps, RSS/Atom feeds, llms.txt, robots.txt and /.well-known/* must
  // never be served from the SW cache — these are crawler-facing surfaces
  // where staleness directly hurts indexing. The Pages worker proxy already
  // sets a 1h edge TTL for them, which is enough.
  if (isSwBypass(url.pathname)) return;

  if (url.pathname.startsWith('/api/')) {
    if (isStreamingApi(url.pathname)) return;

    if (isCacheableApi(url.pathname)) {
      event.respondWith(apiStaleWhileRevalidate(request, url.pathname));
      return;
    }
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(navigationHandler(event));
    return;
  }

  if (isHashedAsset(url.pathname)) {
    event.respondWith(cacheFirst(request, STATIC_CACHE));
    return;
  }

  if (url.pathname.match(/\.(png|jpg|jpeg|gif|svg|ico|webp|avif|woff2?|ttf|eot)$/)) {
    event.respondWith(cacheFirst(request, RUNTIME_CACHE));
    return;
  }

  event.respondWith(staleWhileRevalidate(request));
});

function isHashedAsset(pathname) {
  return /\/assets\/[^/]+-[A-Za-z0-9_-]{8,}\.(js|css)$/.test(pathname);
}

function isStreamingApi(pathname) {
  return pathname.includes('/chat/stream') || pathname.includes('/ai/');
}

function isCacheableApi(pathname) {
  return CACHED_API_PATTERNS.some((p) => p.test(pathname));
}

function isSwBypass(pathname) {
  return SW_BYPASS_PATTERNS.some((p) => p.test(pathname));
}

async function navigationHandler(event) {
  const preloadResponse = event.preloadResponse
    ? await event.preloadResponse.catch(() => null)
    : null;

  if (preloadResponse && preloadResponse.ok) {
    const cache = await caches.open(RUNTIME_CACHE);
    cache.put(event.request, preloadResponse.clone());
    trimCache(RUNTIME_CACHE, RUNTIME_CACHE_MAX);
    return preloadResponse;
  }

  const cached = await caches.match(event.request);
  if (cached) {
    fetch(event.request).then((res) => {
      if (res && res.ok) {
        caches.open(RUNTIME_CACHE).then((c) => {
          c.put(event.request, res);
          trimCache(RUNTIME_CACHE, RUNTIME_CACHE_MAX);
        });
      }
    }).catch(() => {});
    return cached;
  }

  try {
    const response = await fetch(event.request);
    if (response.ok) {
      const cache = await caches.open(RUNTIME_CACHE);
      cache.put(event.request, response.clone());
      trimCache(RUNTIME_CACHE, RUNTIME_CACHE_MAX);
    }
    return response;
  } catch {
    return caches.match('/offline.html');
  }
}

function isJsonResponse(response) {
  const ct = response.headers.get('content-type') || '';
  return ct.includes('application/json');
}

async function apiStaleWhileRevalidate(request, pathname) {
  const cached = await caches.match(request);

  const fetchAndCache = fetch(request).then(async (response) => {
    if (response.ok && isJsonResponse(response)) {
      const cache = await caches.open(API_CACHE);
      const headers = new Headers(response.headers);
      headers.set('sw-cached-at', Date.now().toString());
      const cachedResponse = new Response(await response.clone().blob(), {
        status: response.status,
        statusText: response.statusText,
        headers,
      });
      cache.put(request, cachedResponse);
      trimCache(API_CACHE, API_CACHE_MAX);
    }
    return response;
  }).catch(() => null);

  if (cached) {
    const cachedAt = parseInt(cached.headers.get('sw-cached-at') || '0', 10);
    const age = Date.now() - cachedAt;
    if (age < API_CACHE_TTL) {
      fetchAndCache.catch(() => {});
      return cached;
    }
  }

  const networkResponse = await fetchAndCache;
  if (networkResponse) return networkResponse;

  if (cached) return cached;

  return new Response(JSON.stringify({ error: 'offline' }), {
    status: 503,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function staleWhileRevalidate(request) {
  const cached = await caches.match(request);
  const fetchPromise = fetch(request).then((response) => {
    if (response.ok) {
      caches.open(RUNTIME_CACHE).then((cache) => {
        cache.put(request, response.clone());
        trimCache(RUNTIME_CACHE, RUNTIME_CACHE_MAX);
      });
    }
    return response;
  }).catch(() => null);

  if (cached) {
    fetchPromise.catch(() => {});
    return cached;
  }

  const networkResponse = await fetchPromise;
  if (networkResponse) return networkResponse;

  if (request.mode === 'navigate') {
    return caches.match('/offline.html');
  }
  return new Response('', { status: 408 });
}

async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const target = cacheName || RUNTIME_CACHE;
      const cache = await caches.open(target);
      cache.put(request, response.clone());
      if (target === RUNTIME_CACHE) trimCache(RUNTIME_CACHE, RUNTIME_CACHE_MAX);
    }
    return response;
  } catch {
    return new Response('', { status: 408 });
  }
}

async function trimCache(cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length > maxEntries) {
    const toDelete = keys.slice(0, keys.length - maxEntries);
    await Promise.all(toDelete.map((k) => cache.delete(k)));
  }
}

self.addEventListener('push', (event) => {
  if (!event.data) return;
  let data;
  try {
    data = event.data.json();
  } catch {
    data = { title: 'Syrabit.ai', body: event.data.text() };
  }
  const isCritical = data.severity === 'critical' || data.tag?.startsWith('critical-alert');
  const options = {
    body: data.body || 'New notification',
    icon: data.icon || '/icons/icon-192x192.png',
    badge: '/icons/icon-72x72.png',
    tag: data.tag || 'syrabit-notification',
    data: { url: data.url || '/' },
    requireInteraction: isCritical,
    vibrate: isCritical ? [200, 100, 200, 100, 200] : [200, 100, 200],
  };
  event.waitUntil(
    self.registration.showNotification(data.title || 'Syrabit.ai', options)
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url.includes(url) && 'focus' in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(url);
      }
    })
  );
});

self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') {
    self.skipWaiting();
  }
  if (event.data === 'precacheApi') {
    event.waitUntil(precacheApiData());
  }
});

async function precacheApiData() {
  const apiUrls = [
    '/api/content/boards',
    '/api/content/subjects',
  ];
  const cache = await caches.open(API_CACHE);
  await Promise.allSettled(
    apiUrls.map(async (url) => {
      try {
        const response = await fetch(url);
        if (response.ok && isJsonResponse(response)) {
          const headers = new Headers(response.headers);
          headers.set('sw-cached-at', Date.now().toString());
          const cachedResponse = new Response(await response.blob(), {
            status: response.status,
            statusText: response.statusText,
            headers,
          });
          await cache.put(new Request(url), cachedResponse);
        }
      } catch {}
    })
  );
}
