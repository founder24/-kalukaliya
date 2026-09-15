import { describe, expect, it } from 'vitest';
import { matchRoutes } from 'react-router-dom';
import { ASSAMESE_SEO_ROUTE_PATHS } from './App';

const routes = [
  { path: ASSAMESE_SEO_ROUTE_PATHS.subject, id: 'assamese-subject' },
  { path: ASSAMESE_SEO_ROUTE_PATHS.chapterWithStream, id: 'assamese-chapter-stream' },
  { path: ASSAMESE_SEO_ROUTE_PATHS.chapter, id: 'assamese-chapter' },
  { path: ASSAMESE_SEO_ROUTE_PATHS.topicWithStream, id: 'assamese-topic-stream' },
  { path: ASSAMESE_SEO_ROUTE_PATHS.topic, id: 'assamese-topic' },
  { path: '/:board/:classSlug/:streamSlug/:subjectSlug/:chapterSlug', id: 'generic-chapter-stream' },
  { path: '/:board/:classSlug/:subjectSlug/:chapterSlug', id: 'generic-chapter' },
  { path: '/:board/:classSlug/:subjectSlug', id: 'generic-subject' },
];

describe('Assamese SEO route precedence', () => {
  it.each([
    ['/as/ahsec/class-12/physics', 'assamese-subject'],
    ['/as/ahsec/class-12/physics/motion', 'assamese-chapter'],
    ['/as/ahsec/class-12/science/physics/motion', 'assamese-chapter-stream'],
    ['/as/ahsec/class-12/physics/motion/topic/velocity', 'assamese-topic'],
  ])('routes %s to the Assamese family before generic wildcards', (pathname, id) => {
    expect(matchRoutes(routes, pathname)?.[0]?.route.id).toBe(id);
  });
});