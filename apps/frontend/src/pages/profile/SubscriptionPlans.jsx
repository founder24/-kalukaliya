import { Check, CheckCircle } from 'lucide-react';
import { PLANS, PLAN_RANK, PLAN_FEATURES } from './shared';
import { DOC_ACCESS_CONFIG } from '@/utils/plans';

export default function SubscriptionPlans({
  plan, planInfo, profile,
  setPaymentPlan, setShowPaymentModal,
}) {
  return (
    <>
      <div className="glass-card rounded-2xl overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Subscription</p>
          <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${planInfo.badgeColor}`}>
            {plan.toUpperCase()}
          </span>
        </div>

        <div className="p-4 space-y-4">
          <div className="flex items-center justify-between p-3 rounded-xl"
            style={{ background: 'rgba(139,92,246,0.06)', border: '1px solid rgba(139,92,246,0.15)' }}>
            <div>
              <p className="text-xs text-muted-foreground">Current plan</p>
              <p className="font-bold text-foreground">{planInfo.label}</p>
            </div>
            <div className="text-right">
              <p className="text-xs text-muted-foreground">Document access</p>
              <p className={`text-sm font-semibold ${(DOC_ACCESS_CONFIG[profile?.document_access || PLANS[plan]?.docAccess || 'zero'] || DOC_ACCESS_CONFIG.zero).color}`}>
                {(DOC_ACCESS_CONFIG[profile?.document_access || PLANS[plan]?.docAccess || 'zero'] || DOC_ACCESS_CONFIG.zero).icon}{' '}
                {(DOC_ACCESS_CONFIG[profile?.document_access || PLANS[plan]?.docAccess || 'zero'] || DOC_ACCESS_CONFIG.zero).label}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {Object.entries(PLANS).map(([planKey, info]) => {
              const isActive    = plan === planKey;
              const isPro       = planKey === 'pro';
              const cardRank    = PLAN_RANK[planKey] ?? 0;
              const userRank    = PLAN_RANK[plan]    ?? 0;
              const isLower     = cardRank < userRank;
              const docInfo     = DOC_ACCESS_CONFIG[info.docAccess] || DOC_ACCESS_CONFIG.zero;
              return (
                <div
                  key={planKey}
                  className="relative rounded-xl p-3 flex flex-col transition-all duration-200"
                  style={
                    isActive
                      ? { border: '1px solid rgba(139,92,246,0.50)', background: 'rgba(124,58,237,0.08)', boxShadow: '0 0 20px rgba(139,92,246,0.12)' }
                      : isLower
                      ? { border: '1px solid hsl(var(--border) / 0.2)', background: 'hsl(var(--muted) / 0.3)', opacity: 0.6 }
                      : { border: '1px solid hsl(var(--border) / 0.3)', background: 'hsl(var(--muted) / 0.15)' }
                  }
                >
                  <div
                    className="absolute -top-2.5 left-1/2 -translate-x-1/2 px-2 py-0.5 rounded-full text-[10px] font-bold whitespace-nowrap"
                    style={
                      isActive
                        ? { background: 'rgba(124,58,237,0.25)', color: 'hsl(var(--primary))', border: '1px solid rgba(139,92,246,0.35)' }
                        : isPro
                        ? { background: 'rgba(245,158,11,0.15)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.30)' }
                        : planKey === 'starter'
                        ? { background: 'rgba(139,92,246,0.15)', color: '#a78bfa', border: '1px solid rgba(139,92,246,0.30)' }
                        : { background: 'hsl(var(--muted) / 0.5)', color: 'hsl(var(--muted-foreground))', border: '1px solid hsl(var(--border) / 0.3)' }
                    }
                  >
                    {isActive ? 'ACTIVE' : info.badge}
                  </div>

                  <p className="text-sm font-semibold text-foreground mt-1">{info.label}</p>

                  <p className="font-bold text-2xl mt-1"
                    style={{ color: isPro ? '#f59e0b' : isLower ? 'hsl(var(--muted-foreground))' : 'hsl(var(--primary))' }}>
                    {info.creditsLabel || info.credits.toLocaleString()}
                    <span className="text-xs font-normal text-muted-foreground ml-1">{info.creditsLabel ? '' : 'credits'}</span>
                  </p>

                  <p className="text-base font-semibold text-foreground mt-0.5">
                    {info.price}
                    <span className="text-xs font-normal text-muted-foreground ml-1">{info.period}</span>
                  </p>

                  <div className="flex items-center gap-1.5 mt-2 mb-1">
                    <span className={`text-[10px] font-semibold ${docInfo.color}`}>
                      {docInfo.icon} {docInfo.label}
                    </span>
                  </div>

                  <ul className="mt-1 space-y-1 flex-1">
                    {PLAN_FEATURES[planKey].slice(0, 3).map((f) => (
                      <li key={f} className="flex items-center gap-1 text-[10px] text-muted-foreground/70">
                        <Check size={10} className="text-emerald-600 flex-shrink-0" aria-hidden="true" />
                        {f}
                      </li>
                    ))}
                  </ul>

                  {isActive ? (
                    <div className="mt-3 w-full h-8 rounded-lg flex items-center justify-center text-xs font-medium"
                      style={{ background: 'rgba(139,92,246,0.12)', color: 'hsl(var(--primary))' }}>
                      <CheckCircle size={12} className="mr-1" aria-hidden="true" /> Current Plan
                    </div>
                  ) : isLower ? (
                    <div className="mt-3 w-full h-8 rounded-lg flex items-center justify-center text-[10px] font-medium text-muted-foreground"
                      style={{ background: 'hsl(var(--muted) / 0.3)', border: '1px solid hsl(var(--border) / 0.2)' }}>
                      Included in your plan
                    </div>
                  ) : (
                    <button
                      onClick={() => { setPaymentPlan(planKey); setShowPaymentModal(true); }}
                      className="mt-3 w-full h-8 rounded-lg text-xs font-semibold text-white transition-all hover:opacity-90 active:scale-[0.98]"
                      style={isPro
                        ? { background: 'linear-gradient(135deg,#d97706,#f59e0b)', boxShadow: '0 4px 12px rgba(245,158,11,0.25)' }
                        : { background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)', boxShadow: '0 4px 12px rgba(124,58,237,0.25)' }}
                      aria-label={`Upgrade to ${info.label} plan`}
                      data-testid={`upgrade-${planKey}-button`}
                    >
                      Upgrade to {info.label}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>

    </>
  );
}
