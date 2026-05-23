import { Star } from 'lucide-react';

export function StarRating({ value = 4, max = 5 }) {
  return (
    <div className="flex items-center gap-0.5">
      {[...Array(max)].map((_, i) => (
        <Star
          key={i}
          size={12}
          className={i < value ? 'text-amber-700 fill-amber-400' : 'text-muted-foreground/70'}
        />
      ))}
    </div>
  );
}

export function UsageDots({ value = 3, max = 4, color = 'bg-primary' }) {
  return (
    <div className="flex items-center gap-1">
      {[...Array(max)].map((_, i) => (
        <div
          key={i}
          className={`w-2 h-2 rounded-full ${i < value ? color : 'bg-muted'}`}
        />
      ))}
    </div>
  );
}

export const PLANS = {
  free:    { label: 'Free',    credits: 30,   creditsLabel: '30/day',    price: '₹0',   period: 'forever',   badge: 'FREE TIER',   badgeColor: 'text-slate-600 bg-slate-400/10 border-slate-400/20',  docAccess: 'zero'    },
  starter: { label: 'Starter', credits: 500,  creditsLabel: '500/day',   price: '₹99',  period: 'one-time',  badge: 'POPULAR',     badgeColor: 'text-violet-600 bg-violet-400/10 border-violet-400/20', docAccess: 'limited' },
  pro:     { label: 'Pro',     credits: 4000, creditsLabel: '4,000/day', price: '₹999', period: 'one-time',  badge: 'BEST VALUE',  badgeColor: 'text-amber-700 bg-amber-400/10 border-amber-400/20',    docAccess: 'full'    },
};

export const PLAN_RANK = { free: 0, starter: 1, pro: 2 };

export const PLAN_FEATURES = {
  free:    ['30 AI credits/day', '5 messages/min', 'All subjects access', 'Chat history (limited)', 'Zero document access'],
  starter: ['500 AI credits/day', '10 messages/min', 'All subjects access', 'Full chat history', 'Limited document access', 'Priority responses'],
  pro:     ['4,000 AI credits/day', '15 messages/min', 'Unlimited subjects access', 'Unlimited history', 'Full document access', 'All AI models'],
};

export const TOPUP_OPTIONS = [
  { credits: 100,  price: '₹49',  label: '100 credits' },
  { credits: 500,  price: '₹199', label: '500 credits' },
  { credits: 1000, price: '₹349', label: '1,000 credits' },
];

export function loadRazorpay() {
  return new Promise((resolve) => {
    if (window.Razorpay) { resolve(true); return; }
    const existing = document.querySelector('script[src="https://checkout.razorpay.com/v1/checkout.js"]');
    if (existing) {
      existing.onload = () => resolve(true);
      existing.onerror = () => resolve(false);
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://checkout.razorpay.com/v1/checkout.js';
    script.onload  = () => resolve(true);
    script.onerror = () => resolve(false);
    document.body.appendChild(script);
  });
}

