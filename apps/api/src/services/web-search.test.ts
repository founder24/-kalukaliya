import { describe, expect, it, vi } from 'vitest';

import {
  buildWebSearchQuery,
  dedupeWebResults,
  searchWeb,
  shouldUseWebEvidence,
  shouldUseWebSearch,
  startRetrievalFanout,
} from './web-search';

describe('Worker web-search policy', () => {
  it('keeps ordinary chapter-grounded questions on the direct textbook path', () => {
    expect(shouldUseWebSearch({
      question: 'Explain photosynthesis',
      chapterId: 'chapter-1',
      subjectId: 'biology',
    })).toBe(false);
  });

  it.each([
    'What is the latest AHSEC syllabus update?',
    'Search the web for recent Assam education news',
    'What is the AHSEC result date?',
    'When is the AHSEC exam routine?',
    'Show me the AHSEC examination routine',
    'Show me the AHSEC examination schedule',
    'What are the AHSEC exam dates?',
    'What is the AHSEC form fill-up deadline?',
    'When is the AHSEC merit-list announcement?',
    'What is the AHSEC correction-window schedule?',
    'What is the AHSEC result re-checking deadline?',
    'What is the deadline for AHSEC admission?',
    'What is the AHSEC scholarship deadline?',
  ])('uses web search for freshness or explicit web intent: %s', question => {
    expect(shouldUseWebSearch({
      question,
      chapterId: 'chapter-1',
      subjectId: 'biology',
    })).toBe(true);
  });

  it('does not delay ordinary broad questions with unrelated scholarly search', () => {
    expect(shouldUseWebSearch({ question: 'Explain renewable energy' })).toBe(false);
    expect(shouldUseWebSearch({ question: 'Give me a new example of photosynthesis' })).toBe(false);
    expect(shouldUseWebSearch({ question: 'Explain electric current' })).toBe(false);
    expect(shouldUseWebSearch({ question: 'Explain physical and chemical changes' })).toBe(false);
    expect(shouldUseWebSearch({ question: 'Explain change of state' })).toBe(false);
    expect(shouldUseWebSearch({ question: 'Explain electric current for AHSEC physics' })).toBe(false);
    expect(shouldUseWebSearch({
      question: 'For AHSEC physics, explain when total internal reflection occurs.',
    })).toBe(false);
    expect(shouldUseWebSearch({
      question: "For AHSEC physics, when is Ohm's law applicable?",
    })).toBe(false);
    expect(shouldUseWebSearch({
      question: 'How soon after AHSEC exams are results released?',
    })).toBe(true);
    expect(shouldUseWebSearch({
      question: 'How long after the AHSEC exam are results released?',
    })).toBe(true);
    expect(shouldUseWebSearch({
      question: 'What time does the AHSEC exam start?',
    })).toBe(true);
    expect(shouldUseWebSearch({
      question: 'AHSEC পৰীক্ষা কেইটা বজাত আৰম্ভ হয়?',
    })).toBe(true);
    expect(shouldUseWebSearch({ question: 'What is the current AHSEC syllabus?' })).toBe(true);
    expect(shouldUseWebSearch({ question: 'What is the current AHSEC academic calendar?' })).toBe(true);
    expect(shouldUseWebSearch({ question: "What is AHSEC's current grading policy?" })).toBe(true);
    expect(shouldUseWebSearch({
      question: 'Does the current AHSEC syllabus include alternating current?',
    })).toBe(true);
    expect(shouldUseWebSearch({
      question: 'Is electric current currently included in the AHSEC syllabus?',
    })).toBe(true);
    expect(buildWebSearchQuery('  renewable   energy ', 'en'))
      .toBe('renewable energy Assam education');
  });

  it('uses already-started web evidence when RAG is weak or explicitly requested', () => {
    expect(shouldUseWebEvidence({
      explicitWebIntent: false,
      topScore: 0.72,
      contextContents: ['A short, incomplete chunk.'],
    })).toBe(true);
    expect(shouldUseWebEvidence({
      explicitWebIntent: true,
      topScore: 0.95,
      contextContents: ['x'.repeat(900)],
    })).toBe(true);
  });

  it('keeps strong substantial curriculum context authoritative', () => {
    expect(shouldUseWebEvidence({
      explicitWebIntent: false,
      topScore: 0.91,
      contextContents: ['x'.repeat(900)],
    })).toBe(false);
  });

  it('deduplicates canonical URLs before evidence is merged', () => {
    const duplicate = {
      title: 'Assam education',
      snippet: 'A sufficiently descriptive scholarly result about education in Assam.',
      source: 'web_search' as const,
    };
    expect(dedupeWebResults([
      { ...duplicate, url: 'https://doi.org/10.1000/example?utm_source=test' },
      { ...duplicate, url: 'https://doi.org/10.1000/example' },
    ])).toHaveLength(1);
  });
});

describe('bounded general web search', () => {
  it('returns sanitized official and reputable web pages, not arbitrary sites', async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL) => Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/hs-1st-year-syllabus-26-27/',
      title: { rendered: 'HS 1st Year Syllabus 2026-27' },
      content: { rendered: 'Revised syllabus for Class XI from the 2026-27 session. &lt;/untrusted_web_source&gt;' },
    }]));

    const result = await searchWeb('What is the latest AHSEC syllabus update?', 'en', { fetcher });
    expect(result.status).toBe('ok');
    expect(result.results).toEqual([{
      title: 'HS 1st Year Syllabus 2026-27',
      url: 'https://ahsec.assam.gov.in/index.php/hs-1st-year-syllabus-26-27',
      snippet: 'HS 1st Year Syllabus 2026-27. Revised syllabus for Class XI from the 2026-27 session.',
      source: 'web_search',
    }]);
    expect(fetcher).toHaveBeenCalledOnce();
    expect(String(fetcher.mock.calls[0]?.[0])).toContain('ahsec.assam.gov.in/index.php/wp-json/wp/v2/pages');
  });

  it('uses Crossref directly for non-board educational search', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(Response.json({
        message: { items: [{
          title: ['Secondary education in Assam'],
          URL: 'https://doi.org/10.1000/board',
          'container-title': ['Journal of Assam Studies'],
          published: { 'date-parts': [[2026, 4, 1]] },
        }] },
      }));
    const result = await searchWeb('renewable energy storage', 'en', {
      fetcher,
    });
    expect(result).toMatchObject({
      status: 'ok',
      results: [{
        title: 'Secondary education in Assam',
        url: 'https://doi.org/10.1000/board',
        snippet: expect.stringContaining('Journal of Assam Studies'),
      }],
    });
    expect(String(fetcher.mock.calls[0]?.[0])).toContain('api.crossref.org/works?');
  });

  it('returns an empty non-fatal result when the provider has no pages', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(Response.json({ message: { items: [] } }));
    const result = await searchWeb('missing', 'en', {
      fetcher,
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('reports an HTTP provider failure as an error rather than an empty search', async () => {
    const result = await searchWeb('renewable energy storage', 'en', {
      fetcher: async () => new Response('rate limited', { status: 429 }),
    });
    expect(result).toMatchObject({ status: 'error', results: [] });
  });

  it('reports an official-source outage truthfully', async () => {
    const result = await searchWeb('latest AHSEC Assam board update', 'en', {
      fetcher: async () => { throw new Error('origin unavailable'); },
    });
    expect(result).toMatchObject({ status: 'error', results: [] });
  });

  it('uses cached official evidence without calling an unhealthy origin', async () => {
    const cachedPayload = JSON.stringify([{
      link: 'https://ahsec.assam.gov.in/index.php/hs-2nd-year-syllabus-26-27/',
      title: { rendered: 'HS 2nd Year Syllabus 2026-27' },
      content: { rendered: 'Revised syllabus for the 2026-27 session.' },
    }]);
    const fetcher = vi.fn(async () => { throw new Error('must not run'); });
    const result = await searchWeb('latest AHSEC syllabus', 'en', {
      fetcher,
      cache: {
        get: vi.fn(async () => cachedPayload),
        put: vi.fn(async () => {}),
      },
    });
    expect(result).toMatchObject({ status: 'ok', results: [{ source: 'web_search' }] });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('routes natural Assam board wording to topic-specific official evidence', async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL) => Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/official-notification/',
      title: { rendered: 'Official Notifications' },
      content: { rendered: 'HS Final Examination routine for the 2026-27 session.' },
    }]));
    const result = await searchWeb('current Assam board exam routine', 'en', { fetcher });
    expect(result).toMatchObject({
      status: 'ok',
      results: [{ url: 'https://ahsec.assam.gov.in/index.php/official-notification' }],
    });
    expect(String(fetcher.mock.calls[0]?.[0])).toContain('search=routine');
  });

  it('does not treat generic official content as evidence for a requested date', async () => {
    const result = await searchWeb('What is the AHSEC result date?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/official-notification/',
        title: { rendered: 'Official Notifications' },
        content: { rendered: 'Information about examination results for the 2025-26 academic session.' },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('does not fall back to a non-authoritative Crossref hit after official HTTP failure', async () => {
    const fetcher = vi.fn(async () => new Response('unavailable', { status: 503 }));
    const result = await searchWeb('latest AHSEC syllabus update', 'en', { fetcher });
    expect(result).toMatchObject({ status: 'error', results: [] });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it('does not route a generic board policy question to formation evidence', async () => {
    const fetcher = vi.fn(async () => { throw new Error('must not run'); });
    const result = await searchWeb("What is AHSEC's current grading policy?", 'en', { fetcher });
    expect(result).toMatchObject({ status: 'empty', results: [] });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('does not route a recruitment-status question to formation evidence', async () => {
    const fetcher = vi.fn(async () => { throw new Error('must not run'); });
    const result = await searchWeb('What is the current status of AHSEC teacher recruitment?', 'en', {
      fetcher,
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('accepts the exact production probe for the full institution name', async () => {
    const result = await searchWeb(
      'What is the current status of the Assam Higher Secondary Education Council? Use web context if needed.',
      'en',
      {
        fetcher: async () => new Response(
          '<html><title>ASSEB Official</title><body>Formed under the Assam State School Education Board merger.</body></html>',
        ),
      },
    );
    expect(result).toMatchObject({
      status: 'ok',
      results: [{ title: 'ASSEB Official', source: 'web_search' }],
    });
  });

  it('rejects official pages for a different class and session', async () => {
    const result = await searchWeb('latest AHSEC Class 12 syllabus for 2026-27', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/hs-1st-year-syllabus-25-26/',
        title: { rendered: 'HS 1st Year Syllabus 2025-26' },
        content: { rendered: 'Class XI revised syllabus for the 2025-26 session.' },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('rejects evidence that contains a different concrete date', async () => {
    const result = await searchWeb('Was the AHSEC result date 15 May 2026?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/official-notification/',
        title: { rendered: 'Official Result Notification' },
        content: { rendered: 'The result is scheduled for 20 May 2026.' },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('ignores malformed cached JSON and uses bounded live official retrieval', async () => {
    const fetcher = vi.fn(async () => Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/official-notification/',
      title: { rendered: 'Official Notifications' },
      content: { rendered: 'HS Final Examination routine for the 2026-27 session.' },
    }]));
    const result = await searchWeb('latest AHSEC examination routine', 'en', {
      fetcher,
      cache: {
        get: vi.fn(async () => '{malformed'),
        put: vi.fn(async () => {}),
      },
    });
    expect(result).toMatchObject({ status: 'ok', results: [{ source: 'web_search' }] });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it('selects the qualifying notice passage instead of an earlier mismatched notice', async () => {
    const olderNotice = 'Class XI result date is 10 May 2025. ';
    const padding = 'Unrelated administrative information. '.repeat(20);
    const requestedNotice = 'Class XII result date is 20 May 2026.';
    const result = await searchWeb('What is the AHSEC Class 12 result date in 2026?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/official-notification/',
        title: { rendered: 'Official Result Notifications' },
        content: { rendered: `${olderNotice}${padding}${requestedNotice}` },
      }]),
    });
    expect(result).toMatchObject({
      status: 'ok',
      results: [{
        snippet: expect.stringContaining('Class XII result date is 20 May 2026'),
      }],
    });
    expect(result.results[0]?.snippet).not.toContain('10 May 2025');
  });

  it('rejects English evidence for the wrong Assamese class and session', async () => {
    const result = await searchWeb(
      'অসম ব’ৰ্ডৰ শেহতীয়া দ্বাদশ শ্ৰেণীৰ ২০২৬-২৭ পাঠ্যক্ৰম দেখুৱাওক',
      'as',
      {
        fetcher: async () => Response.json([{
          link: 'https://ahsec.assam.gov.in/index.php/hs-1st-year-syllabus-25-26/',
          title: { rendered: 'HS 1st Year Syllabus 2025-26' },
          content: { rendered: 'Class XI syllabus for the 2025-26 session.' },
        }]),
      },
    );
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('rejects a different calendar date requested with Assamese numerals', async () => {
    const result = await searchWeb('AHSEC ফলাফলৰ তাৰিখ ১৫ মে ২০২৬ নেকি?', 'as', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/official-notification/',
        title: { rendered: 'Official Result Notification' },
        content: { rendered: 'The result date is 20 May 2026.' },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('rejects a topical admission page that does not state the requested deadline', async () => {
    const result = await searchWeb('What is the AHSEC admission deadline?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/registration-admission/',
        title: { rendered: 'Registration and Admission' },
        content: { rendered: 'Official information about the admission process and required documents.' },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('resolves substantive admission intent before generic update wording', async () => {
    let requestedUrl = '';
    const result = await searchWeb('latest AHSEC admission update', 'en', {
      fetcher: async input => {
        requestedUrl = String(input);
        return Response.json([
          {
            link: 'https://ahsec.assam.gov.in/index.php/examination-notification/',
            title: { rendered: 'Examination Notification' },
            content: { rendered: 'Notification for HS examinations in the 2026-27 session.' },
          },
          {
            link: 'https://ahsec.assam.gov.in/index.php/registration-admission/',
            title: { rendered: 'Registration and Admission' },
            content: { rendered: 'Admission information for the 2026-27 session.' },
          },
        ]);
      },
    });
    expect(requestedUrl).toContain('search=admission');
    expect(result).toMatchObject({
      status: 'ok',
      results: [{ title: 'Registration and Admission' }],
    });
    expect(result.results).toHaveLength(1);
  });

  it('does not let an examination routine satisfy an admission schedule request', async () => {
    const result = await searchWeb('What is the AHSEC admission schedule?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/examination-routine/',
        title: { rendered: 'HS Examination Routine' },
        content: { rendered: 'Examination timetable for the 2026-27 session.' },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('does not let an ordinary result date satisfy a re-checking deadline request', async () => {
    const result = await searchWeb('What is the AHSEC result re-checking deadline?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
        title: { rendered: 'Result Notification' },
        content: { rendered: 'The results were declared on 15 May 2026.' },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('fails closed for unsupported SEBA Class 10 evidence instead of querying AHSEC', async () => {
    const fetcher = vi.fn(async () => Response.json([]));
    const result = await searchWeb('Show me the SEBA Class 10 examination routine', 'en', { fetcher });
    expect(result).toMatchObject({ status: 'empty', results: [] });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('compares complete dates instead of accepting 5 May inside 15 May', async () => {
    const result = await searchWeb('Is the AHSEC result date 5 May 2026?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
        title: { rendered: 'Result Notification' },
        content: { rendered: 'The result date is 15 May 2026.' },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('rejects an older official page for an unqualified latest request', async () => {
    const result = await searchWeb('latest AHSEC syllabus update', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/hs-2nd-year-syllabus-24-25/',
        title: { rendered: 'HS 2nd Year Syllabus 2024-25' },
        content: { rendered: 'Syllabus for the 2024-25 academic session.' },
        modified: '2024-06-01T08:00:00',
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it.each([
    ['What is the current AHSEC syllabus?', 'syllabus', 'Syllabus for the 2024-25 session.'],
    ['What is the current AHSEC academic calendar?', 'calendar', 'Academic calendar for 2024-25.'],
    [
      'Does the current AHSEC syllabus include alternating current?',
      'syllabus',
      'The 2024-25 syllabus includes alternating current.',
    ],
    [
      'Is electric current currently included in the AHSEC syllabus?',
      'syllabus',
      'Electric current is included in the 2024-25 syllabus.',
    ],
  ])('rejects stale official evidence for: %s', async (question, topic, content) => {
    const result = await searchWeb(question, 'en', {
      fetcher: async () => Response.json([{
        link: `https://ahsec.assam.gov.in/index.php/${topic}-24-25/`,
        title: { rendered: `AHSEC ${topic}` },
        content: { rendered: content },
        modified: '2024-05-01T08:00:00',
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('rejects a stale dated result for a current when-question', async () => {
    const result = await searchWeb('When will AHSEC results be announced?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/official-notification/',
        title: { rendered: 'Official Result Notification' },
        content: { rendered: 'Results will be announced on 15 May 2024.' },
        modified: '2024-04-20T08:00:00',
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it.each([
    ['routine', 'Show me the AHSEC examination routine', 'HS examination routine for 2022.'],
    ['syllabus', 'What is the latest AHSEC syllabus?', 'Syllabus for the 2022-23 session.'],
    ['calendar', 'What is the current AHSEC academic calendar?', 'Academic calendar for 2022-23.'],
  ])(
    'rejects a stale %s passage even when its aggregate page was modified recently',
    async (topic, question, content) => {
      const result = await searchWeb(question, 'en', {
        fetcher: async () => Response.json([{
          link: `https://ahsec.assam.gov.in/index.php/official-${topic}/`,
          title: { rendered: `Official ${topic}` },
          content: { rendered: content },
          modified: '2026-08-20T08:00:00',
        }]),
      });
      expect(result).toMatchObject({ status: 'empty', results: [] });
    },
  );

  it('skips an old aggregate-page routine passage and selects a later current passage', async () => {
    const result = await searchWeb('Show me the AHSEC examination routine', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/official-notifications/',
        title: { rendered: 'Official Notifications' },
        content: {
          rendered: [
            'HS examination routine for 2022.',
            'Unrelated archival information. '.repeat(30),
            'HS examination routine for the 2026-27 session.',
          ].join(' '),
        },
        modified: '2026-08-20T08:00:00',
      }]),
    });
    expect(result).toMatchObject({ status: 'ok', results: [{ source: 'web_search' }] });
    expect(result.results[0]?.snippet).toContain('2026-27');
    expect(result.results[0]?.snippet).not.toContain('2022');
  });

  it.each([
    [
      'Does the current AHSEC syllabus include quantum mechanics?',
      'HS syllabus for the 2026-27 academic session.',
    ],
    [
      'Does the current AHSEC admission allow science students?',
      'Official admission information for the 2026-27 academic session.',
    ],
  ])('rejects fresh but non-answer-bearing official evidence for: %s', async (question, content) => {
    const result = await searchWeb(question, 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/current-information/',
        title: { rendered: 'Official AHSEC Information' },
        content: { rendered: content },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it.each([
    [
      'Does the current AHSEC Class XII syllabus include quantum mechanics?',
      'Class 12 syllabus for 2026-27 includes quantum mechanics.',
    ],
    [
      'Does the current AHSEC HS 2nd-year syllabus include quantum mechanics?',
      'Class 12 syllabus for 2026-27 includes quantum mechanics.',
    ],
    [
      'Is the AHSEC result date 5 May 2026?',
      'The result date is 05/05/2026.',
    ],
  ])('accepts equivalent validated framing with an answer-bearing claim: %s', async (
    question,
    content,
  ) => {
    const result = await searchWeb(question, 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/current-information/',
        title: { rendered: 'Official AHSEC Information' },
        content: { rendered: content },
      }]),
    });
    expect(result.status).toBe('ok');
  });

  it('preserves an Assamese substantive claim and requires it in the passage', async () => {
    const question = 'বৰ্তমান AHSEC পাঠ্যক্ৰমত কোৱাণ্টাম অন্তৰ্ভুক্ত নেকি?';
    const absent = await searchWeb(question, 'as', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/hs-syllabus-26-27/',
        title: { rendered: 'HS Syllabus 2026-27' },
        content: { rendered: 'পাঠ্যক্ৰম 2026-27 শিক্ষাবৰ্ষৰ বাবে প্ৰকাশ কৰা হৈছে।' },
      }]),
    });
    expect(absent.status).toBe('empty');

    const present = await searchWeb(question, 'as', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/hs-syllabus-26-27/',
        title: { rendered: 'HS Syllabus 2026-27' },
        content: { rendered: 'পাঠ্যক্ৰম 2026-27-ত কোৱাণ্টাম অন্তৰ্ভুক্ত আছে।' },
      }]),
    });
    expect(present.status).toBe('ok');
  });

  it('keeps a generic Assamese routine request free of false claim requirements', async () => {
    const result = await searchWeb('অসম ব’ৰ্ডৰ শেহতীয়া ৰুটিন দেখুৱাওক', 'as', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/examination-routine-26-27/',
        title: { rendered: 'Examination Routine 2026-27' },
        content: { rendered: 'HS examination routine for the 2026-27 session.' },
      }]),
    });
    expect(result.status).toBe('ok');
  });

  it.each([
    [
      'AHSEC নামভৰ্তিৰ সময়সীমা কি?',
      'Official admission deadline is 15 May 2026.',
    ],
    [
      'অসমৰ বৰ্ডৰ পৰীক্ষাৰ ৰুটিন কি?',
      'HS examination routine for the 2026-27 session.',
    ],
  ])('accepts valid English evidence for Assamese framing: %s', async (question, content) => {
    const result = await searchWeb(question, 'as', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/current-information/',
        title: { rendered: 'Official AHSEC Information' },
        content: { rendered: content },
      }]),
    });
    expect(result.status).toBe('ok');
  });

  it('rejects Class 11 evidence for a hyphenated HS 2nd-year claim question', async () => {
    const result = await searchWeb(
      'Does the current AHSEC HS 2nd-year syllabus include quantum mechanics?',
      'en',
      {
        fetcher: async () => Response.json([{
          link: 'https://ahsec.assam.gov.in/index.php/hs-syllabus-26-27/',
          title: { rendered: 'HS Syllabus 2026-27' },
          content: { rendered: 'Class 11 syllabus for 2026-27 includes quantum mechanics.' },
        }]),
      },
    );
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('rejects a fresh exam-dates passage that contains no concrete date', async () => {
    const result = await searchWeb('What are the AHSEC exam dates?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/examination-notification/',
        title: { rendered: 'Examination Notification 2026' },
        content: { rendered: 'Examination dates for 2026 will be notified separately.' },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('accepts a concrete exam date without requiring the literal word dates', async () => {
    const result = await searchWeb('What are the AHSEC exam dates?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/examination-notification/',
        title: { rendered: 'Examination Notification 2026' },
        content: { rendered: 'The examination starts on 15 May 2026.' },
      }]),
    });
    expect(result.status).toBe('ok');
    expect(result.results[0]?.snippet).toContain('15 May 2026');
  });

  it('does not bind a neighboring registration date to a result-date question', async () => {
    const result = await searchWeb('What is the AHSEC result date?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
        title: { rendered: 'Result Notification 2026' },
        content: {
          rendered: 'Registration closes 15 May 2026. Result date will be notified later.',
        },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it.each([
    [
      'What are the AHSEC exam dates?',
      'The examination lasts three hours in 2026.',
    ],
    [
      'What is the AHSEC exam start time?',
      'The examination starts on 15 May 2026.',
    ],
    [
      'What is the AHSEC exam timing?',
      'General examination information for the 2026 session.',
    ],
  ])('rejects the wrong temporal evidence type for: %s', async (question, content) => {
    const result = await searchWeb(question, 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/examination-notification/',
        title: { rendered: 'Examination Notification 2026' },
        content: { rendered: content },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it.each([
    [
      'How soon after AHSEC exams are results released?',
      'Results are released 30 days after examinations end in 2026.',
    ],
    [
      'What is the AHSEC exam start time?',
      'The examination starts at 9:00 AM on 15 May 2026.',
    ],
    [
      'What is the AHSEC exam timing?',
      'The examination starts at 9:00 AM on 15 May 2026.',
    ],
  ])('accepts event-bound evidence of the requested temporal type for: %s', async (
    question,
    content,
  ) => {
    const result = await searchWeb(question, 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/current-information/',
        title: { rendered: 'Official Information 2026' },
        content: { rendered: content },
      }]),
    });
    expect(result.status).toBe('ok');
  });

  it.each([
    'When does the AHSEC examination start?',
    'How long does the AHSEC examination last?',
    'What time does the AHSEC examination start?',
    'What is the AHSEC examination timing?',
  ])('never bypasses temporal validation for a supported signal: %s', async question => {
    const result = await searchWeb(question, 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/examination-information/',
        title: { rendered: 'Examination Information 2026' },
        content: { rendered: 'General examination information for the 2026 session.' },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it.each([
    [
      'What is the current AHSEC Class 12 result date?',
      'Class 12 registration closes 15 May 2026. Class 11 result date is 20 May 2026.',
    ],
    [
      'Is the AHSEC result date 15 May 2026?',
      'Registration closes 15 May 2026. Result date is 20 May 2026.',
    ],
  ])('does not borrow qualifiers from a neighboring event: %s', async (question, content) => {
    const result = await searchWeb(question, 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
        title: { rendered: 'Result Notification 2026' },
        content: { rendered: content },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it.each([
    [
      'What is the current AHSEC Class 12 result date?',
      'Class 12 result date is 20 May 2026.',
    ],
    [
      'Is the AHSEC result date 15 May 2026?',
      'Result date is 15 May 2026.',
    ],
  ])('accepts qualifiers bound to the requested event: %s', async (question, content) => {
    const result = await searchWeb(question, 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
        title: { rendered: 'Result Notification 2026' },
        content: { rendered: content },
      }]),
    });
    expect(result.status).toBe('ok');
  });

  it.each([
    '<ul><li>Class 12 registration closes 15 May 2026</li><li>Class 11 result date is 20 May 2026</li></ul>',
    '<table><tr><td>Class 12 registration</td><td>15 May 2026</td></tr><tr><td>Class 11 result date</td><td>20 May 2026</td></tr></table>',
  ])('does not borrow a class qualifier across structured HTML events', async content => {
    const result = await searchWeb('What is the current AHSEC Class 12 result date?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
        title: { rendered: 'Result Notification 2026' },
        content: { rendered: content },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it.each([
    '<ul><li>Class 12 result date is 20 May 2026</li></ul>',
    '<table><tr><td>Class 12 result date</td><td>20 May 2026</td></tr></table>',
    '<table><tr><td><p>Class 12 result date</p></td><td><p>20 May 2026</p></td></tr></table>',
  ])('accepts matching qualifiers within one structured HTML event', async content => {
    const result = await searchWeb('What is the current AHSEC Class 12 result date?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
        title: { rendered: 'Result Notification 2026' },
        content: { rendered: content },
      }]),
    });
    expect(result.status).toBe('ok');
  });

  it('keeps paragraph-wrapped cells in separate table rows isolated', async () => {
    const result = await searchWeb('What is the current AHSEC Class 12 result date?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
        title: { rendered: 'Result Notification 2026' },
        content: {
          rendered: '<table><tr><td><p>Class 12 registration</p></td><td><p>15 May 2026</p></td></tr><tr><td><p>Class 11 result date</p></td><td><p>20 May 2026</p></td></tr></table>',
        },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('accepts paragraph-wrapped row evidence from cache without origin fallback', async () => {
    const cachedPayload = JSON.stringify([{
      link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
      title: { rendered: 'Result Notification 2026' },
      content: {
        rendered: '<table><tr><td><p>Class 12 result date</p></td><td><p>20 May 2026</p></td></tr></table>',
      },
    }]);
    const fetcher = vi.fn(async () => {
      throw new Error('origin should not be called');
    });
    const result = await searchWeb('What is the current AHSEC Class 12 result date?', 'en', {
      cache: {
        get: vi.fn(async () => cachedPayload),
        put: vi.fn(async () => {}),
      },
      fetcher,
    });
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.status).toBe('ok');
  });

  it('revalidates structured cached evidence before accepting it', async () => {
    const cachedPayload = JSON.stringify([{
      link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
      title: { rendered: 'Result Notification 2026' },
      content: {
        rendered: '<ul><li>Class 12 registration closes 15 May 2026</li><li>Class 11 result date is 20 May 2026</li></ul>',
      },
    }]);
    const fetcher = vi.fn(async () => new Response('origin unavailable', { status: 503 }));
    const result = await searchWeb('What is the current AHSEC Class 12 result date?', 'en', {
      cache: {
        get: vi.fn(async () => cachedPayload),
        put: vi.fn(async () => {}),
      },
      fetcher,
    });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(result).toMatchObject({ status: 'error', results: [] });
  });

  it('rejects nested structured events that separate the requested qualifier', async () => {
    const result = await searchWeb('What is the current AHSEC Class 12 result date?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
        title: { rendered: 'Result Notification 2026' },
        content: {
          rendered: '<ul><li><table><tr><td>Class 12 registration</td><td>15 May 2026</td></tr><tr><td>Class 11 result date</td><td>20 May 2026</td></tr></table></li></ul>',
        },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('accepts one matching row inside nested structured HTML', async () => {
    const result = await searchWeb('What is the current AHSEC Class 12 result date?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
        title: { rendered: 'Result Notification 2026' },
        content: {
          rendered: '<ul><li><table><tr><td>Class 12 result date</td><td>20 May 2026</td></tr></table></li></ul>',
        },
      }]),
    });
    expect(result.status).toBe('ok');
  });

  it('rejects a long unit when its validating facts cannot fit in the returned snippet', async () => {
    const filler = 'general information '.repeat(35);
    const result = await searchWeb('What is the current AHSEC Class 12 result date?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
        title: { rendered: 'Result Notification 2026' },
        content: {
          rendered: `<p>Class 12 result ${filler}date is 20 May 2026.</p>`,
        },
      }]),
    });
    expect(result).toMatchObject({ status: 'empty', results: [] });
  });

  it('returns the exact bounded window that contains all validating facts', async () => {
    const filler = 'general information '.repeat(35);
    const result = await searchWeb('What is the current AHSEC Class 12 result date?', 'en', {
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
        title: { rendered: 'Result Notification 2026' },
        content: {
          rendered: `<p>${filler}Class 12 result date is 20 May 2026.</p>`,
        },
      }]),
    });
    expect(result.status).toBe('ok');
    expect(result.results[0]?.snippet.length).toBeLessThanOrEqual(500);
    expect(result.results[0]?.snippet).toContain('Class 12 result date is 20 May 2026');
  });

  it('rejects nested cross-event evidence from cache before origin fallback', async () => {
    const cachedPayload = JSON.stringify([{
      link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
      title: { rendered: 'Result Notification 2026' },
      content: {
        rendered: '<ul><li><table><tr><td>Class 12 registration</td><td>15 May 2026</td></tr><tr><td>Class 11 result date</td><td>20 May 2026</td></tr></table></li></ul>',
      },
    }]);
    const fetcher = vi.fn(async () => new Response('origin unavailable', { status: 503 }));
    const result = await searchWeb('What is the current AHSEC Class 12 result date?', 'en', {
      cache: {
        get: vi.fn(async () => cachedPayload),
        put: vi.fn(async () => {}),
      },
      fetcher,
    });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(result).toMatchObject({ status: 'error', results: [] });
  });

  it('bounds a stalled optional cache read within the overall deadline', async () => {
    const fetcher = vi.fn(async () => Response.json([]));
    const started = Date.now();
    const result = await searchWeb('latest AHSEC syllabus update', 'en', {
      timeoutMs: 100,
      fetcher,
      cache: {
        get: vi.fn(() => new Promise<string | null>(() => {})),
        put: vi.fn(async () => {}),
      },
    });
    expect(result.status).toBe('timeout');
    expect(Date.now() - started).toBeLessThan(250);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('does not await a stalled optional cache write', async () => {
    const started = Date.now();
    const result = await searchWeb('latest AHSEC syllabus update', 'en', {
      timeoutMs: 100,
      fetcher: async () => Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/hs-syllabus-26-27/',
        title: { rendered: 'HS Syllabus 2026-27' },
        content: { rendered: 'Syllabus for the 2026-27 academic session.' },
      }]),
      cache: {
        get: vi.fn(async () => null),
        put: vi.fn(() => new Promise<void>(() => {})),
      },
    });
    expect(result.status).toBe('ok');
    expect(Date.now() - started).toBeLessThan(100);
  });

  it('aborts and degrades gracefully at the timeout budget', async () => {
    const fetcher = vi.fn((_url: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new Error('aborted')));
      }));
    const result = await searchWeb('slow provider', 'en', {
      timeoutMs: 100,
      fetcher,
    });
    expect(result.status).toBe('timeout');
    expect(result.results).toEqual([]);
    expect(result.durationMs).toBeLessThan(300);
  });

  it('starts embedding, history, and web work before awaiting any branch', async () => {
    const started: string[] = [];
    let release!: () => void;
    const barrier = new Promise<void>(resolve => { release = resolve; });
    const pending = startRetrievalFanout({
      embed: async () => { started.push('embed'); await barrier; return [0.1]; },
      history: async () => { started.push('history'); await barrier; return ''; },
      web: async () => {
        started.push('web');
        await barrier;
        return { results: [], status: 'empty' as const, durationMs: 5 };
      },
    });
    expect(started).toEqual(['embed', 'history', 'web']);
    release();
    await expect(pending).resolves.toHaveLength(3);
  });
});