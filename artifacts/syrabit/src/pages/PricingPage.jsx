import { useNavigate } from 'react-router-dom';
import { PublicLayout } from '@/components/layout/PublicLayout';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Check, X, Zap, Trophy, Sparkles } from 'lucide-react';
import { DOC_ACCESS_CONFIG } from '@/utils/plans';
import { useAuth } from '@/context/AuthContext';
import PageMeta from '@/components/seo/PageMeta';
import { useContentLang } from '@/context/LanguageContext';
import LangToggle from '@/components/ui/LangToggle';

const faqJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'FAQPage',
  mainEntity: [
    {
      '@type': 'Question',
      name: 'Is Syrabit.ai free to use?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Yes, Syrabit.ai offers a free plan with 30 AI credits per day, access to all subjects, and chat history — no credit card required. You can upgrade to Starter (₹99 one-time) for 500 credits/day or Pro (₹999 one-time) for 4,000 credits/day whenever you need more.',
      },
    },
    {
      '@type': 'Question',
      name: 'How much does Syrabit.ai cost?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Syrabit.ai has three plans: Free (₹0, 30 credits/day), Starter (₹99 one-time payment, 500 credits/day with priority responses and advanced AI models), and Pro (₹999 one-time payment, 4,000 credits/day with all AI models, full document access, and early access to new features). There are no subscriptions or recurring charges.',
      },
    },
    {
      '@type': 'Question',
      name: 'What are credits on Syrabit.ai?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Each AI response on Syrabit.ai costs 1 credit. Free users get 30 credits per day, Starter users get 500 credits per day, and Pro users get 4,000 credits per day. All credits reset daily at midnight UTC, giving you a fresh allowance every day based on your plan.',
      },
    },
    {
      '@type': 'Question',
      name: 'What payment methods does Syrabit.ai accept?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Syrabit.ai accepts payments through Razorpay, supporting UPI, debit cards, credit cards, net banking, and wallets. Both Starter (₹99) and Pro (₹999) are one-time payments with no recurring subscriptions or lock-in.',
      },
    },
    {
      '@type': 'Question',
      name: 'Can I upgrade my Syrabit.ai plan later?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Yes, you can upgrade your plan at any time. Your existing credits are preserved when you upgrade, and the new plan activates immediately. You can start with the free plan and move to Starter or Pro whenever you need more credits, priority responses, or advanced AI models.',
      },
    },
  ],
};

const PLANS = {
  en: [
    {
      id: 'free',
      icon: Zap,
      name: 'Free',
      price: '0',
      period: 'forever',
      credits: '30 credits/day',
      docAccess: 'zero',
      description: 'Start exploring, no card needed',
      features: [
        { label: '30 AI credits/day',           included: true  },
        { label: '5 messages/min',              included: true  },
        { label: 'All subjects access',         included: true  },
        { label: 'Chat history (limited)',      included: true  },
        { label: 'Zero document access',        included: true  },
        { label: 'Priority responses',          included: false },
        { label: 'Advanced AI models',          included: false },
      ],
      cta: 'Get Started Free',
      ctaPath: '/signup',
      highlighted: false,
      isPaid: false,
    },
    {
      id: 'starter',
      icon: Trophy,
      name: 'Starter',
      price: '99',
      period: 'one-time',
      credits: '500 credits/day',
      docAccess: 'limited',
      description: 'Best for regular students',
      badge: 'MOST POPULAR',
      features: [
        { label: '500 AI credits/day',         included: true  },
        { label: '10 messages/min',            included: true  },
        { label: 'All subjects access',        included: true  },
        { label: 'Full chat history',          included: true  },
        { label: 'Limited document access',    included: true  },
        { label: 'Priority responses',         included: true  },
        { label: 'Advanced AI models',         included: true  },
      ],
      cta: 'Buy Starter — ₹99',
      ctaPath: '/signup',
      highlighted: true,
      isPaid: true,
    },
    {
      id: 'pro',
      icon: Sparkles,
      name: 'Pro',
      price: '999',
      period: 'one-time',
      credits: '4,000 credits/day',
      docAccess: 'full',
      description: 'For serious exam prep',
      badge: 'BEST VALUE',
      features: [
        { label: '4,000 AI credits/day',       included: true  },
        { label: '15 messages/min',            included: true  },
        { label: 'Unlimited subjects access',  included: true  },
        { label: 'Unlimited history',          included: true  },
        { label: 'Full document access',       included: true  },
        { label: 'All AI models (fastest)',    included: true  },
        { label: 'Early access to features',   included: true  },
      ],
      cta: 'Go Pro — ₹999',
      ctaPath: '/signup',
      highlighted: false,
      isPaid: true,
    },
  ],
  as: [
    {
      id: 'free',
      icon: Zap,
      name: 'বিনামূলীয়া',
      price: '0',
      period: 'চিৰকাললৈ',
      credits: '30 ক্ৰেডিট/দিন',
      docAccess: 'zero',
      description: 'অন্বেষণ আৰম্ভ কৰক, কাৰ্ড নালাগে',
      features: [
        { label: '30 AI ক্ৰেডিট/দিন',              included: true  },
        { label: '5 বাৰ্তা/মিনিট',                 included: true  },
        { label: 'সকলো বিষয়ৰ একচেছ',              included: true  },
        { label: 'চেট ইতিহাস (সীমিত)',             included: true  },
        { label: 'শূন্য নথি একচেছ',                included: true  },
        { label: 'অগ্ৰাধিকাৰ উত্তৰ',               included: false },
        { label: 'উন্নত AI মডেল',                  included: false },
      ],
      cta: 'বিনামূলীয়াকৈ আৰম্ভ কৰক',
      ctaPath: '/signup',
      highlighted: false,
      isPaid: false,
    },
    {
      id: 'starter',
      icon: Trophy,
      name: 'ষ্টাৰ্টাৰ',
      price: '99',
      period: 'এককালীন',
      credits: '500 ক্ৰেডিট/দিন',
      docAccess: 'limited',
      description: 'নিয়মীয়া ছাত্ৰ-ছাত্ৰীৰ বাবে শ্ৰেষ্ঠ',
      badge: 'আটাইতকৈ জনপ্ৰিয়',
      features: [
        { label: '500 AI ক্ৰেডিট/দিন',             included: true  },
        { label: '10 বাৰ্তা/মিনিট',                included: true  },
        { label: 'সকলো বিষয়ৰ একচেছ',              included: true  },
        { label: 'সম্পূৰ্ণ চেট ইতিহাস',             included: true  },
        { label: 'সীমিত নথি একচেছ',                included: true  },
        { label: 'অগ্ৰাধিকাৰ উত্তৰ',               included: true  },
        { label: 'উন্নত AI মডেল',                  included: true  },
      ],
      cta: 'ষ্টাৰ্টাৰ কিনক — ₹99',
      ctaPath: '/signup',
      highlighted: true,
      isPaid: true,
    },
    {
      id: 'pro',
      icon: Sparkles,
      name: 'প্ৰ\'',
      price: '999',
      period: 'এককালীন',
      credits: '4,000 ক্ৰেডিট/দিন',
      docAccess: 'full',
      description: 'গুৰুতৰ পৰীক্ষাৰ প্ৰস্তুতিৰ বাবে',
      badge: 'শ্ৰেষ্ঠ মূল্য',
      features: [
        { label: '4,000 AI ক্ৰেডিট/দিন',           included: true  },
        { label: '15 বাৰ্তা/মিনিট',                included: true  },
        { label: 'সীমাহীন বিষয়ৰ একচেছ',            included: true  },
        { label: 'সীমাহীন ইতিহাস',                 included: true  },
        { label: 'সম্পূৰ্ণ নথি একচেছ',              included: true  },
        { label: 'সকলো AI মডেল (দ্ৰুততম)',         included: true  },
        { label: 'বৈশিষ্ট্যসমূহত আগতীয়া একচেছ',    included: true  },
      ],
      cta: 'প্ৰ\' লওক — ₹999',
      ctaPath: '/signup',
      highlighted: false,
      isPaid: true,
    },
  ],
};

const _t = {
  en: {
    badgeText: 'Simple Pricing',
    heading: 'Affordable exam prep',
    subheading: 'Start free, upgrade when you need more. No subscriptions, no lock-in.',
    signedInAs: 'Signed in as',
    upgradeNote: '— upgrading will activate immediately.',
    upgradeTo: 'Upgrade to',
    faqTitle: 'FAQs',
    faqs: [
      { q: 'What are credits?', a: 'Each AI response costs 1 credit. Free users get 30/day, Starter 500/day, Pro 4,000/day. All credits reset at midnight UTC.' },
      { q: 'Do credits expire?', a: 'All plan credits reset daily at midnight UTC. You get a fresh allowance every day based on your plan.' },
      { q: 'Can I upgrade later?', a: 'Yes! You can upgrade anytime. Your existing credits will be preserved.' },
      { q: 'Which AHSEC classes are supported?', a: 'We support both Class 11 and Class 12 for Science (PCM, PCB) and Arts streams.' },
    ],
    pageTitle: 'Pricing',
    pageDescription: 'Simple, affordable pricing for Syrabit.ai — the AI-powered study platform for AHSEC, SEBA, and Degree students in Assam. Start free with 30 credits/day or upgrade for more.',
  },
  as: {
    badgeText: 'সৰল মূল্য নিৰ্ধাৰণ',
    heading: 'সুলভ পৰীক্ষাৰ প্ৰস্তুতি',
    subheading: 'বিনামূলীয়াকৈ আৰম্ভ কৰক, প্ৰয়োজন হলে আপগ্ৰেড কৰক। কোনো চাবস্ক্ৰিপচন নাই, কোনো লক-ইন নাই।',
    signedInAs: 'হিচাপে চাইন ইন কৰা হৈছে',
    upgradeNote: '— আপগ্ৰেডিং তৎক্ষণাত সক্ৰিয় হ\'ব।',
    upgradeTo: 'লৈ আপগ্ৰেড কৰক',
    faqTitle: 'সঘনাই সোধা প্ৰশ্ন',
    faqs: [
      { q: 'ক্ৰেডিট কি?', a: 'প্ৰতিটো AI উত্তৰত 1 ক্ৰেডিট খৰচ হয়। বিনামূলীয়া ব্যৱহাৰকাৰীয়ে 30/দিন, ষ্টাৰ্টাৰে 500/দিন, প্ৰ\'ই 4,000/দিন পায়। সকলো ক্ৰেডিট মাজনিশা UTC ত ৰিছেট হয়।' },
      { q: 'ক্ৰেডিটৰ ম্যাদ উকলে নেকি?', a: 'সকলো পৰিকল্পনাৰ ক্ৰেডিট দৈনিক মাজনিশা UTC ত ৰিছেট হয়। আপুনি আপোনাৰ পৰিকল্পনাৰ ওপৰত ভিত্তি কৰি প্ৰতিদিনে এটা নতুন বৰাদ্দ পায়।' },
      { q: 'পিছত আপগ্ৰেড কৰিব পাৰিমনে?', a: 'হয়! আপুনি যিকোনো সময়তে আপগ্ৰেড কৰিব পাৰে। আপোনাৰ বৰ্তমানৰ ক্ৰেডিট সংৰক্ষিত থাকিব।' },
      { q: 'কোন AHSEC শ্ৰেণী সমৰ্থিত?', a: 'আমি বিজ্ঞান (PCM, PCB) আৰু কলা শাখাৰ বাবে শ্ৰেণী 11 আৰু শ্ৰেণী 12 দুয়োটাকে সমৰ্থন কৰোঁ।' },
    ],
    pageTitle: 'মূল্য নিৰ্ধাৰণ',
    pageDescription: 'Syrabit.ai ৰ বাবে সৰল, সুলভ মূল্য নিৰ্ধাৰণ — অসমৰ AHSEC, SEBA, আৰু ডিগ্ৰী ছাত্ৰ-ছাত্ৰীৰ বাবে AI-চালিত অধ্যয়ন মঞ্চ। 30 ক্ৰেডিট/দিনৰ সৈতে বিনামূলীয়াকৈ আৰম্ভ কৰক বা অধিকৰ বাবে আপগ্ৰেড কৰক।',
  },
};

function getJsonLd(lang) {
  return {
    '@context': 'https://schema.org',
    '@type': 'WebPage',
    name: lang === 'as' ? 'মূল্য নিৰ্ধাৰণ — Syrabit.ai' : 'Pricing — Syrabit.ai',
    url: 'https://syrabit.ai/pricing',
    inLanguage: lang === 'as' ? ['as', 'en'] : ['en', 'as'],
    description: lang === 'as'
      ? 'Syrabit.ai ৰ বাবে সৰল, সুলভ মূল্য নিৰ্ধাৰণ। 30 ক্ৰেডিট/দিনৰ সৈতে বিনামূলীয়াকৈ আৰম্ভ কৰক।'
      : 'Simple, affordable pricing for Syrabit.ai. Start free with 30 credits/day.',
    isPartOf: {
      '@type': 'WebSite',
      name: 'Syrabit.ai',
      url: 'https://syrabit.ai',
    },
    mainEntity: {
      '@type': 'AggregateOffer',
      priceCurrency: 'INR',
      lowPrice: '0',
      highPrice: '999',
      offerCount: '3',
      offers: [
        { '@type': 'Offer', name: 'Free Plan', price: '0', priceCurrency: 'INR', description: '30 credits/day' },
        { '@type': 'Offer', name: 'Starter Plan', price: '99', priceCurrency: 'INR', description: '500 credits/day' },
        { '@type': 'Offer', name: 'Pro Plan', price: '999', priceCurrency: 'INR', description: '4,000 credits/day' },
      ],
    },
  };
}

export default function PricingPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { contentLang, switchLang } = useContentLang();
  const t = _t[contentLang] || _t.en;
  const plans = PLANS[contentLang] || PLANS.en;
  const jsonLd = getJsonLd(contentLang);

  const handleCtaClick = (plan) => {
    if (!plan.isPaid) {
      navigate(user ? '/chat' : '/signup');
      return;
    }
    if (user) {
      navigate(`/profile?upgrade=${plan.id}`);
    } else {
      navigate('/signup');
    }
  };

  return (
    <PublicLayout>
      <PageMeta
        title={t.pageTitle}
        description={t.pageDescription}
        url="https://syrabit.ai/pricing"
        keywords="Syrabit pricing, AI education pricing Assam, AHSEC study credits, Syrabit মূল্য, অসমীয়া AI শিক্ষা মঞ্চ মূল্য"
        jsonLd={[jsonLd, faqJsonLd]}
      />
      <div className="min-h-screen pt-8 pb-24 px-4">
        <div className="text-center max-w-2xl mx-auto mb-16">
          <div className="flex justify-center mb-4">
            <LangToggle contentLang={contentLang} switchLang={switchLang} />
          </div>
          <Badge className="bg-violet-500/10 text-violet-600 border-violet-500/25 mb-4">
            {t.badgeText}
          </Badge>
          <h1 className="text-3xl sm:text-5xl font-semibold text-foreground mb-4">
            {t.heading}
          </h1>
          <p className="text-muted-foreground text-lg">
            {t.subheading}
          </p>
        </div>

        <div className="max-w-5xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6" data-testid="pricing-page">
          {plans.map((plan) => {
            const Icon = plan.icon;
            return (
              <div
                key={plan.id}
                className={`relative rounded-2xl p-6 glass-card ${
                  plan.highlighted
                    ? 'ring-1 ring-violet-500/40 shadow-[0_26px_90px_rgba(124,58,237,0.10)]'
                    : ''
                }`}
                data-testid="pricing-plan-card"
              >
                {plan.badge && (
                  <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                    <span className="text-xs font-bold text-white bg-violet-600 border border-violet-500/40 px-3 py-1 rounded-full">
                      {plan.badge}
                    </span>
                  </div>
                )}

                <div className="flex items-center gap-3 mb-4">
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${
                    plan.highlighted ? 'bg-violet-600/15' : 'bg-muted'
                  }`}>
                    <Icon size={20} className={plan.highlighted ? 'text-violet-600' : 'text-muted-foreground'} />
                  </div>
                  <div>
                    <h3 className="text-foreground font-semibold">{plan.name}</h3>
                    <p className="text-muted-foreground text-xs">{plan.description}</p>
                  </div>
                </div>

                <div className="mb-6">
                  <div className="flex items-baseline gap-1">
                    <span className="text-muted-foreground text-sm">₹</span>
                    <span className="text-4xl font-bold text-foreground">{plan.price}</span>
                    <span className="text-muted-foreground text-sm">{plan.period}</span>
                  </div>
                  <p className="text-violet-600 text-sm font-semibold mt-1">{plan.credits}</p>
                  {plan.docAccess && (() => {
                    const da = DOC_ACCESS_CONFIG[plan.docAccess];
                    return da ? (
                      <p className={`text-xs font-medium mt-1 ${da.color}`}>
                        {da.icon} {da.label}
                      </p>
                    ) : null;
                  })()}
                </div>

                <ul className="space-y-3 mb-8">
                  {plan.features.map((feature) => (
                    <li key={feature.label} className="flex items-center gap-2.5">
                      {feature.included ? (
                        <Check size={16} className="text-emerald-500 flex-shrink-0" />
                      ) : (
                        <X size={16} className="text-muted-foreground/30 flex-shrink-0" />
                      )}
                      <span className={`text-sm ${
                        feature.included ? 'text-foreground/80' : 'text-muted-foreground'
                      }`}>{feature.label}</span>
                    </li>
                  ))}
                </ul>

                <Button
                  onClick={() => handleCtaClick(plan)}
                  className={`w-full ${
                    plan.highlighted
                      ? 'bg-violet-600 hover:bg-violet-500 text-white shadow-lg shadow-violet-500/20'
                      : 'bg-muted hover:bg-muted/80 text-foreground border border-border/30'
                  }`}
                  data-testid={`pricing-${plan.id}-cta-button`}
                >
                  {plan.isPaid && user ? `${t.upgradeTo} ${plan.name} →` : plan.cta}
                </Button>
              </div>
            );
          })}
        </div>

        {user && (
          <p className="text-center text-muted-foreground text-sm mt-8">
            {t.signedInAs} <span className="text-violet-600">{user.email}</span> {t.upgradeNote}
          </p>
        )}

        <div className="max-w-2xl mx-auto mt-20">
          <h2 className="text-2xl font-semibold text-foreground text-center mb-8">{t.faqTitle}</h2>
          <div className="space-y-4">
            {t.faqs.map(({ q, a }) => (
              <div key={q} className="glass-card rounded-xl p-5">
                <h3 className="text-foreground font-medium mb-2">{q}</h3>
                <p className="text-muted-foreground text-sm">{a}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </PublicLayout>
  );
}
