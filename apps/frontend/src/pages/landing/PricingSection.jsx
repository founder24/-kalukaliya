import { Link } from 'react-router-dom';
import { Zap, ArrowRight, Sparkles, CheckCircle, Trophy } from 'lucide-react';
import Reveal from './Reveal';
import GlowOrb from './GlowOrb';

const PLANS = {
  en: [
    {
      id: 'free', name: 'Free', price: '₹0', period: 'forever', credits: '30/day',
      renewal: '30 credits/day · resets daily', icon: Zap, highlighted: false, badge: null,
      docAccess: '🔒 Zero document access',
      features: ['30 AI credits/day', '5 messages/min', 'All subjects access', 'Chat history (limited)', 'Zero document access'],
      ctaText: 'Get Started Free',
    },
    {
      id: 'starter', name: 'Starter', price: '₹99', period: 'one-time', credits: '500/day',
      renewal: '500 credits/day · resets daily', icon: Trophy, highlighted: true, badge: 'MOST POPULAR',
      docAccess: '📄 Limited document access',
      features: ['500 AI credits/day', '10 messages/min', 'All subjects access', 'Full chat history', 'Limited document access', 'Priority responses'],
      ctaText: 'Buy Starter',
    },
    {
      id: 'pro', name: 'Pro', price: '₹999', period: 'one-time', credits: '4,000/day',
      renewal: '4,000 credits/day · resets daily', icon: Sparkles, highlighted: false, badge: 'BEST VALUE',
      docAccess: '📚 Full document access',
      features: ['4,000 AI credits/day', '15 messages/min', 'Unlimited subjects access', 'Unlimited history', 'Full document access', 'All AI models (fastest)', 'Early access to features'],
      ctaText: 'Go Pro',
    },
  ],
  as: [
    {
      id: 'free', name: 'বিনামূলীয়া', price: '₹০', period: 'চিৰদিনৰ বাবে', credits: '৩০/দিন',
      renewal: '৩০ ক্ৰেডিট/দিন · দৈনিক ৰিছেট', icon: Zap, highlighted: false, badge: null,
      docAccess: '🔒 নথি প্ৰৱেশ নাই',
      features: ['৩০ AI ক্ৰেডিট/দিন', '৫ বাৰ্তা/মিনিট', 'সকলো বিষয়ৰ প্ৰৱেশ', 'চেট ইতিহাস (সীমিত)', 'নথি প্ৰৱেশ নাই'],
      ctaText: 'বিনামূলীয়াকৈ আৰম্ভ কৰক',
    },
    {
      id: 'starter', name: 'Starter', price: '₹৯৯', period: 'এবাৰৰ বাবে', credits: '৫০০/দিন',
      renewal: '৫০০ ক্ৰেডিট/দিন · দৈনিক ৰিছেট', icon: Trophy, highlighted: true, badge: 'আটাইতকৈ জনপ্ৰিয়',
      docAccess: '📄 সীমিত নথি প্ৰৱেশ',
      features: ['৫০০ AI ক্ৰেডিট/দিন', '১০ বাৰ্তা/মিনিট', 'সকলো বিষয়ৰ প্ৰৱেশ', 'সম্পূৰ্ণ চেট ইতিহাস', 'সীমিত নথি প্ৰৱেশ', 'অগ্ৰাধিকাৰ উত্তৰ'],
      ctaText: 'Starter কিনক',
    },
    {
      id: 'pro', name: 'Pro', price: '₹৯৯৯', period: 'এবাৰৰ বাবে', credits: '৪,০০০/দিন',
      renewal: '৪,০০০ ক্ৰেডিট/দিন · দৈনিক ৰিছেট', icon: Sparkles, highlighted: false, badge: 'শ্ৰেষ্ঠ মূল্য',
      docAccess: '📚 সম্পূৰ্ণ নথি প্ৰৱেশ',
      features: ['৪,০০০ AI ক্ৰেডিট/দিন', '১৫ বাৰ্তা/মিনিট', 'সীমাহীন বিষয়ৰ প্ৰৱেশ', 'সীমাহীন ইতিহাস', 'সম্পূৰ্ণ নথি প্ৰৱেশ', 'সকলো AI মডেল (দ্ৰুততম)', 'নতুন সুবিধালৈ আগতীয়া প্ৰৱেশ'],
      ctaText: 'Pro লওক',
    },
  ],
};

const _t = {
  en: {
    badge: 'SIMPLE PRICING',
    heading: 'Start free. Scale as you need.',
    sub: 'No subscriptions. No hidden fees. All plans include daily credits that reset at midnight UTC.',
    footer: 'All credits reset daily at midnight UTC · fresh allowance every day',
  },
  as: {
    badge: 'সৰল মূল্য নিৰ্ধাৰণ',
    heading: 'বিনামূলীয়াকৈ আৰম্ভ কৰক। প্ৰয়োজন অনুসৰি বৃদ্ধি কৰক।',
    sub: 'কোনো চাবস্ক্ৰিপশ্বন নাই। কোনো লুকাই থকা মাচুল নাই। সকলো পৰিকল্পনাত দৈনিক ক্ৰেডিট অন্তৰ্ভুক্ত যি মাজনিশা UTC-ত ৰিছেট হয়।',
    footer: 'সকলো ক্ৰেডিট মাজনিশা UTC-ত দৈনিক ৰিছেট হয় · প্ৰতিদিন নতুন ভাট্টা',
  },
};

export default function PricingSection({ contentLang = 'en' }) {
  const t = _t[contentLang] || _t.en;
  const plans = PLANS[contentLang] || PLANS.en;

  return (
    <section id="pricing" className="py-28 relative overflow-hidden" style={{ background: 'hsl(var(--muted) / 0.15)' }}>
      <div className="absolute inset-0 pointer-events-none">
        <GlowOrb color="radial-gradient(circle,#7c3aed,transparent)" size={600} x="10%" y="30%" blur={120} opacity={0.05} animRange={20} duration={22} />
      </div>
      <div className="max-w-5xl mx-auto px-5 relative z-10">
        <Reveal className="text-center mb-14">
          <div
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full mb-5"
            style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(139,92,246,0.18)' }}
          >
            <Zap size={14} className="text-violet-600" />
            <span className="text-xs font-semibold tracking-widest text-violet-600">{t.badge}</span>
          </div>
          <h2 className="text-foreground mb-4" style={{ fontSize: 'clamp(1.8rem,4vw,2.8rem)', fontWeight: 800, letterSpacing: '-0.02em' }}>
            {t.heading}
          </h2>
          <p className="max-w-lg mx-auto text-muted-foreground">
            {t.sub}
          </p>
        </Reveal>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {plans.map((plan, i) => {
            const isPro = plan.id === 'pro';
            const priceColor = isPro ? '#d97706' : '#7c3aed';
            return (
              <Reveal
                key={plan.id}
                delay={i * 0.08}
                className="relative rounded-3xl p-7 flex flex-col transition-all duration-300 glass-card hover:-translate-y-1.5"
                data-testid="pricing-plan-card"
                style={
                  plan.highlighted
                    ? {
                        border: '1px solid rgba(139,92,246,0.30)',
                        boxShadow: '0 0 50px rgba(139,92,246,0.08)',
                      }
                    : undefined
                }
              >
                {plan.badge && (
                  <div
                    className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full"
                    style={{
                      fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
                      ...(plan.highlighted
                        ? { background: 'rgba(139,92,246,0.15)', color: '#7c3aed', border: '1px solid rgba(139,92,246,0.25)' }
                        : { background: 'rgba(245,158,11,0.10)', color: '#d97706', border: '1px solid rgba(245,158,11,0.25)' }),
                    }}
                  >
                    {plan.badge}
                  </div>
                )}
                <div className="flex items-center gap-3 mb-5">
                  <div
                    className="w-10 h-10 rounded-xl flex items-center justify-center"
                    style={{ background: isPro ? 'rgba(245,158,11,0.08)' : 'rgba(124,58,237,0.08)' }}
                  >
                    <plan.icon className="w-5 h-5" style={{ color: priceColor }} />
                  </div>
                  <div>
                    <p className="text-foreground" style={{ fontWeight: 700 }}>{plan.name}</p>
                    <p className="text-xs text-muted-foreground">{plan.credits} · {plan.renewal}</p>
                  </div>
                </div>
                <div className="mb-5">
                  <span style={{ fontSize: '2.2rem', fontWeight: 800, color: priceColor }}>{plan.price}</span>
                  <span className="text-sm ml-1 text-muted-foreground">{plan.period}</span>
                  {plan.docAccess && (
                    <p className="text-xs font-medium mt-1.5" style={{ color: isPro ? '#059669' : plan.highlighted ? '#7c3aed' : '#64748b' }}>
                      {plan.docAccess}
                    </p>
                  )}
                </div>
                <ul className="space-y-2.5 mb-8 flex-1">
                  {plan.features.map((feat) => (
                    <li key={feat} className="flex items-center gap-2.5 text-sm">
                      <CheckCircle className="w-4 h-4 flex-shrink-0 text-emerald-500" />
                      <span className="text-foreground/70">{feat}</span>
                    </li>
                  ))}
                </ul>
                <Link
                  to="/signup"
                  className="w-full flex items-center justify-center gap-2 h-11 rounded-xl text-sm font-bold transition-all hover:opacity-90 active:scale-95"
                  style={
                    plan.highlighted
                      ? { background: 'linear-gradient(to right,#7c3aed,#8b5cf6)', color: '#fff', boxShadow: '0 4px 20px rgba(139,92,246,0.25)' }
                      : { background: 'hsl(var(--muted) / 0.5)', color: 'hsl(var(--foreground))', border: '1px solid hsl(var(--border))' }
                  }
                  data-testid={`pricing-${plan.id}-cta-button`}
                >
                  {plan.ctaText} <ArrowRight size={16} />
                </Link>
              </Reveal>
            );
          })}
        </div>

        <p className="text-center text-sm mt-8 text-muted-foreground">
          {t.footer}
        </p>
      </div>
    </section>
  );
}
