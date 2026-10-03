import { describe, expect, it, vi } from 'vitest';

import { conversationDetail } from './conversations';

describe('conversation source provenance', () => {
  it('restores the verified curriculum chain from assistant message metadata', async () => {
    const sourceEntry = {
      id: 'chapter:chapter-cell',
      title: 'Cell structure',
      kind: 'curriculum',
      url: '/ahsec/class-11/science/biology/cell-structure',
      snippet: 'The cell membrane controls transport.',
      matched_passage: 'The cell membrane controls transport.',
      chapter_id: 'chapter-cell',
      chapter_slug: 'cell-structure',
      subject_slug: 'biology',
      subject_id: 'biology-11',
      course_slug: 'science',
      course_name: 'Science',
      class_slug: 'class-11',
      class_name: 'Class 11',
      board_slug: 'ahsec',
      board_name: 'AHSEC',
      topic_name: 'Cell membrane',
      subject_name: 'Biology',
      medium: 'english',
      source_type: 'chapter_direct_notes',
    };
    const sourceCard = {
      rag_source: 'chapter_direct_notes',
      rag_path: 'chapter_direct',
      chapter_id: 'chapter-cell',
      rag_chapter_name: 'Cell structure',
      rag_chapter_slug: 'cell-structure',
      rag_subject_id: 'biology-11',
      rag_subject_name: 'Biology',
      rag_topic_name: 'Cell membrane',
      rag_board_name: 'AHSEC',
      rag_class_name: 'Class 11',
      rag_stream_name: 'Science',
      rag_stream_slug: 'science',
      rag_course_name: 'Science',
      rag_course_slug: 'science',
      rag_board_slug: 'ahsec',
      rag_class_slug: 'class-11',
      rag_subject_slug: 'biology',
      sources: [sourceEntry],
      matched_passage: sourceEntry.matched_passage,
    };
    const messageRows = [
      {
        role: 'user',
        content: 'Why does the cell membrane control transport?',
        lang: 'en',
        subject_id: 'biology-11',
        chapter_id: 'chapter-cell',
        metadata: '{}',
        created_at: 1,
      },
      {
        role: 'assistant',
        content: 'It controls which substances enter and leave the cell.',
        lang: 'en',
        subject_id: 'biology-11',
        chapter_id: 'chapter-cell',
        metadata: JSON.stringify({ model: 'test-model', source_card: sourceCard }),
        created_at: 2,
      },
    ];
    const db = {
      prepare: vi.fn((sql: string) => ({
        bind: vi.fn(() => sql.includes('SELECT role, content')
          ? { all: vi.fn(async () => ({ results: messageRows })) }
          : { first: vi.fn(async () => null) }),
      })),
    };
    const context = {
      env: { DB: db },
      json: vi.fn((body: unknown) => new Response(JSON.stringify(body), { status: 200 })),
    };

    const response = await conversationDetail(
      context as unknown as Parameters<typeof conversationDetail>[0],
      'user-1',
      'session-1',
    );
    const body = await response.json() as {
      messages: Array<Record<string, unknown>>;
    };

    expect(body.messages[1]).toMatchObject({
      rag_source: 'chapter_direct_notes',
      rag_topic_name: 'Cell membrane',
      rag_chapter_name: 'Cell structure',
      rag_subject_name: 'Biology',
      rag_course_name: 'Science',
      rag_class_name: 'Class 11',
      source_entries: [sourceEntry],
    });
  });
});