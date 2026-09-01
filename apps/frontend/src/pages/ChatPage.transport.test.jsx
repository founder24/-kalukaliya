import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import React from 'react';

const mockSearchParams = vi.fn(() => [new URLSearchParams(), vi.fn()]);

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  useSearchParams: (...args) => mockSearchParams(...args),
  useLocation: () => ({
    pathname: '/chat',
    search: '',
    hash: '',
    state: null,
    key: 'transport-test',
  }),
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ user: null, authChecked: true }),
}));

vi.mock('@/context/LanguageContext', () => ({
  useContentLang: () => ({
    contentLang: 'en',
    switchLang: vi.fn(),
  }),
}));

vi.mock('@/components/layout/AppLayout', () => ({
  AppLayout: ({ children }) => <div>{children}</div>,
}));

vi.mock('./chat/EmptyState', () => ({
  EmptyState: () => <div>Empty chat</div>,
}));

vi.mock('./chat/InputBar', () => ({
  InputBar: ({ sendMsg }) => (
    <button type="button" onClick={() => sendMsg('Explain gravity')}>
      Send test message
    </button>
  ),
}));

vi.mock('./chat/ModelSelector', () => ({
  ModelSelector: () => null,
  MODELS: [{ id: 'gemini-flash', label: 'Gemini Flash' }],
}));

vi.mock('./chat/MessageBubble', () => ({
  MessageBubble: ({ msg, onRetry }) => (
    <article data-role={msg.role}>
      {msg.role === 'user' && <span>{msg.content}</span>}
      {msg.isConnectionInterrupted && (
        <div data-testid="connection-interrupted-card">
          Connection interrupted
          <button type="button" onClick={onRetry}>Retry now</button>
          <span data-testid="failure-stage">{msg.failureStage}</span>
        </div>
      )}
      {!msg.isAiUnavailable && msg.role === 'assistant' && (
        <span data-testid="assistant-content">{msg.content}</span>
      )}
    </article>
  ),
}));

vi.mock('@/utils/api', () => ({
  getConversation: vi.fn(() => new Promise(() => {})),
  getAnonConversation: vi.fn(() => new Promise(() => {})),
  getSubject: vi.fn(() => new Promise(() => {})),
  getChapters: vi.fn(() => new Promise(() => {})),
  API_BASE: 'https://api.example/api/v1',
  apiClient: () => ({
    get: vi.fn(() => new Promise(() => {})),
    post: vi.fn(() => new Promise(() => {})),
  }),
  getAnonId: vi.fn(() => 'anon_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'),
}));

vi.mock('@/hooks/useTokenManager', () => ({
  getToken: vi.fn(() => null),
}));

vi.mock('@/utils/analytics', () => ({
  Analytics: {
    page: vi.fn(),
    event: vi.fn(),
    chatMessage: vi.fn(),
    chatCreditsExhausted: vi.fn(),
  },
}));

vi.mock('@/utils/firebasePerf', () => ({
  startTrace: vi.fn(() => ({
    stop: vi.fn(),
    putAttribute: vi.fn(),
  })),
  makeTraceparent: vi.fn(() => null),
}));

vi.mock('@/hooks/useHashScroll', () => ({ useHashScroll: vi.fn() }));
vi.mock('@/components/ReviewPrompt', () => ({ requestReviewPrompt: vi.fn() }));
vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}));

import ChatPage from './ChatPage';

function completedStream(content = 'Gravity attracts masses.') {
  return new Response(
    `data: ${JSON.stringify({
      event: 'source_card',
      request_id: 'server-retry-id',
      conversation_id: 'conversation-1',
    })}\n\n` +
    `data: ${JSON.stringify({ content })}\n\n` +
    `data: ${JSON.stringify({ event: 'syrabit_done', done: true })}\n\n`,
    {
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'X-Request-ID': 'server-retry-id',
      },
    },
  );
}

function disconnectedStream() {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(
        `data: ${JSON.stringify({
          event: 'source_card',
          request_id: 'server-stream-id',
          conversation_id: 'conversation-1',
        })}\n\n`,
      ));
      controller.enqueue(encoder.encode(
        `data: ${JSON.stringify({ content: 'Partial answer' })}\n\n`,
      ));
      controller.error(new TypeError('network connection lost'));
    },
  });
  return new Response(body, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream',
      'X-Request-ID': 'server-stream-id',
    },
  });
}

describe('ChatPage transport recovery', () => {
  beforeEach(() => {
    vi.useRealTimers();
    HTMLElement.prototype.scrollIntoView = vi.fn();
    mockSearchParams.mockReturnValue([new URLSearchParams(), vi.fn()]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('turns a pre-response fetch rejection into a recoverable connection card', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }));
    render(<ChatPage />);

    fireEvent.click(screen.getByRole('button', { name: 'Send test message' }));

    expect(await screen.findByTestId('connection-interrupted-card')).toBeInTheDocument();
    expect(screen.getByTestId('failure-stage')).toHaveTextContent('pre_response');
    expect(screen.getAllByText('Explain gravity')).toHaveLength(1);
    expect(screen.queryByText('Failed to fetch')).not.toBeInTheDocument();
  });

  it('turns a mid-stream reader failure into a recoverable connection card', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => disconnectedStream()));
    render(<ChatPage />);

    fireEvent.click(screen.getByRole('button', { name: 'Send test message' }));

    expect(await screen.findByTestId('connection-interrupted-card')).toBeInTheDocument();
    expect(screen.getByTestId('failure-stage')).toHaveTextContent('stream');
    expect(screen.getAllByText('Explain gravity')).toHaveLength(1);
  });

  it('automatically retries once with the same logical request and no duplicate prompt', async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(completedStream());
    vi.stubGlobal('fetch', fetchMock);
    render(<ChatPage />);

    fireEvent.click(screen.getByRole('button', { name: 'Send test message' }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByTestId('connection-interrupted-card')).toBeInTheDocument();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const firstBody = JSON.parse(fetchMock.mock.calls[0][1].body);
    const secondBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(secondBody.client_request_id).toBe(firstBody.client_request_id);
    expect(screen.getAllByText('Explain gravity')).toHaveLength(1);

    expect(await screen.findByText('Gravity attracts masses.')).toBeInTheDocument();
  });
});