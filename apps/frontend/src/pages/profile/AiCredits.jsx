import {
  Database, Zap, MessageSquare, BookMarked,
} from 'lucide-react';

export default function AiCredits({
  stats, creditsRemaining, creditsUsed, creditsLimit,
  creditPercent, isLowCredits, plan, setShowTopUpModal,
}) {
  return (
    <div className="glass-card rounded-2xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Daily Usage</p>
      </div>
      <div className="p-4">
        <div className="grid grid-cols-2 gap-3">
          {[
            { icon: Database, label: 'Total Tokens', value: stats.total_tokens > 1000 ? `${(stats.total_tokens/1000).toFixed(0)}K` : stats.total_tokens, color: 'text-blue-600', bg: 'rgba(59,130,246,0.10)' },
            { icon: Zap,      label: 'Credits Left', value: creditsRemaining,  color: isLowCredits ? 'text-amber-700' : 'text-emerald-600', bg: isLowCredits ? 'rgba(245,158,11,0.10)' : 'rgba(16,185,129,0.10)' },
            { icon: MessageSquare, label: 'Conversations', value: stats.conversations, color: 'text-violet-600', bg: 'rgba(139,92,246,0.10)' },
            { icon: BookMarked, label: 'Saved Subjects', value: stats.saved_subjects, color: 'text-pink-700', bg: 'rgba(244,63,94,0.10)' },
          ].map(({ icon: Icon, label, value, color, bg }) => (
            <div key={label} className="rounded-xl p-3" style={{ background: bg, border: `1px solid ${bg.replace('0.10', '0.20')}` }}>
              <Icon size={18} className={`${color} mb-2`} />
              <p className={`text-xl font-bold ${color}`}>{value}</p>
              <p className="text-muted-foreground/60 text-xs mt-0.5">{label}</p>
            </div>
          ))}
        </div>
        <div className="mt-4">
          <div className="flex justify-between text-xs text-muted-foreground mb-1.5">
            <span>{creditsLimit === 0 ? 'No credits — upgrade to chat' : 'Credits used'}</span>
            <span className={isLowCredits ? 'text-amber-700' : ''}>
              {creditsLimit === 0 ? '' : `${creditsUsed} / ${creditsLimit}`}
            </span>
          </div>
          <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'rgba(124,58,237,0.10)' }}>
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: creditsLimit === 0 ? '100%' : `${creditPercent}%`,
                background: creditsLimit === 0
                  ? 'rgba(100,116,139,0.4)'
                  : isLowCredits
                  ? 'linear-gradient(to right, #f59e0b, #f97316)'
                  : 'linear-gradient(to right, #7c3aed, #8b5cf6)',
                boxShadow: creditsLimit === 0 ? 'none' : isLowCredits ? '0 0 6px rgba(245,158,11,0.5)' : '0 0 6px rgba(139,92,246,0.4)',
              }}
            />
          </div>
          {plan !== 'free' && (
            <button
              onClick={() => setShowTopUpModal(true)}
              className="mt-3 w-full h-8 rounded-lg text-xs font-semibold transition-all hover:opacity-90 active:scale-[0.98] flex items-center justify-center gap-1.5"
              style={{ background: 'rgba(139,92,246,0.12)', color: 'hsl(var(--primary))', border: '1px solid rgba(139,92,246,0.25)' }}
            >
              <Zap size={12} /> Buy More Credits
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
