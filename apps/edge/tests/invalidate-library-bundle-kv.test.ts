import { describe, expect, it, vi } from 'vitest';
import {
  assertMakerSubjectBilingual,
  invalidateLibraryBundleKeys,
  LIBRARY_BUNDLE_PREFIX,
  productionIsrNamespaceId,
  verifyPublicLibraryBundles,
} from '../scripts/invalidate-library-bundle-kv.mjs';

describe('production library-bundle KV invalidation', () => {
  it('selects only the production ISR cache namespace', () => {
    const toml = `
[[kv_namespaces]]
binding = "ISR_CACHE_KV"
id = "11111111111111111111111111111111"
[env.production]
name = "worker-prod"
[[env.production.kv_namespaces]]
binding = "RATE_LIMIT_KV"
id = "22222222222222222222222222222222"
[[env.production.kv_namespaces]]
binding = "ISR_CACHE_KV"
id = "33333333333333333333333333333333"
[[env.staging.kv_namespaces]]
binding = "ISR_CACHE_KV"
id = "44444444444444444444444444444444"
`;
    expect(productionIsrNamespaceId(toml)).toBe('33333333333333333333333333333333');
  });

  it('deletes only the requested prefix across every listing page', async () => {
    const prefix = LIBRARY_BUNDLE_PREFIX;
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(input.toString());
      if (url.pathname.endsWith('/keys')) {
        if (!url.searchParams.has('cursor')) {
          return Response.json({
            success: true,
            result: [{ name: `${prefix}?slim=1` }],
            result_info: { cursor: 'next-page' },
          });
        }
        return Response.json({
          success: true,
          result: [{ name: `${prefix}?boot=degree-id` }],
          result_info: {},
        });
      }
      expect(init.method).toBe('DELETE');
      return Response.json({ success: true, result: {} });
    });

    const deleted = await invalidateLibraryBundleKeys({
      accountId: 'account',
      namespaceId: 'namespace',
      token: 'test-token',
      fetchImpl: fetchImpl as typeof fetch,
    });

    expect(deleted).toBe(2);
    expect(fetchImpl).toHaveBeenCalledTimes(4);
    const deletePaths = fetchImpl.mock.calls
      .filter(([, init]) => init.method === 'DELETE')
      .map(([url]) => new URL(url.toString()).pathname);
    expect(deletePaths).toEqual([
      expect.stringContaining(encodeURIComponent(`${prefix}?slim=1`)),
      expect.stringContaining(encodeURIComponent(`${prefix}?boot=degree-id`)),
    ]);
  });

  it('fails closed if a list response includes a key outside the prefix', async () => {
    const fetchImpl = vi.fn(async () => Response.json({
      success: true,
      result: [{ name: 'unrelated:cache-key' }],
      result_info: {},
    }));

    await expect(invalidateLibraryBundleKeys({
      accountId: 'account',
      namespaceId: 'namespace',
      token: 'test-token',
      fetchImpl: fetchImpl as typeof fetch,
    })).rejects.toThrow('outside the library-bundle prefix');
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('fails the invalidation when Cloudflare refuses a delete', async () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init: RequestInit = {}) => {
      if (init.method === 'DELETE') {
        return Response.json({
          success: false,
          errors: [{ message: 'permission denied' }],
        }, { status: 403 });
      }
      return Response.json({
        success: true,
        result: [{ name: `${LIBRARY_BUNDLE_PREFIX}?slim=1` }],
        result_info: {},
      });
    });

    await expect(invalidateLibraryBundleKeys({
      accountId: 'account',
      namespaceId: 'namespace',
      token: 'test-token',
      fetchImpl: fetchImpl as typeof fetch,
    })).rejects.toThrow('Cloudflare KV request failed (HTTP 403): permission denied.');
  });

  it('rejects cards without Assamese name or description fields', () => {
    const cards = Array.from({ length: 3 }, (_, index) => ({
      slug: 'makers-of-modern-assam-mdc-01013',
      name_as: index === 0 ? '' : 'অসমীয়া নাম',
      description_as: 'অসমীয়া বিৱৰণ',
    }));
    expect(() => assertMakerSubjectBilingual({ subjects: cards }, 'test')).toThrow('missing Assamese');
  });

  it('verifies full, slim, and Degree boot variants with normal cache keys', async () => {
    const calls: string[] = [];
    const cards = Array.from({ length: 3 }, () => ({
      slug: 'makers-of-modern-assam-mdc-01013',
      name_as: 'আধুনিক অসমৰ নিৰ্মাতা সকল',
      description_as: 'অসমীয়া পাঠ্যক্ৰমৰ বিৱৰণ',
    }));
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(input.toString());
      calls.push(`${url.pathname}${url.search}`);
      return Response.json({
        subjects: cards,
        boards: [{ id: 'degree-board', name: 'Degree' }],
      });
    });

    await verifyPublicLibraryBundles({ baseUrl: 'https://syrabit.ai', fetchImpl: fetchImpl as typeof fetch });

    expect(calls).toEqual([
      '/api/v1/content/library-bundle',
      '/api/v1/content/library-bundle?slim=1',
      '/api/v1/content/library-bundle?boot=degree-board',
    ]);
  });
});
