import { describe, expect, it } from 'vitest';

import { buildSystemPrompt } from './chat';

describe('student chat curriculum scope', () => {
  it('limits English answers to Assam Board and refuses other boards', () => {
    const prompt = buildSystemPrompt({
      lang: 'en',
      contextText: '',
      history: '',
      question: 'Explain a CBSE chapter.',
    });

    expect(prompt).toContain('Your scope is limited to the Assam Board curriculum');
    expect(prompt).toContain('Do not answer CBSE, NCERT, ICSE');
    expect(prompt).toContain('invite the student to ask an Assam Board equivalent');
  });

  it('includes the same restriction in Assamese mode', () => {
    const prompt = buildSystemPrompt({
      lang: 'as',
      contextText: '',
      history: '',
      question: 'CBSE ৰ এটা অধ্যায় বুজাই দিয়া।',
    });

    expect(prompt).toContain('অসম ব’ৰ্ডৰ পাঠ্যক্রম');
    expect(prompt).toContain('CBSE, NCERT, ICSE');
    expect(prompt).toContain('অসম ব’ৰ্ডৰ সমতুল্য প্ৰশ্ন');
  });
});