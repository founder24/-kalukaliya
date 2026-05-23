/**
 * plans.js — Single source of truth for plan configuration.
 * Mirrors backend PLAN_LIMITS + PLAN_PRICES exactly.
 * Import this everywhere: ProfilePage, PricingPage, LandingPage, AdminPlans.
 */

const PLANS = {
  free: {
    id: 'free',
    label: 'Free',
    credits: 30,
    creditsLabel: '30/day',
    price: '₹0',
    period: '/month',
    documentAccess: 'Zero document access',
    description: 'Get started, no card needed',
    features: [
      '30 AI credits/day',
      '5 messages/min',
      'All subjects access',
      'Chat history (limited)',
      'Basic AI model',
      'Zero document access',
    ],
    maxTokens: 512,
    badge: null,
    highlighted: false,
    creditsColor: 'text-slate-400',
    badgeStyle: {
      bg: 'rgba(100,116,139,0.12)',
      color: '#94a3b8',
      border: '1px solid rgba(100,116,139,0.25)',
    },
  },
  starter: {
    id: 'starter',
    label: 'Starter',
    credits: 500,
    creditsLabel: '500/day',
    price: '₹99',
    period: 'one-time',
    documentAccess: 'Limited document access',
    description: 'Best for regular students',
    features: [
      '500 AI credits/day',
      '10 messages/min',
      'All subjects access',
      'Full chat history',
      'Advanced AI models',
      'Limited document access',
      'Priority responses',
    ],
    maxTokens: 768,
    badge: 'MOST POPULAR',
    highlighted: true,
    creditsColor: 'text-violet-400',
    badgeStyle: {
      bg: 'rgba(139,92,246,0.18)',
      color: '#a78bfa',
      border: '1px solid rgba(139,92,246,0.35)',
    },
  },
  pro: {
    id: 'pro',
    label: 'Pro',
    credits: 4000,
    creditsLabel: '4,000/day',
    price: '₹999',
    period: 'one-time',
    documentAccess: 'Full document access',
    description: 'For serious exam prep',
    features: [
      '4,000 AI credits/day',
      '15 messages/min',
      'Unlimited subjects access',
      'Unlimited history',
      'All AI models (fastest)',
      'Full document access',
      'Early access to features',
    ],
    maxTokens: 1024,
    badge: 'BEST VALUE',
    highlighted: false,
    creditsColor: 'text-amber-400',
    badgeStyle: {
      bg: 'rgba(245,158,11,0.15)',
      color: '#f59e0b',
      border: '1px solid rgba(245,158,11,0.30)',
    },
  },
};

/** Document access labels with icon color */
export const DOC_ACCESS_CONFIG = {
  zero:    { label: 'Zero document access',    color: 'text-slate-400',  icon: '🔒' },
  limited: { label: 'Limited document access', color: 'text-violet-400', icon: '📄' },
  full:    { label: 'Full document access',    color: 'text-emerald-400',icon: '📚' },
};
