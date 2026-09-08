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
      {msg.isAiUnavailable && <div data-testid="ai-unavailable-card">AI unavailable</div>}
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

function chunkedStream(chunks, { close = true } = {}) {
  const encoder = new TextEncoder();
  const encoded = chunks.map((chunk) =>
    typeof chunk === 'string' ? encoder.encode(chunk) : chunk
  );
  return new Response(new ReadableStream({
    start(controller) {
      encoded.forEach((chunk) => controller.enqueue(chunk));
      if (close) controller.close();
    },
  }), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
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
    const fetchMock = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<ChatPage />);

    fireEvent.click(screen.getByRole('button', { name: 'Send test message' }));

    // The transport performs one bounded automatic retry before leaving the
    // stable manual-retry card on screen.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByTestId('connection-interrupted-card')).toBeInTheDocument();
    expect(screen.getByTestId('failure-stage')).toHaveTextContent('pre_response');
    expect(screen.getAllByText('Explain gravity')).toHaveLength(1);
    expect(screen.queryByText('Failed to fetch')).not.toBeInTheDocument();
  });

  it('turns a mid-stream reader failure into a recoverable connection card', async () => {
    const fetchMock = vi.fn(async () => disconnectedStream());
    vi.stubGlobal('fetch', fetchMock);
    render(<ChatPage />);

    fireEvent.click(screen.getByRole('button', { name: 'Send test message' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
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

  it('keeps a terminal SSE error as an error card instead of overwriting it with an empty success', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => chunkedStream([
      `data: ${JSON.stringify({
        error: 'The AI provider is temporarily unavailable.',
        error_kind: 'provider_unavailable',
        failure_stage: 'provider_stream',
      })}\n\n`,
    ])));
    render(<ChatPage />);

    fireEvent.click(screen.getByRole('button', { name: 'Send test message' }));

    expect(await screen.findByTestId('ai-unavailable-card')).toBeInTheDocument();
    expect(screen.queryByTestId('assistant-content')).not.toBeInTheDocument();
  });

  it('parses fragmented UTF-8, CRLF framing, and a final unterminated event', async () => {
    const encoder = new TextEncoder();
    const prefix = encoder.encode(`data:${JSON.stringify({ content: 'অসমীয়া' })}\r\n\r\n`);
    const done = encoder.encode(`data:${JSON.stringify({ event: 'syrabit_done', done: true })}`);
    vi.stubGlobal('fetch', vi.fn(async () => chunkedStream([
      prefix.slice(0, 9),
      prefix.slice(9, 14),
      prefix.slice(14),
      done,
    ])));
    render(<ChatPage />);

    fireEvent.click(screen.getByRole('button', { name: 'Send test message' }));

    expect(await screen.findByText('অসমীয়া')).toBeInTheDocument();
  });

  it('stops reading after the SSE done marker', async () => {
    const body =
      `data: ${JSON.stringify({ content: 'Complete answer' })}\n\n` +
      `data: ${JSON.stringify({ event: 'syrabit_done', done: true })}\n\n` +
      'data: [DONE]\n\n';
    vi.stubGlobal('fetch', vi.fn(async () => chunkedStream([body], { close: false })));
    render(<ChatPage />);

    fireEvent.click(screen.getByRole('button', { name: 'Send test message' }));

    expect(await screen.findByText('Complete answer')).toBeInTheDocument();
  });
});