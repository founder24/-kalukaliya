import { describe, expect, it } from 'vitest';
import { verifyTollbit } from '../scripts/verify-tollbit.mjs';

const origin = 'https://publisher.example';
const page = `${origin}/ahsec/year/subject/chapter`;
const subject = `${origin}/ahsec/year/subject`;

function mockFetch(fault = '') {
  return async (input: URL, init: RequestInit) => {
    const ua = new Headers(init.headers).get('User-Agent') || '';
    const path = input.pathname;
    const headers = { 'Cache-Control': 'no-store' };
    if (path === '/tollbit/subjects.json') {
      return Response.json({ subjects: [{
        url: subject, included_in_subject_sitemap: true,
        chapter_pages: [{ url: page, language: 'en' }],
      }] });
    }
    if (path.startsWith('/sitemap')) {
      return new Response(`<urlset><url><loc>${path.includes('subjects') ? subject : page}</loc></url></urlset>`);
    }
    if (path === '/ai.txt') return new Response('[disallow]\nGPTBot', {
      status: fault === 'manifest' ? 404 : 200,
      headers: { 'Content-Type': 'text/plain' },
    });
    if (path === '/llms.txt') return new Response('/tollbit/subjects.json');
    if (ua === 'Mozilla/5.0' || (fault === 'cookie' && new Headers(init.headers).has('Cookie'))) return new Response('origin');
    if (fault === 'perplexity' && ua === 'PerplexityBot') return new Response('origin');
    if (['GPTBot', 'ClaudeBot', 'CCBot'].includes(ua)) return new Response('denied', { status: 403, headers });
    return new Response('Tollbit token required', { status: 402, headers });
  };
}

describe('read-only Tollbit live contract checker', () => {
  it('checks synthetic routes without claiming real provider confirmation', async () => {
    const result = await verifyTollbit(origin, mockFetch());
    expect(result.subjects).toBe(1);
    expect(result.results.length).toBeGreaterThan(12);
    expect(result.providerOnboardingVerified).toBe(false);
    expect(result.licensedDeliveryVerified).toBe(false);
    expect(result.dashboardIngestionVerified).toBe(false);
    expect(result.coverage.assamese).toBe(false);
  });
  it.each(['cookie', 'perplexity', 'manifest'])('fails when %s regression appears', async (fault) => {
    await expect(verifyTollbit(origin, mockFetch(fault))).rejects.toThrow();
  });
  it('rejects credentials and non-HTTPS URLs', async () => {
    await expect(verifyTollbit('https://name:password@publisher.example')).rejects.toThrow('origin');
    await expect(verifyTollbit('http://publisher.example')).rejects.toThrow('HTTPS');
  });
});
