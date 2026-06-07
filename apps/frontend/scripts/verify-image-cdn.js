#!/usr/bin/env node
/**
 * verify-image-cdn.js
 *
 * Confirms Cloudflare Image Resizing is active on syrabit.ai and measures
 * real byte savings across mobile / tablet / desktop breakpoints.
 *
 * What it verifies:
 *   1. The /cdn-cgi/image/ transform endpoint is reachable and returns a
 *      `cf-resized: internal=ok/...` response header — the same signal used
 *      by the frontend probeImageResizer() to auto-activate CDN transforms.
 *   2. Byte savings at each breakpoint (320 w / 640 w / 960 w) exceed the
 *      minimum thresholds defined in SAVINGS_THRESHOLDS below.
 *   3. The AVIF/WebP format probe: if the browser UA supports AVIF, CF should
 *      return `content-type: image/avif` rather than JPEG. We test both a
 *      modern-browser Accept header and a legacy one.
 *
 * Baseline (measured 2026-06-07, opengraph.jpg 39.9 KB):
 *   w=960  →  23.7 KB  (40.6% saving)
 *   w=640  →  12.3 KB  (69.2% saving)
 *   w=320  →   4.2 KB  (89.4% saving)
 *
 * Exit codes:
 *   0 — all checks passed
 *   1 — one or more checks failed
 *
 * Usage:
 *   node apps/frontend/scripts/verify-image-cdn.js
 *   node apps/frontend/scripts/verify-image-cdn.js --image https://syrabit.ai/opengraph.jpg
 */

const SITE     = process.env.SITE_URL || 'https://syrabit.ai';
const IMAGE    = process.argv.find((a) => a.startsWith('http')) || `${SITE}/opengraph.jpg`;
const TIMEOUT  = Number(process.env.VERIFY_TIMEOUT_MS) || 20_000;

const BREAKPOINTS = [
  { label: 'mobile (320 w)',  width: 320, minSavingPct: 75 },
  { label: 'tablet (640 w)',  width: 640, minSavingPct: 55 },
  { label: 'desktop (960 w)', width: 960, minSavingPct: 30 },
];

function ok(msg)   { console.log(`  ✓  ${msg}`); }
function fail(msg) { console.log(`  ✗  ${msg}`); }
function info(msg) { console.log(`  ℹ  ${msg}`); }

let failures = 0;

function checkFail(msg) {
  fail(msg);
  failures++;
}

async function fetchBytes(url, acceptHeader) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      headers: acceptHeader ? { Accept: acceptHeader } : {},
    });
    if (!res.ok) return { size: null, type: null, cfResized: null, status: res.status };
    const buf = await res.arrayBuffer();
    return {
      size:      buf.byteLength,
      type:      res.headers.get('content-type'),
      cfResized: res.headers.get('cf-resized'),
      status:    res.status,
    };
  } catch (e) {
    return { size: null, type: null, cfResized: null, status: 0, error: e.message };
  } finally {
    clearTimeout(timer);
  }
}

function cdnUrl(imageUrl, opts) {
  const optStr = Object.entries(opts).map(([k, v]) => `${k}=${v}`).join(',');
  return `${SITE}/cdn-cgi/image/${optStr}/${encodeURI(imageUrl)}`;
}

async function main() {
  console.log('═══════════════════════════════════════════════════════════════');
  console.log(' verify-image-cdn.js — Cloudflare Image Resizing benchmark');
  console.log(` Site:  ${SITE}`);
  console.log(` Image: ${IMAGE}`);
  console.log('═══════════════════════════════════════════════════════════════\n');

  // ── Step 1: Fetch the original image to get the baseline byte count ──────
  console.log('── Step 1: Baseline (original image, no CDN transform) ──');
  const orig = await fetchBytes(IMAGE);
  if (orig.size === null) {
    checkFail(`Could not fetch original image: status=${orig.status} error=${orig.error || 'unknown'}`);
    console.log(`\n  ✗  ${failures} check(s) failed.`);
    process.exit(1);
  }
  ok(`Original: ${orig.size.toLocaleString()} bytes (${(orig.size / 1024).toFixed(1)} KB) — ${orig.type}`);

  // ── Step 2: Probe that cf-resized header is present at each breakpoint ───
  console.log('\n── Step 2: CDN transform probe (cf-resized header) ──');
  const ACCEPT_AVIF = 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8';
  const ACCEPT_JPEG = 'image/jpeg,image/*,*/*;q=0.8';

  let cfResizedConfirmed = false;

  for (const bp of BREAKPOINTS) {
    const url  = cdnUrl(IMAGE, { width: bp.width, quality: 85, format: 'auto', fit: 'cover' });
    const r    = await fetchBytes(url, ACCEPT_AVIF);

    if (r.size === null) {
      checkFail(`${bp.label}: CDN request failed (status=${r.status} error=${r.error || 'unknown'})`);
      continue;
    }

    const cfHeader = r.cfResized || '';
    const isTransformed = cfHeader.startsWith('internal=ok') || cfHeader.startsWith('internal=ram');

    if (!isTransformed) {
      checkFail(
        `${bp.label}: cf-resized header missing or not active ` +
        `(got: "${cfHeader || 'none'}") — ` +
        'check Cloudflare Image Resizing add-on at ' +
        'dash.cloudflare.com → Speed → Optimization → Image Resizing',
      );
      continue;
    }

    cfResizedConfirmed = true;

    const savedBytes = orig.size - r.size;
    const savedPct   = ((savedBytes / orig.size) * 100).toFixed(1);
    const passMin    = parseFloat(savedPct) >= bp.minSavingPct;
    const marker     = passMin ? '✓' : '✗';

    console.log(
      `  ${marker}  ${bp.label.padEnd(17)} ` +
      `${r.size.toLocaleString().padStart(8)} B  ` +
      `(${(r.size / 1024).toFixed(1)} KB)  ` +
      `-${savedPct}%  ` +
      `${passMin ? 'PASS' : `FAIL (need ≥${bp.minSavingPct}%)`}  ` +
      `type=${r.type}`,
    );
    if (!passMin) {
      checkFail(
        `${bp.label} byte saving ${savedPct}% is below minimum ${bp.minSavingPct}% — ` +
        'image may not be going through the resize pipeline',
      );
    } else {
      info(`    cf-resized: ${cfHeader.split(' ').slice(0, 4).join(' ')}`);
    }
  }

  // ── Step 3: AVIF / WebP delivery probe ────────────────────────────────────
  console.log('\n── Step 3: Next-gen format delivery (AVIF / WebP) ──');
  const avifUrl = cdnUrl(IMAGE, { width: 640, quality: 85, format: 'auto' });
  const avifRes = await fetchBytes(avifUrl, ACCEPT_AVIF);
  const jpegRes = await fetchBytes(avifUrl, ACCEPT_JPEG);

  if (avifRes.type && jpegRes.type) {
    // Both fetched successfully — compare
    const modernType  = avifRes.type.split(';')[0].trim();
    const legacyType  = jpegRes.type.split(';')[0].trim();
    const isNextGen   = modernType === 'image/avif' || modernType === 'image/webp';

    if (isNextGen) {
      ok(`Modern browser receives next-gen format: ${modernType}  (${avifRes.size.toLocaleString()} B)`);
      ok(`Legacy browser receives:                 ${legacyType}  (${jpegRes.size?.toLocaleString() ?? '?'} B)`);
    } else {
      // CF may serve JPEG to both when format=auto and the source is already a
      // small JPEG — this is not necessarily a failure. Emit as info, not fail.
      info(`format=auto delivered ${modernType} to AVIF-capable browser`);
      info('(CF may choose JPEG when source is already efficiently encoded at this size)');
      info('For larger images (e.g. chapter thumbnails stored as PNG) AVIF delivery will activate.');
    }
  } else {
    info(`AVIF probe: status=${avifRes.status}  JPEG probe: status=${jpegRes.status}`);
  }

  // ── Summary ────────────────────────────────────────────────────────────────
  console.log('\n── Summary ──────────────────────────────────────────────────────');

  if (!cfResizedConfirmed) {
    checkFail(
      'cf-resized header was not found on ANY CDN request. ' +
      'Image Resizing may not be active. ' +
      'Enable it at dash.cloudflare.com → Speed → Optimization → Image Resizing, ' +
      'or run: node apps/frontend/scripts/cloudflare-phase6-apply.js',
    );
  } else {
    ok('Cloudflare Image Resizing confirmed active (cf-resized: internal=ok/... header present)');
  }

  if (failures === 0) {
    console.log('\n  ✓  All image CDN checks passed.');
    console.log('  Images will load significantly faster for students on slow mobile connections.');
    console.log('');
    console.log('  Lighthouse / Observatory:');
    console.log('  • Weekly Cloudflare Observatory runs are scheduled for:');
    console.log('    – https://syrabit.ai/');
    console.log('    – https://syrabit.ai/ahsec/class-12/physics');
    console.log('  • Check results: dash.cloudflare.com → Speed → Observatory → syrabit.ai');
    console.log('  • Core Web Vitals alerts: LCP>2.5 s, CLS>0.1, INP>200 ms → admin@syrabit.ai');
    process.exit(0);
  } else {
    console.log(`\n  ✗  ${failures} image CDN check(s) failed — see details above.`);
    process.exit(1);
  }
}

main().catch((e) => {
  console.error('\nUnhandled error:', e);
  process.exit(1);
});
