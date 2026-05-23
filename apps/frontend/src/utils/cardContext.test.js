/**
 * Task #409 — pin the chat-page card_context builder.
 *
 * Two contracts to lock:
 *
 *   1. The legacy "subject + chapters" path used by the SubjectCard /
 *      SubjectPage / SubjectLandingPage / ChapterPage Ask-AI buttons
 *      must produce the same output the inline ChatPage useMemo did
 *      before the refactor — same `Subject:` / `Active chapter
 *      (priority context):` / `Syllabus chapters:` markers the
 *      backend's rag.py library branch keys off.
 *
 *   2. The new `seedContext` path used by PersonalizedCmsPage's
 *      "Ask AI about this plan" button must:
 *        - return the seed when no subject is loaded yet,
 *        - prepend the seed above the subject summary when a subject
 *          IS loaded (so the seed outranks the syllabus),
 *        - never lose the rag.py-priority markers,
 *        - respect the 4_000-char cap.
 */
import { describe, it, expect } from 'vitest';
import { buildCardContext, buildPlanSeedContext } from './cardContext';

const SUBJECT = {
  name: 'Political Science 2nd Sem NEP',
  description: 'Comparative governance, political theory and Indian polity.',
  tags: ['Polity', 'Theory', 'Governance'],
};
const USER = { board_name: 'AHSEC', class_name: 'Class 12', stream_name: 'Arts' };
const CHAPTERS = [
  { id: 'c1', title: 'State and Government', order_index: 1, description: 'What the state does.' },
  { id: 'c2', title: 'Political Theory',     order_index: 2, content: 'A'.repeat(900) },
  { id: 'c3', title: 'Indian Constitution',  order_index: 3 },
];

describe('buildCardContext — subject path (legacy SubjectCard / SubjectPage flow)', () => {
  it('returns null when there is neither subject nor seed', () => {
    expect(buildCardContext({})).toBeNull();
    expect(buildCardContext({ subject: null, seedContext: '   ' })).toBeNull();
  });

  it('renders the rag.py-priority markers in subject-only mode', () => {
    const out = buildCardContext({ subject: SUBJECT, scopedChapters: CHAPTERS, user: USER });
    expect(out).toContain('Subject: Political Science 2nd Sem NEP');
    expect(out).toContain('Description: Comparative governance');
    expect(out).toContain('Topics covered: Polity, Theory, Governance');
    expect(out).toContain('Board/Class: AHSEC | Class 12 | Arts');
    expect(out).toContain('Syllabus chapters:');
    expect(out).toContain('Chapter 1 — State and Government: What the state does.');
    expect(out).toContain('Chapter 3 — Indian Constitution');
    expect(out).not.toContain('Active chapter (priority context):');
  });

  it('promotes the active chapter and excludes it from the "Other chapters" list', () => {
    const out = buildCardContext({
      subject: SUBJECT,
      scopedChapters: CHAPTERS,
      activeChapter: CHAPTERS[1],
      user: USER,
    });
    expect(out).toContain('Active chapter (priority context): Political Theory');
    expect(out).toContain('Other chapters in this subject:');
    expect(out).toContain('Chapter 1 — State and Government');
    expect(out).toContain('Chapter 3 — Indian Constitution');
    const activeIdx = out.indexOf('Active chapter (priority context):');
    const otherIdx  = out.indexOf('Other chapters in this subject:');
    expect(activeIdx).toBeGreaterThan(-1);
    expect(otherIdx).toBeGreaterThan(activeIdx);
    expect(out.match(/Political Theory/g)?.length).toBe(1);
  });

  it('caps at 4 000 chars', () => {
    const fat = {
      ...SUBJECT,
      description: 'D'.repeat(5000),
      tags: Array.from({ length: 200 }, (_, i) => `tag${i}`),
    };
    const out = buildCardContext({ subject: fat, scopedChapters: CHAPTERS, user: USER });
    expect(out.length).toBeLessThanOrEqual(4000);
  });
});

describe('buildCardContext — seedContext path (Task #409 PersonalizedCmsPage flow)', () => {
  it('returns the seed alone when no subject is present', () => {
    const out = buildCardContext({ seedContext: 'PERSONALIZED STUDY PLAN (priority context):\nTitle: 7-day Polity sprint' });
    expect(out).toContain('PERSONALIZED STUDY PLAN (priority context):');
    expect(out).toContain('Title: 7-day Polity sprint');
    expect(out).not.toContain('Subject:');
  });

  it('prepends the seed ABOVE the subject summary so the seed outranks the syllabus', () => {
    const seed = 'PERSONALIZED STUDY PLAN (priority context):\nTitle: 7-day Polity sprint';
    const out = buildCardContext({
      subject: SUBJECT,
      scopedChapters: CHAPTERS,
      user: USER,
      seedContext: seed,
    });
    const seedIdx = out.indexOf('PERSONALIZED STUDY PLAN');
    const subjIdx = out.indexOf('Subject: Political Science 2nd Sem NEP');
    expect(seedIdx).toBe(0);
    expect(subjIdx).toBeGreaterThan(seedIdx);
    expect(out).toContain('Syllabus chapters:');
  });

  it('ignores a whitespace-only seed', () => {
    const out = buildCardContext({ subject: SUBJECT, seedContext: '   \n\n   ' });
    expect(out.startsWith('Subject:')).toBe(true);
  });
});

describe('buildPlanSeedContext (Task #409 personalised-plan summariser)', () => {
  it('returns "" for falsy / non-object input', () => {
    expect(buildPlanSeedContext(null)).toBe('');
    expect(buildPlanSeedContext(undefined)).toBe('');
    expect(buildPlanSeedContext('not-a-doc')).toBe('');
  });

  it('renders title, subject, plan length, weak topics, then the body', () => {
    const seed = buildPlanSeedContext({
      title: '7-day Indian Polity sprint',
      subject_name: 'Political Science 2nd Sem NEP',
      days: 7,
      weak_topics: ['Federalism', 'Fundamental Rights'],
      content: 'Day 1: Read Ch 1.\nDay 2: Practice MCQs.',
    });
    expect(seed.startsWith('PERSONALIZED STUDY PLAN (priority context):')).toBe(true);
    expect(seed).toContain('Title: 7-day Indian Polity sprint');
    expect(seed).toContain('Subject: Political Science 2nd Sem NEP');
    expect(seed).toContain('Plan length: 7-day sprint');
    expect(seed).toContain('Weak topics flagged for review: Federalism, Fundamental Rights');
    expect(seed).toContain('Plan content:');
    expect(seed).toContain('Day 1: Read Ch 1.');
  });

  it('strips inline HTML from content_html when raw content is missing', () => {
    const seed = buildPlanSeedContext({
      title: 'Plan',
      content_html: '<h2>Day 1</h2><p>Read <strong>chapter 1</strong>.</p>',
    });
    expect(seed).toContain('Day 1 Read chapter 1');
    expect(seed).not.toMatch(/<\/?[a-z]+/i);
  });

  it('truncates the body at 2 000 chars so the seed plus library boilerplate fits the prompt budget', () => {
    const seed = buildPlanSeedContext({ title: 'Plan', content: 'X'.repeat(5000) });
    const body = seed.split('Plan content:\n')[1] || '';
    expect(body.length).toBeLessThanOrEqual(2000);
  });
});
