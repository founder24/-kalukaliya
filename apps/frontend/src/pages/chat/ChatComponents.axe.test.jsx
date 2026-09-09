import { render } from '@testing-library/react';
import { axe, toHaveNoViolations } from 'jest-axe';
import { expect, it, vi } from 'vitest';

import { InputBar } from './InputBar';
import { MessageBubble } from './MessageBubble';

expect.extend(toHaveNoViolations);

vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock('@/utils/api', () => ({
  API_BASE: '/api/v1',
  getAnonId: () => null,
  postChatFeedback: vi.fn(),
  eduRequestSite: vi.fn(),
}));
vi.mock('@/hooks/useTTS', () => ({ getTTSLang: () => 'en' }));
vi.mock('@/hooks/useShare', () => ({ useShare: () => ({ share: vi.fn(), canShare: false }) }));
vi.mock('@/components/study/MicButton', () => ({ MicButton: () => null }));
vi.mock('@/components/study/ReadAloudButton', () => ({ ReadAloudButton: () => null }));
vi.mock('@/components/study/QuizModal', () => ({ QuizModal: () => null }));

it('has no axe violations in the real composer and partial-error message', async () => {
  const { container } = render(
    <>
      <MessageBubble
        msg={{
          id: 'partial',
          role: 'assistant',
          content: 'A useful partial answer.',
          isAiUnavailable: true,
          isConnectionInterrupted: true,
          isPartialResponse: true,
          autoRetryScheduled: false,
        }}
        onRetry={vi.fn()}
        isLast
        messageIndex={0}
        responseLang="en"
        scopedChapters={[]}
      />
      <InputBar
        subject={null}
        messages={[]}
        scopedChapters={[]}
        input=""
        setInput={vi.fn()}
        isLoading={false}
        isOutOfCredits={false}
        isLow={false}
        credits={0}
        effectiveLimit={6}
        remaining={6}
        creditPercent={0}
        textareaRef={{ current: null }}
        adjustTextarea={vi.fn()}
        sendMsg={vi.fn()}
        handleStop={vi.fn()}
        isAnon
        activeChapter={null}
        onDismissChapter={vi.fn()}
      />
    </>,
  );

  expect(await axe(container)).toHaveNoViolations();
});