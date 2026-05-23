import { describe, it, expect } from 'vitest';
import { parseAdminEduDeepLink } from './AdminEduBrowser';

// Task #625 — the Slack appeal-spike alert posts a link of shape
//   <admin-url>?tab=requested&domain=<encoded-domain>
// and the admin UI must parse those params on mount so the admin
// lands on the Site requests tab filtered to the spiking domain.

describe('parseAdminEduDeepLink', () => {
  it('pulls tab=requested and domain out of the query string', () => {
    const out = parseAdminEduDeepLink('?tab=requested&domain=popular-edu.org');
    expect(out).toEqual({ tab: 'requested', domain: 'popular-edu.org' });
  });

  it('decodes percent-encoded domains (matches backend urllib.quote)', () => {
    const out = parseAdminEduDeepLink('?tab=requested&domain=foo%2Bbar.edu');
    expect(out.tab).toBe('requested');
    expect(out.domain).toBe('foo+bar.edu');
  });

  it('accepts allowlist and blocked tabs but rejects unknown values', () => {
    expect(parseAdminEduDeepLink('?tab=allowlist').tab).toBe('allowlist');
    expect(parseAdminEduDeepLink('?tab=blocked').tab).toBe('blocked');
    // Unknown tab falls through to null so AdminEduBrowser keeps the
    // default 'allowlist' landing tab rather than switching to a
    // tab id it can't render.
    expect(parseAdminEduDeepLink('?tab=mystery').tab).toBe(null);
  });

  it('returns empty sentinels for missing / empty input', () => {
    expect(parseAdminEduDeepLink('')).toEqual({ tab: null, domain: '' });
    expect(parseAdminEduDeepLink(null)).toEqual({ tab: null, domain: '' });
    expect(parseAdminEduDeepLink(undefined)).toEqual({ tab: null, domain: '' });
  });

  it('clamps absurdly long domains so hostile URLs cannot bloat state', () => {
    const huge = 'a'.repeat(1000) + '.com';
    const out = parseAdminEduDeepLink(`?tab=requested&domain=${huge}`);
    expect(out.tab).toBe('requested');
    // RFC-compliant hostnames top out at 253 chars; we never let more
    // than that through into component state.
    expect(out.domain.length).toBeLessThanOrEqual(253);
  });
});
