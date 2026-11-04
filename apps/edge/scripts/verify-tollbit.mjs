import { pathToFileURL } from 'node:url';

// Read-only HTTP probes; no publisher key, signatures or purchase tokens.
// User-Agent simulation checks routing, not real crawler attribution.
export async function verifyTollbit(origin, fetchImpl = fetch) {
  const base = new URL(origin);
  if (base.protocol !== 'https:' || base.pathname !== '/' || base.search || base.hash || base.username || base.password) {
    throw new Error('Supply an HTTPS publisher origin, without credentials or a path.');
  }
  const results = [];
  async function probe(path, userAgent, expectedStatus, extraHeaders = {}) {
    const response = await fetchImpl(new URL(path, base), {
      headers: { 'User-Agent': userAgent, ...extraHeaders },
      redirect: 'manual',
      signal: AbortSignal.timeout(15000),
    });
    if (response.status !== expectedStatus) {
      await response.body?.cancel();
      throw new Error(`${userAgent} ${path}: expected ${expectedStatus}, received ${response.status}`);
    }
    if ([402, 403].includes(expectedStatus) && !response.headers.get('cache-control')?.includes('no-store')) {
      await response.body?.cancel();
      throw new Error(`Gate response is cacheable: ${path}`);
    }
    results.push({ path, userAgent, status: response.status, credentialVariant: Object.keys(extraHeaders) });
    return response;
  }
  const directory = await (await probe('/tollbit/subjects.json', 'OAI-SearchBot', 200)).json();
  if (!Array.isArray(directory.subjects) || !directory.subjects.length) throw new Error('Empty or malformed subject directory.');
  const canonical = directory.subjects.map((subject) => subject.url);
  if (new Set(canonical).size !== canonical.length) throw new Error('Duplicate canonical subjects.');
  const allPages = directory.subjects.flatMap((subject) => subject.chapter_pages ?? []);
  const candidates = [
    allPages.find((page) => page.language === 'en'),
    allPages.find((page) => page.language === 'as'),
    allPages.find((page) => new URL(page.url).pathname.includes('/degree/')),
    directory.subjects.find((subject) => subject.included_in_subject_sitemap),
  ].filter(Boolean);
  const pages = [...new Set(candidates.map((page) => page.url))];
  if (!allPages.length || !pages.length) throw new Error('No published pages available for verification.');
  for (const url of [...canonical, ...allPages.map((page) => page.url)]) {
    if (new URL(url).origin !== base.origin) throw new Error('Directory contains an unexpected origin.');
  }
  const sitemapResponses = await Promise.all([
    probe('/sitemap-subjects.xml', 'OAI-SearchBot', 200),
    probe('/sitemap-chapters.xml', 'OAI-SearchBot', 200),
  ]);
  const sitemapBodies = await Promise.all(sitemapResponses.map((r) => r.text()));
  const sitemapUrls = sitemapBodies.map((body) => {
    if (!body.includes('<urlset')) throw new Error('Sitemap is not a URL set.');
    return new Set([...body.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1].replaceAll('&amp;', '&')));
  });
  const eligible = new Set(directory.subjects.filter((s) => s.included_in_subject_sitemap).map((s) => s.url));
  const chapterUrls = new Set(allPages.map((p) => p.url));
  for (const [expected, actual] of [[eligible, sitemapUrls[0]], [chapterUrls, sitemapUrls[1]]]) {
    if (expected.size !== actual.size || [...expected].some((url) => !actual.has(url))) {
      throw new Error('Subject directory and sitemap URLs disagree.');
    }
  }
  const manifestResponse = await probe('/ai.txt', 'OAI-SearchBot', 200);
  const manifest = await manifestResponse.text();
  if (!manifestResponse.headers.get('content-type')?.includes('text/plain') || manifest.includes('<html') || !manifest.includes('[disallow]')) {
    throw new Error('ai.txt must be a plain-text policy manifest.');
  }
  const llmsResponse = await probe('/llms.txt', 'OAI-SearchBot', 200);
  if (!(await llmsResponse.text()).includes('/tollbit/subjects.json')) throw new Error('LLM index omits subject directory.');
  for (const url of pages) {
    const path = new URL(url).pathname;
    await (await probe(path, 'Mozilla/5.0', 200)).body?.cancel();
    for (const ua of ['ChatGPT-User', 'OAI-SearchBot', 'PerplexityBot', 'Claude-SearchBot']) {
      const response = await probe(path, ua, 402);
      if (!(await response.text()).toLowerCase().includes('tollbit')) throw new Error('Expected Tollbit token-required response.');
    }
    await (await probe(path, 'ChatGPT-User', 402, { Cookie: 'audit_probe=1' })).body?.cancel();
    for (const ua of ['GPTBot', 'ClaudeBot', 'CCBot']) {
      await (await probe(path, ua, 403)).body?.cancel();
    }
  }
  await (await probe('/', 'ChatGPT-User', 402)).body?.cancel();
  return {
    results,
    subjects: canonical.length,
    chapterPages: chapterUrls.size,
    coverage: {
      english: allPages.some((p) => p.language === 'en'),
      assamese: allPages.some((p) => p.language === 'as'),
      degree: allPages.some((p) => new URL(p.url).pathname.includes('/degree/')),
    },
    providerOnboardingVerified: false,
    licensedDeliveryVerified: false,
    dashboardIngestionVerified: false,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (!process.argv[2]) throw new Error('Usage: node verify-tollbit.mjs https://publisher.example');
    console.log(JSON.stringify(await verifyTollbit(process.argv[2]), null, 2));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
