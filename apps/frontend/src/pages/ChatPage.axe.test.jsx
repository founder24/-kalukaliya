/**
 * Task #197 — ChatPage: axe accessibility audit.
 *
 * Covers three render states students encounter:
 *  1. Anonymous user — empty chat (EmptyState shown)
 *  2. Authenticated user — empty chat (EmptyState shown)
 *  3. Anonymous user — loaded conversation with messages
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { axe, toHaveNoViolations } from 'jest-axe';
import { render, act } from '@testing-library/react';
import React from 'react';

expect.extend(toHaveNoViolations);

const mockSearchParams = vi.fn(() => [new URLSearchParams(), vi.fn()]);
vi.mock('react-router-dom', () => ({
  useNavigate:     () => vi.fn(),
  useSearchParams: (...args) => mockSearchParams(...args),
  // Task #409 — ChatPage now reads location.state.seedCardContext
  // (from PersonalizedCmsPage's "Ask AI about this plan" Link state)
  // to seed card_context. The axe test mounts <ChatPage /> bare, so
  // an empty-state location is sufficient — the consumer reads the
  // optional `state` field defensively.
  useLocation:     () => ({ pathname: '/chat', search: '', hash: '', state: null, key: 'test' }),
}));

const mockUseAuth = vi.fn(() => ({ user: null, authChecked: true }));
vi.mock('@/context/AuthContext', () => ({
  useAuth: (...args) => mockUseAuth(...args),
}));

vi.mock('@/context/LanguageContext', () => ({
  useContentLang: () => ({ contentLang: 'en', switchLang: vi.fn(), toggleLang: vi.fn() }),
}));

vi.mock('@/components/layout/AppLayout', () => ({
  AppLayout: ({ children }) => <div data-testid="app-layout">{children}</div>,
}));

vi.mock('./chat/EmptyState', () => ({
  EmptyState: () => (
    <main>
      <h2>Hi! I&apos;m Syra</h2>
      <p>Ask me anything about your syllabus.</p>
    </main>
  ),
}));

vi.mock('./chat/InputBar', () => ({
  InputBar: () => (
    <div role="region" aria-label="Message input">
      <label htmlFor="chat-input-stub">Message</label>
      <input id="chat-input-stub" type="text" />
    </div>
  ),
}));

vi.mock('./chat/ModelSelector', () => ({
  ModelSelector: () => <div />,
  MODELS: [{ id: 'gemini-flash', label: 'Gemini Flash' }],
}));

vi.mock('./chat/MessageBubble', () => ({
  MessageBubble: ({ msg }) => <div role="article" aria-label={`${msg.role} message`}>{msg.content}</div>,
}));

const mockGetAnonConversation = vi.fn(() => new Promise(() => {}));
const mockGetConversation     = vi.fn(() => new Promise(() => {}));
vi.mock('@/utils/api', () => ({
  getConversation:     (...args) => mockGetConversation(...args),
  getAnonConversation: (...args) => mockGetAnonConversation(...args),
  getSubject:          vi.fn(() => new Promise(() => {})),
  getChapters:         vi.fn(() => new Promise(() => {})),
  API_BASE:            'http://localhost',
  apiClient:           () => ({
    get:  vi.fn(() => new Promise(() => {})),
    post: vi.fn(() => new Promise(() => {})),
  }),
  getAnonId: vi.fn(() => 'anon-test-id'),
}));

vi.mock('@/utils/analytics', () => ({
  Analytics: { page: vi.fn(), event: vi.fn(), identify: vi.fn() },
}));

vi.mock('@/utils/firebasePerf', () => ({
  startTrace:      vi.fn(() => ({ finish: vi.fn() })),
  makeTraceparent: vi.fn(() => '00-abc123-def456-01'),
}));

vi.mock('@/hooks/useHashScroll', () => ({
  useHashScroll: vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

import ChatPage from './ChatPage';

beforeEach(() => {
  vi.useRealTimers();
  HTMLElement.prototype.scrollIntoView = vi.fn();
  mockSearchParams.mockReturnValue([new URLSearchParams(), vi.fn()]);
  mockGetAnonConversation.mockReturnValue(new Promise(() => {}));
  mockGetConversation.mockReturnValue(new Promise(() => {}));
});

afterEach(() => {
  vi.restoreAllMocks();
});

const SAMPLE_MESSAGES = [
  { id: 'm1', role: 'user',      content: 'What is photosynthesis?' },
  { id: 'm2', role: 'assistant', content: 'Photosynthesis is the process by which plants make food using sunlight.' },
];

describe('ChatPage — axe accessibility audit', () => {
  it('has no axe violations for an anonymous user (empty chat, no conversation)', async () => {
    let container;
    await act(async () => {
      ({ container } = render(<ChatPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations for an authenticated user (empty chat, no conversation)', async () => {
    mockUseAuth.mockReturnValueOnce({
      user:        { id: 'u1', email: 'student@example.com', credits_used: 0, credits_limit: 50 },
      authChecked: true,
    });

    let container;
    await act(async () => {
      ({ container } = render(<ChatPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations when a conversation with messages is loaded', async () => {
    mockSearchParams.mockReturnValue([new URLSearchParams('id=conv-123'), vi.fn()]);
    mockGetAnonConversation.mockResolvedValue({
      data: { id: 'conv-123', messages: SAMPLE_MESSAGES },
    });

    let container;
    await act(async () => {
      ({ container } = render(<ChatPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
