import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { MessageBubble } from './MessageBubble';

vi.mock('@/utils/api', () => ({
  postChatFeedback: vi.fn(),
  eduRequestSite: vi.fn(),
}));
vi.mock('@/utils/logger', () => ({ log: Object.assign(vi.fn(), { error: vi.fn() }) }));
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock('@/hooks/useShare', () => ({ useShare: () => ({ share: vi.fn() }) }));
vi.mock('@/components/study/ReadAloudButton', () => ({ ReadAloudButton: () => null }));
vi.mock('@/components/study/QuizModal', () => ({ QuizModal: () => null }));

function renderBubble(props) {
  return render(
    <MemoryRouter>
      <MessageBubble
        msg={{ id: 'a1', role: 'assistant', content: '', isAiUnavailable: true, retryText: 'কি ফটোসিন্থেচিচ?' }}
        isLast
        responseLang="as"
        {...props}
      />
    </MemoryRouter>,
  );
}

describe('MessageBubble — Assamese chat unavailable card (Task #370)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders the localized Assamese error card when isAssameseUnavailable is true', () => {
    renderBubble({
      msg: { id: 'a1', role: 'assistant', content: '', isAiUnavailable: true, isAssameseUnavailable: true, retryText: 'কি ফটোসিন্থেচিচ?' },
      onSwitchToEnglish: vi.fn(),
      onRetry: vi.fn(),
    });

    expect(screen.getByTestId('assamese-unavailable-card')).toBeTruthy();
    expect(screen.queryByTestId('ai-unavailable-card')).toBeNull();
    expect(screen.getByText(/অসমীয়া চেট সেৱা সাময়িকভাৱে অনুপলব্ধ/)).toBeTruthy();
    expect(screen.getByTestId('assamese-switch-english')).toBeTruthy();
    expect(screen.queryByText(/Auto-retry in/)).toBeNull();
  });

  it('falls back to the generic English card when isAssameseUnavailable is false', () => {
    renderBubble({
      msg: { id: 'a2', role: 'assistant', content: '', isAiUnavailable: true, retryText: 'hi' },
      onRetry: vi.fn(),
    });

    expect(screen.getByTestId('ai-unavailable-card')).toBeTruthy();
    expect(screen.queryByTestId('assamese-unavailable-card')).toBeNull();
    expect(screen.queryByTestId('assamese-switch-english')).toBeNull();
    expect(screen.getByText(/Syra is resting/)).toBeTruthy();
  });

  it('invokes onSwitchToEnglish when the user clicks the switch button', () => {
    const onSwitchToEnglish = vi.fn();
    renderBubble({
      msg: { id: 'a3', role: 'assistant', content: '', isAiUnavailable: true, isAssameseUnavailable: true, retryText: 'কি ফটোসিন্থেচিচ?' },
      onSwitchToEnglish,
      onRetry: vi.fn(),
    });

    fireEvent.click(screen.getByTestId('assamese-switch-english'));
    expect(onSwitchToEnglish).toHaveBeenCalledTimes(1);
  });

  it('hides the switch button when no onSwitchToEnglish callback is wired', () => {
    renderBubble({
      msg: { id: 'a4', role: 'assistant', content: '', isAiUnavailable: true, isAssameseUnavailable: true, retryText: 'কি ফটোসিন্থেচিচ?' },
      onRetry: vi.fn(),
    });

    expect(screen.queryByTestId('assamese-switch-english')).toBeNull();
  });
});
