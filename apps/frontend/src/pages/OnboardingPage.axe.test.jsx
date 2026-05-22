/**
 * Task #194 / Task #198 — OnboardingPage: axe audit + step-indicator ARIA.
 *
 * Covers:
 *  - axe audit on two key render states (loading spinner, boards loaded)
 *  - Step-indicator structural assertions: nav landmark, aria-label on each
 *    step item, aria-current="step" on the active step (Task #198)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { axe, toHaveNoViolations } from 'jest-axe';
import { render, screen, act } from '@testing-library/react';
import React from 'react';

expect.extend(toHaveNoViolations);

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

const mockRefreshUser = vi.fn();
const mockUpdateUser  = vi.fn();
const mockLogout      = vi.fn();

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({
    user:              { email: 'test@example.com', onboarding_done: false },
    authChecked:       true,
    justAuthenticated: { current: true },
    refreshUser:       mockRefreshUser,
    updateUser:        mockUpdateUser,
    logout:            mockLogout,
  }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('@/components/Logo', () => ({
  LogoMark: () => <div data-testid="logo-mark" aria-hidden="true" />,
}));

const mockGetBoards = vi.fn();
vi.mock('@/utils/api', () => ({
  getBoards:      (...args) => mockGetBoards(...args),
  getClasses:     vi.fn().mockResolvedValue({ data: [] }),
  getStreams:      vi.fn().mockResolvedValue({ data: [] }),
  saveOnboarding: vi.fn().mockResolvedValue({}),
}));

import OnboardingPage from './OnboardingPage';

const SAMPLE_BOARDS = [
  { id: 'b1', name: 'AHSEC', description: 'Assam Higher Secondary Education Council' },
  { id: 'b2', name: 'SEBA', description: 'Board of Secondary Education, Assam' },
];

beforeEach(() => {
  mockNavigate.mockClear();
  mockGetBoards.mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('OnboardingPage — axe accessibility audit', () => {
  it('has no axe violations while boards are loading (spinner state)', async () => {
    mockGetBoards.mockReturnValue(new Promise(() => {}));
    let container;
    await act(async () => {
      ({ container } = render(<OnboardingPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations when boards have loaded (board-selection step)', async () => {
    mockGetBoards.mockResolvedValue({ data: SAMPLE_BOARDS });
    let container;
    await act(async () => {
      ({ container } = render(<OnboardingPage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});

describe('OnboardingPage — step indicator ARIA structure (Task #198)', () => {
  it('renders a nav landmark with aria-label="Onboarding progress"', async () => {
    mockGetBoards.mockResolvedValue({ data: SAMPLE_BOARDS });
    await act(async () => { render(<OnboardingPage />); });
    expect(screen.getByRole('navigation', { name: 'Onboarding progress' })).toBeInTheDocument();
  });

  it('gives each step item an aria-label describing its number, name, and status', async () => {
    mockGetBoards.mockResolvedValue({ data: SAMPLE_BOARDS });
    await act(async () => { render(<OnboardingPage />); });
    expect(screen.getByRole('listitem', { name: /Step 1 of 3: Board – current/i })).toBeInTheDocument();
    expect(screen.getByRole('listitem', { name: /Step 2 of 3: Class – upcoming/i })).toBeInTheDocument();
    expect(screen.getByRole('listitem', { name: /Step 3 of 3: Stream – upcoming/i })).toBeInTheDocument();
  });

  it('sets aria-current="step" only on the active step item', async () => {
    mockGetBoards.mockResolvedValue({ data: SAMPLE_BOARDS });
    await act(async () => { render(<OnboardingPage />); });
    const currentItem = screen.getByRole('listitem', { name: /Board – current/i });
    expect(currentItem).toHaveAttribute('aria-current', 'step');
    const upcomingItems = screen.getAllByRole('listitem', { name: /upcoming/i });
    upcomingItems.forEach((item) => expect(item).not.toHaveAttribute('aria-current'));
  });
});
