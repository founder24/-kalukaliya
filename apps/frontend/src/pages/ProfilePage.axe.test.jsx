/**
 * Task #194 — ProfilePage: axe accessibility audit.
 *
 * Audits the two early-return render states that exist before any of the
 * complex sub-components (ProfileHeader, AiCredits, etc.) are mounted:
 *
 *  1. user = null  →  "Sign in to view your profile" prompt
 *  2. user set, API pending  →  animated loading skeleton
 *
 * Both are high-traffic states (first impression for unauthenticated visitors
 * and the blank-screen gap between auth and data load) and use no profile
 * sub-components, so the mock surface is minimal and the audit is reliable.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { axe, toHaveNoViolations } from 'jest-axe';
import { render, act, screen } from '@testing-library/react';
import React from 'react';

expect.extend(toHaveNoViolations);

const mockNavigate = vi.fn();
const mockSetSearchParams = vi.fn();
const mockApiGet = vi.fn();

vi.mock('react-router-dom', () => ({
  useNavigate:      () => mockNavigate,
  useSearchParams:  () => [new URLSearchParams(), mockSetSearchParams],
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('@/utils/analytics', () => ({
  Analytics: {
    pageView:          vi.fn(),
    purchaseComplete:  vi.fn(),
    upgradeInitiated:  vi.fn(),
  },
}));

vi.mock('@/utils/api', () => ({
  apiClient:           () => ({ get: mockApiGet }),
  createPaymentOrder:  vi.fn(),
  verifyPayment:       vi.fn(),
  createCreditTopUp:   vi.fn(),
  verifyCreditTopUp:   vi.fn(),
}));

vi.mock('@/utils/adsConfig', () => ({
  hydrateAdsOptOutFromServer: vi.fn(),
}));

vi.mock('@/components/layout/AppLayout', () => ({
  AppLayout: ({ children }) => <main>{children}</main>,
}));

vi.mock('@/components/PageTitle', () => ({
  PageTitle: () => null,
}));

vi.mock('./profile/shared', () => ({
  PLANS:         { starter: { label: 'Starter', credits: 500 }, pro: { label: 'Pro', credits: 2000 } },
  PLAN_RANK:     { free: 0, starter: 1, pro: 2 },
  PLAN_FEATURES: {},
  TOPUP_OPTIONS: [],
  loadRazorpay:  vi.fn().mockResolvedValue(true),
  StarRating:    () => null,
  UsageDots:     () => null,
}));

vi.mock('./profile/ProfileHeader',        () => ({ default: () => <div data-testid="profile-header" /> }));
vi.mock('./profile/AcademicDetails',      () => ({ default: () => <div data-testid="academic-details" /> }));
vi.mock('./profile/AiCredits',            () => ({ default: () => <div data-testid="ai-credits" /> }));
vi.mock('./profile/SubscriptionPlans',    () => ({ default: () => <div data-testid="subscription-plans" /> }));
vi.mock('./profile/DangerZone',           () => ({
  default:        () => <div data-testid="danger-zone" />,
  DeletionBanner: () => <div data-testid="deletion-banner" />,
}));
vi.mock('./profile/PrivacyControls',      () => ({ default: () => <div data-testid="privacy-controls" /> }));
vi.mock('./profile/ReferralProfileCard',   () => ({ default: () => <div data-testid="referral-profile-card" /> }));
vi.mock('./profile/EditFieldDialog',      () => ({ default: () => null }));
vi.mock('./profile/DeleteConfirmDialog',  () => ({ default: () => null }));
vi.mock('./profile/PaymentModal',         () => ({ default: () => null }));
vi.mock('./profile/TopUpModal',           () => ({ default: () => null }));
vi.mock('./profile/PaymentHistory',       () => ({ default: () => <div data-testid="payment-history" /> }));

let mockUser = null;

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => {
    const [user, setUser] = React.useState(mockUser);
    const updateUser = React.useCallback((updates) => {
      setUser((previous) => (previous ? { ...previous, ...updates } : previous));
    }, []);
    return { user, updateUser, refreshUser: vi.fn() };
  },
}));

import ProfilePage from './ProfilePage';

beforeEach(() => {
  mockUser = null;
  mockNavigate.mockClear();
  mockApiGet.mockReset();
  mockApiGet.mockImplementation(() => new Promise(() => {}));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ProfilePage — axe accessibility audit', () => {
  it('has no axe violations on the sign-in prompt (user not authenticated)', async () => {
    mockUser = null;
    let container;
    await act(async () => {
      ({ container } = render(<ProfilePage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations on the loading skeleton (user authenticated, data pending)', async () => {
    mockUser = { email: 'test@example.com', role: 'user', plan: 'free', credits_remaining: 30 };
    let container;
    await act(async () => {
      ({ container } = render(<ProfilePage />));
    });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('does not refetch forever when synchronizing the loaded profile to auth state', async () => {
    mockUser = { id: 'staging-user', email: 'student@example.invalid', role: 'user' };
    mockApiGet.mockImplementation((path) => Promise.resolve({
      data: path === '/user/profile'
        ? { id: 'staging-user', name: 'Staging Gate Student', plan: 'free' }
        : { conversations: 0, saved_subjects: 0, total_tokens: 0, credits_used: 0 },
    }));

    await act(async () => {
      render(<ProfilePage />);
    });

    expect(await screen.findByTestId('profile-page')).toBeInTheDocument();
    expect(mockApiGet).toHaveBeenCalledTimes(2);
  });
});
