/**
 * Deployment Audit Tests
 *
 * Verifies:
 * (a) Critical lucide-react icon imports resolve without errors
 * (b) Critical page components can mount without throwing
 *
 * These tests catch deployment failures caused by broken imports
 * or missing dependencies.
 */
import { describe, it, expect, vi, beforeAll } from 'vitest';

// Define build-time globals that Vite normally provides
globalThis.__TRUSTPILOT_BU_ID__ = '';

// Mock dependencies that components need
vi.mock('react-router-dom', () => ({
  Link: ({ children, ...props }) => <a {...props}>{children}</a>,
  useNavigate: () => vi.fn(),
  useLocation: () => ({ pathname: '/', search: '', hash: '' }),
  useParams: () => ({}),
  useSearchParams: () => [new URLSearchParams(), vi.fn()],
}));

vi.mock('@/utils/api', () => ({
  API_BASE: 'http://test.local/api',
  default: { get: vi.fn(), post: vi.fn() },
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

// ═══════════════════════════════════════════════════════════════
// (a) lucide-react icon imports
// ═══════════════════════════════════════════════════════════════

describe('lucide-react icon imports', () => {
  it('imports Sparkles without error', async () => {
    const mod = await import('lucide-react');
    expect(mod.Sparkles).toBeDefined();
  });

  it('documents that brand icons (GithubIcon, Twitter) were removed in v1.16.0', async () => {
    // lucide-react@1.16.0 removed all brand icons.
    // The project uses "GithubIcon as Github" which is the recommended
    // migration pattern even though it resolves to undefined at runtime.
    // This test documents the known limitation.
    const mod = await import('lucide-react');
    // Brand icons are NOT available in this version
    expect(mod.GithubIcon).toBeUndefined();
    expect(mod.Twitter).toBeUndefined();
  });

  it('imports ChevronRight without error', async () => {
    const mod = await import('lucide-react');
    expect(mod.ChevronRight).toBeDefined();
  });

  it('imports Globe without error', async () => {
    const mod = await import('lucide-react');
    expect(mod.Globe).toBeDefined();
  });

  it('imports Mail without error', async () => {
    const mod = await import('lucide-react');
    expect(mod.Mail).toBeDefined();
  });

  it('imports Search without error', async () => {
    const mod = await import('lucide-react');
    expect(mod.Search).toBeDefined();
  });

  it('imports ArrowLeft and ArrowRight without error', async () => {
    const mod = await import('lucide-react');
    expect(mod.ArrowLeft).toBeDefined();
    expect(mod.ArrowRight).toBeDefined();
  });

  it('imports Loader2 without error', async () => {
    const mod = await import('lucide-react');
    expect(mod.Loader2).toBeDefined();
  });

  it('imports AlertTriangle without error', async () => {
    const mod = await import('lucide-react');
    expect(mod.AlertTriangle).toBeDefined();
  });

  it('imports all non-brand icons used in BrowserPage', async () => {
    const {
      ArrowLeft, ArrowRight, RotateCw, X, Plus, Star, Search, Globe,
      Sparkles, BookmarkPlus, Clock, ShieldAlert, ExternalLink,
      PanelRightClose, PanelRightOpen, Menu, Loader2, Languages,
      StickyNote, Square, HelpCircle, GraduationCap, CheckCircle2,
      AlertTriangle,
    } = await import('lucide-react');

    expect(ArrowLeft).toBeDefined();
    expect(ArrowRight).toBeDefined();
    expect(RotateCw).toBeDefined();
    expect(X).toBeDefined();
    expect(Plus).toBeDefined();
    expect(Star).toBeDefined();
    expect(Search).toBeDefined();
    expect(Globe).toBeDefined();
    expect(Sparkles).toBeDefined();
    expect(BookmarkPlus).toBeDefined();
    expect(Clock).toBeDefined();
    expect(ShieldAlert).toBeDefined();
    expect(ExternalLink).toBeDefined();
    expect(PanelRightClose).toBeDefined();
    expect(PanelRightOpen).toBeDefined();
    expect(Menu).toBeDefined();
    expect(Loader2).toBeDefined();
    expect(Languages).toBeDefined();
    expect(StickyNote).toBeDefined();
    expect(Square).toBeDefined();
    expect(HelpCircle).toBeDefined();
    expect(GraduationCap).toBeDefined();
    expect(CheckCircle2).toBeDefined();
    expect(AlertTriangle).toBeDefined();
  });

  it('imports core UI icons used across the project', async () => {
    const mod = await import('lucide-react');
    const coreIcons = [
      'Sparkles', 'ChevronRight', 'Mail', 'Globe', 'Search',
      'ArrowLeft', 'ArrowRight', 'Loader2', 'AlertTriangle',
      'ExternalLink', 'Menu', 'X', 'Plus', 'Star',
    ];
    for (const icon of coreIcons) {
      expect(mod[icon]).toBeDefined();
    }
  });
});

// ═══════════════════════════════════════════════════════════════
// (b) Critical page components render without crashing
// ═══════════════════════════════════════════════════════════════

describe('Critical page component imports', () => {
  it('TestimonialsFooter module can be imported', async () => {
    // Just verify the module loads without throwing (with global defined)
    const mod = await import('@/pages/landing/TestimonialsFooter');
    expect(mod).toBeDefined();
    expect(mod.default).toBeDefined();
  });

  it('TestimonialsFooter renders successfully with inline SVG brand icons', async () => {
    // TestimonialsFooter previously used `GithubIcon as Github` and `Twitter`
    // from lucide-react, but brand icons were removed in v1.16.0.
    // The component now uses inline SVG components, so it renders without error.
    const { renderToStaticMarkup } = await import('react-dom/server');
    const React = await import('react');
    const { default: TestimonialsFooter } = await import(
      '@/pages/landing/TestimonialsFooter'
    );

    // The render should succeed now that brand icons are inline SVGs
    const html = renderToStaticMarkup(React.createElement(TestimonialsFooter));
    expect(html).toContain('Twitter');
    expect(html).toContain('GitHub');
  });
});
