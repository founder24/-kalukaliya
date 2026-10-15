import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import axios from 'axios';
import TopicAnswerManager from './TopicAnswerManager';

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
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
});
