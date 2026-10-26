import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import axios from 'axios';
import TopicAnswerManager from './TopicAnswerManager';

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('@/utils/adminHelpers', () => ({
  API: '/api/v1',
  authHeaders: () => ({ headers: { Authorization: 'Bearer test-token' } }),
}));

describe('TopicAnswerManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axios.get.mockResolvedValue({ data: { topics: [] } });
    axios.post.mockResolvedValue({ data: { topic: { id: 'new-topic' } } });
  });

  it('creates a cited answer card draft through the staff content route', async () => {
    render(
      <TopicAnswerManager
        chapter={{ id: 'chapter-1', title: 'Unit I' }}
        adminToken="test-token"
        user={{ role: 'admin' }}
        onBack={() => {}}
      />,
    );

    await screen.findByText('No answer cards on this chapter yet.');
    fireEvent.change(screen.getByTestId('topic-answer-title'), {
      target: { value: 'What shaped modern Assamese literature?' },
    });
    fireEvent.change(screen.getByTestId('topic-answer-content'), {
      target: { value: 'A sourced answer. Source: Assam Sahitya Sabha — https://example.org/source' },
    });
    fireEvent.click(screen.getByTestId('topic-answer-create'));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        expect.stringContaining('/staff/content/chapter/chapter-1/topics'),
        {
          title: 'What shaped modern Assamese literature?',
          content: 'A sourced answer. Source: Assam Sahitya Sabha — https://example.org/source',
        },
        expect.any(Object),
      );
    });
  });

  it('saves language-specific topic notes and explicit stable question links', async () => {
    axios.get.mockResolvedValue({ data: {
      topics: [{
        id: 'topic-1', title: 'A topic', topic_slug: 'a-topic', title_as: 'এটা বিষয়',
        topic_slug_as: 'eta-bishoy', status: 'published', notes_en: '', notes_as: '',
        notes_status_en: 'draft', notes_status_as: 'draft',
      }],
      questions: {
        en: [{ editor_question_id: 'q-1', question: 'What happened?', answer: 'A fact.' }],
        as: [{ editor_question_id: 'q-as-1', question: 'কি ঘটিছিল?', solution: 'উত্তৰ।' }],
      },
    } });
    axios.patch.mockResolvedValue({ data: { ok: true } });
    render(
      <TopicAnswerManager
        chapter={{ id: 'chapter-1', title: 'Unit I' }}
        adminToken="test-token"
        user={{ role: 'admin' }}
        onBack={() => {}}
      />,
    );
    const notes = await screen.findByTestId('topic-page-notes-en-topic-1');
    fireEvent.change(notes, { target: { value: 'Reviewed English notes.' } });
    const englishPanel = screen.getByTestId('topic-page-en-topic-1');
    fireEvent.click(within(englishPanel).getByRole('button', { name: /save notes/i }));
    await waitFor(() => expect(axios.patch).toHaveBeenCalledWith(
      expect.stringContaining('/topics/topic-1'),
      { notes_en: 'Reviewed English notes.' },
      expect.any(Object),
    ));
    await waitFor(() => {
      const panel = screen.getByTestId('topic-page-en-topic-1');
      expect(within(panel).getByRole('button', { name: /save question links/i }).disabled).toBe(false);
    });
    const refreshedPanel = screen.getByTestId('topic-page-en-topic-1');
    fireEvent.click(within(refreshedPanel).getByLabelText('What happened?'));
    fireEvent.click(within(refreshedPanel).getByRole('button', { name: /save question links/i }));
    await waitFor(() => expect(axios.patch).toHaveBeenCalledWith(
      expect.stringContaining('/topics/topic-1/question-links/en'),
      { question_ids: ['q-1'] },
      expect.any(Object),
    ));
  });
});
