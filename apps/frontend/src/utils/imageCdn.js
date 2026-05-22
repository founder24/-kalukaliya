/**
 * Image CDN helper — rewrites raw thumbnail URLs into Cloudflare's
 * `/cdn-cgi/image/<options>/<source>` transformation endpoint so we get
 *   • format=auto  → AVIF / WebP delivery to browsers that support them
 *   • width=N      → properly-sized responses (saves 60–80% bytes on mobile)
 *   • quality=85   → near-lossless visual quality at a fraction of the bytes
 *
 * Requires Cloudflare Image Resizing (Pages Pro or Cloudflare Images add-on)
 * to be enabled on the zone. If it is not, set VITE_DISABLE_IMAGE_CDN=1 and
 * URLs will pass through unchanged.
 *
 * Plan-gated fallback: if Image Resizing is not active on the zone, calling
 * markImageResizerUnavailable() (or probeImageResizer()) silently falls back to
 * the original URL in production.  In development a console warning is logged.
 * No code changes are needed once the plan upgrade is purchased — the probe will
 * detect the Cf-Bgj: imgconvert response header and stop falling back automatically.
 *
 * Task #383.
 */

const DISABLED =
  typeof import.meta !== 'undefined' &&
  import.meta.env &&
  import.meta.env.VITE_DISABLE_IMAGE_CDN === '1';

const IS_DEV =
  typeof import.meta !== 'undefined' &&
  import.meta.env &&
  import.meta.env.DEV === true;

const DEFAULT_WIDTHS = [320, 640, 960];
const DEFAULT_OPTS = { quality: 85, format: 'auto', fit: 'cover' };

// Module-level flag: set to true when Image Resizing is detected as unavailable.
// Falls back to original URLs until the plan upgrade is purchased and activated.
let _planGated = false;

/**
 * Mark Cloudflare Image Resizing as unavailable for this session.
 * - Development: logs a console warning once with the upgrade URL.
 * - Production: silently falls back to original URLs.
 * - Idempotent: safe to call multiple times.
 *
 * Called automatically by probeImageResizer() or manually from an img onError
 * handler if the /cdn-cgi/image/ URL returns a non-transformed response.
 */
export function markImageResizerUnavailable() {
  if (_planGated) return;
  _planGated = true;
  if (IS_DEV) {
    console.warn(
      '[imageCdn] Cloudflare Image Resizing is not active on this zone. ' +
      'Image URLs will fall back to original sources (no /cdn-cgi/image/ transform). ' +
      'Enable the add-on at: https://dash.cloudflare.com → Speed → Optimization → Image Resizing. ' +
      'Once enabled, re-run probeImageResizer() or reload the page to re-activate CDN transforms.',
    );
  }
}

/**
 * Returns true when Image Resizing is active and CDN URLs will be generated.
 * Returns false when DISABLED or when markImageResizerUnavailable() was called.
 */
export function isImageResizerAvailable() {
  return !DISABLED && !_planGated;
}

/**
 * Probe whether Cloudflare Image Resizing is active by fetching a small
 * /cdn-cgi/image/ URL and checking the Cf-Bgj: imgconvert response header.
 *
 * Call once at app startup (e.g. in App.jsx useEffect) to auto-detect plan
 * availability.  If Image Resizing is not active, falls back to original URLs
 * silently in production (or with a console warning in development).
 *
 * No-op when VITE_DISABLE_IMAGE_CDN=1, when already marked unavailable, or
 * when testImageUrl is not provided.
 *
 * @param {string} testImageUrl  A publicly accessible image URL to probe through
 *                               /cdn-cgi/image/ — typically a small placeholder
 *                               image already served by the site.
 */
export async function probeImageResizer(testImageUrl) {
  if (DISABLED || _planGated || !testImageUrl) return;
  try {
    const cdnUrl = cdnImage(testImageUrl, { width: 1, quality: 1, format: 'webp' });
    if (cdnUrl === testImageUrl) return;
    const res = await fetch(cdnUrl, { method: 'HEAD', cache: 'no-store' });
    const transformed = res.headers.get('Cf-Bgj') === 'imgconvert';
    if (!transformed) {
      markImageResizerUnavailable();
    }
  } catch {
    // Network error — leave state unchanged; don't mark unavailable on transient errors
  }
}

function isPassThrough(src) {
  if (!src || typeof src !== 'string') return true;
  if (src.startsWith('data:')) return true;
  if (src.startsWith('blob:')) return true;
  // Already routed through CF transformations — don't double-wrap.
  if (src.includes('/cdn-cgi/image/')) return true;
  return false;
}

/**
 * Rewrite `src` into a Cloudflare image-transform URL at the given width.
 * Returns the input unchanged when:
 *   - VITE_DISABLE_IMAGE_CDN=1 is set
 *   - Image Resizing is not available (plan-gated; returns original URL gracefully)
 *   - The input is a data/blob URI or already-transformed URL
 */
export function cdnImage(src, { width, quality, format, fit } = {}) {
  if (DISABLED || _planGated || isPassThrough(src)) return src;
  const opts = {
    ...DEFAULT_OPTS,
    ...(width != null ? { width } : null),
    ...(quality != null ? { quality } : null),
    ...(format != null ? { format } : null),
    ...(fit != null ? { fit } : null),
  };
  const optString = Object.entries(opts)
    .map(([k, v]) => `${k}=${v}`)
    .join(',');
  // encodeURI preserves the URL structure (protocol slashes, query separator)
  // while escaping whitespace and other characters that would otherwise
  // produce a malformed CDN URL on edge cases (e.g. spaces in CMS-uploaded
  // filenames).
  return `/cdn-cgi/image/${optString}/${encodeURI(src)}`;
}

/**
 * Build a `srcset` string for responsive `<img>` rendering.
 * Returns `undefined` (so React drops the attribute) when the helper is
 * disabled, plan-gated, or the source is unsuitable.
 */
export function cdnSrcSet(src, widths = DEFAULT_WIDTHS, opts = {}) {
  if (DISABLED || _planGated || isPassThrough(src)) return undefined;
  return widths
    .map((w) => `${cdnImage(src, { ...opts, width: w })} ${w}w`)
    .join(', ');
}
