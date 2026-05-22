/**
 * Task #296 — backwards-compat redirect map for AdminPage sidebar
 * consolidation. Asserts every legacy section id resolves to its new
 * merged parent + initial tab/sub-tab and preserves caller-supplied
 * navContext extras (panel, channel, etc.) used by deep-links.
 */
import { describe, it, expect } from 'vitest';
import { resolveSectionRedirect, SECTION_REDIRECTS } from './AdminPage';

describe('AdminPage SECTION_REDIRECTS', () => {
  it('passes through unknown / current sections unchanged', () => {
    expect(resolveSectionRedirect('dashboard')).toEqual({
      section: 'dashboard',
      navContext: null,
    });
    expect(resolveSectionRedirect('users', { search: 'foo' })).toEqual({
      section: 'users',
      navContext: { search: 'foo' },
    });
  });

  it('routes the AI & Automation legacy ids', () => {
    expect(resolveSectionRedirect('apiconfig')).toEqual({
      section: 'ai',
      navContext: { tab: 'providers', subTab: 'apiconfig' },
    });
    expect(resolveSectionRedirect('vertex')).toEqual({
      section: 'ai',
      navContext: { tab: 'providers', subTab: 'vertex' },
    });
    expect(resolveSectionRedirect('intelligence')).toEqual({
      section: 'ai',
      navContext: { tab: 'providers', subTab: 'intelligence' },
    });
    expect(resolveSectionRedirect('automation')).toEqual({
      section: 'ai',
      navContext: { tab: 'jobs' },
    });
  });

  it('routes the Revenue legacy ids', () => {
    expect(resolveSectionRedirect('monetization').section).toBe('revenue');
    expect(resolveSectionRedirect('monetization').navContext.tab).toBe('monetization');
    expect(resolveSectionRedirect('plans').navContext.tab).toBe('plans');
    expect(resolveSectionRedirect('ads').navContext.tab).toBe('ads');
  });

  it('routes the Access & Security legacy ids', () => {
    expect(resolveSectionRedirect('googleauth')).toEqual({
      section: 'security',
      navContext: { tab: 'auth' },
    });
    expect(resolveSectionRedirect('ratelimits').navContext.tab).toBe('ratelimits');
    expect(resolveSectionRedirect('botsecurity').navContext.tab).toBe('botsecurity');
    expect(resolveSectionRedirect('edubrowser').navContext.tab).toBe('edubrowser');
  });

  it('routes the Logs legacy ids', () => {
    // logsexplorer just lands on the unified Logs section with no extras.
    expect(resolveSectionRedirect('logsexplorer')).toEqual({
      section: 'logs',
      navContext: {},
    });
    // activitylog seeds the admin-actions pseudo-source so the explorer
    // swaps its body for the AdminActivityLog audit trail.
    expect(resolveSectionRedirect('activitylog')).toEqual({
      section: 'logs',
      navContext: { initialSources: ['admin-actions'] },
    });
  });

  it('routes feedback to the Conversations Feedback tab', () => {
    expect(resolveSectionRedirect('feedback')).toEqual({
      section: 'conversations',
      navContext: { tab: 'feedback' },
    });
  });

  it('preserves botsecurity deep-link extras (panel, channel)', () => {
    // AdminDashboard fires onNavigate('botsecurity', { panel: 'alert-settings', channel: 'push' })
    expect(resolveSectionRedirect('botsecurity', { panel: 'alert-settings', channel: 'push' })).toEqual({
      section: 'security',
      navContext: { tab: 'botsecurity', panel: 'alert-settings', channel: 'push' },
    });
    expect(resolveSectionRedirect('botsecurity', { panel: 'alert-cooldowns' })).toEqual({
      section: 'security',
      navContext: { tab: 'botsecurity', panel: 'alert-cooldowns' },
    });
  });

  it('lets caller-supplied tab override the default redirect tab', () => {
    const r = resolveSectionRedirect('apiconfig', { tab: 'jobs' });
    expect(r.section).toBe('ai');
    expect(r.navContext.tab).toBe('jobs');
  });

  it('handles the special blog → contenthub forwarder', () => {
    expect(resolveSectionRedirect('blog')).toEqual({
      section: 'contenthub',
      navContext: { initialTab: 'blog' },
    });
  });

  it('exports a SECTION_REDIRECTS map covering every retired section', () => {
    const retired = [
      'apiconfig', 'vertex', 'intelligence', 'automation',
      'monetization', 'plans', 'ads',
      'googleauth', 'ratelimits', 'botsecurity', 'edubrowser',
      'logsexplorer', 'activitylog',
      'feedback',
    ];
    for (const id of retired) {
      expect(SECTION_REDIRECTS, `expected ${id} to redirect`).toHaveProperty(id);
    }
  });
});
