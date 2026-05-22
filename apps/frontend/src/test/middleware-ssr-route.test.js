// Task #386 — review-remediation unit tests for the Pages Functions
// SSR route mapper. Each frontend URL family exercised by the
// middleware MUST translate to a backend SSR endpoint that actually
// exists in seo_engine.py — these tests pin the mapping so a future
// change in either side breaks here loudly instead of silently
// regressing into SPA-only renders.
import { describe, it, expect } from 'vitest';
import { mapSsrRoute } from '../../functions/_middleware.js';

describe('mapSsrRoute (Task #386 SSR families)', () => {
  it('maps the homepage', () => {
    const r = mapSsrRoute('/');
    expect(r).toEqual({ backend: '/api/seo/html/homepage', family: 'homepage' });
  });

  it('maps the about page', () => {
    const r = mapSsrRoute('/about');
    expect(r).toEqual({ backend: '/api/seo/html/about', family: 'about' });
  });

  it('maps the subject family to /api/seo/html/subject/...', () => {
    const r = mapSsrRoute('/seba/class-12/physics');
    expect(r).toEqual({
      backend: '/api/seo/html/subject/seba/class-12/physics',
      family: 'subject',
    });
  });

  it('maps the topic family to the default notes endpoint', () => {
    const r = mapSsrRoute('/ahsec/class-12/physics/newton-laws');
    expect(r).toEqual({
      backend: '/api/seo/html/ahsec/class-12/physics/newton-laws',
      family: 'topic',
    });
  });

  it('maps a typed topic family (mcqs / pyq / qa / …)', () => {
    const r = mapSsrRoute('/cbse/class-10/maths/quadratic-equations/mcqs');
    expect(r).toEqual({
      backend: '/api/seo/html/cbse/class-10/maths/quadratic-equations/mcqs',
      family: 'topic_typed',
    });
  });

  it('maps the /pyq/<board>/<class>/<subject> shortcut to ?page_type=pyq', () => {
    const r = mapSsrRoute('/pyq/seba/class-10/maths');
    expect(r.family).toBe('pyq');
    expect(r.backend).toContain('/api/seo/html/subject/seba/class-10/maths');
    expect(r.backend).toContain('page_type=pyq');
  });

  it('maps /pyq/<year>/<paper> to its own SSR family', () => {
    const r = mapSsrRoute('/pyq/2024/major');
    expect(r).toEqual({
      backend: '/api/seo/html/pyq/2024/major',
      family: 'pyq_year_paper',
    });
  });

  it('rejects /pyq/<year>/<paper> with non-4-digit year (avoids false positives)', () => {
    expect(mapSsrRoute('/pyq/24/major')).toBeNull();
  });

  it('forwards Assamese (/as) prefix as ?lang=as', () => {
    const r = mapSsrRoute('/as/seba/class-12/physics');
    expect(r.family).toBe('subject');
    expect(r.backend).toContain('/api/seo/html/subject/seba/class-12/physics');
    expect(r.backend).toContain('lang=as');
  });

  it('forwards Assamese on a typed topic too', () => {
    const r = mapSsrRoute('/as/ahsec/class-12/physics/newton-laws/notes');
    expect(r.family).toBe('topic_typed');
    expect(r.backend).toContain('/api/seo/html/ahsec/class-12/physics/newton-laws/notes');
    expect(r.backend).toContain('lang=as');
  });

  it('maps slug-only topic family to /api/seo/html/topic/<slug>', () => {
    // Review remediation #2 — the middleware now resolves slug-only
    // families via the backend slug-resolver routes.
    expect(mapSsrRoute('/topic/newton-laws')).toEqual({
      backend: '/api/seo/html/topic/newton-laws',
      family: 'topic_slug',
    });
    expect(mapSsrRoute('/chapter/laws-of-motion')).toEqual({
      backend: '/api/seo/html/chapter/laws-of-motion',
      family: 'chapter_slug',
    });
    expect(mapSsrRoute('/subject/physics')).toEqual({
      backend: '/api/seo/html/subject/physics',
      family: 'subject_slug',
    });
  });

  it('forwards Assamese on slug-only families too', () => {
    const r = mapSsrRoute('/as/topic/newton-laws');
    expect(r.family).toBe('topic_slug');
    expect(r.backend).toContain('/api/seo/html/topic/newton-laws');
    expect(r.backend).toContain('lang=as');
  });

  it('does not map deeper /topic|/chapter|/subject paths (only 2-segment slug form)', () => {
    expect(mapSsrRoute('/topic/foo/bar')).toBeNull();
    expect(mapSsrRoute('/chapter')).toBeNull();
  });

  it('returns null for unknown boards (defensive — avoid 404 spam)', () => {
    expect(mapSsrRoute('/madeup-board/class-1/physics')).toBeNull();
  });

  it('returns null for app shells (chat / library / admin)', () => {
    expect(mapSsrRoute('/chat')).toBeNull();
    expect(mapSsrRoute('/library')).toBeNull();
    expect(mapSsrRoute('/admin/dashboard')).toBeNull();
  });

  it('rejects an unknown trailing page_type slug (avoids fake routes)', () => {
    expect(
      mapSsrRoute('/seba/class-12/physics/newton-laws/not-a-real-pagetype'),
    ).toBeNull();
  });

  it('maps the board-scoped chapter family /<board>/<class>/<subject>/chapter/<slug>', () => {
    const r = mapSsrRoute('/ahsec/class-12/physics/chapter/laws-of-motion');
    expect(r).toEqual({
      backend: '/api/seo/html/ahsec/class-12/physics/chapter/laws-of-motion',
      family: 'chapter',
    });
  });

  it('forwards Assamese on the board-scoped chapter family', () => {
    const r = mapSsrRoute('/as/ahsec/class-12/physics/chapter/laws-of-motion');
    expect(r.family).toBe('chapter');
    expect(r.backend).toContain('/api/seo/html/ahsec/class-12/physics/chapter/laws-of-motion');
    expect(r.backend).toContain('lang=as');
  });
});
