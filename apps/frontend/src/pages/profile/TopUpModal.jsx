import { Zap, CreditCard, Loader2 } from 'lucide-react';
import { TOPUP_OPTIONS } from './shared';
import ModalOverlay from '@/components/ui/ModalOverlay';

export default function TopUpModal({
  showTopUpModal, topUpCredits, setTopUpCredits,
  topUpLoading, planInfo, creditsRemaining,
  setShowTopUpModal, handleTopUpCheckout,
}) {
  if (!showTopUpModal) return null;
  return (
    <ModalOverlay
      open={showTopUpModal}
      onClose={() => setShowTopUpModal(false)}
      title="Buy More Credits"
      borderColor="rgba(139,92,246,0.25)"
      backdropOpacity="0.7"
    >
      <p className="text-sm text-muted-foreground">
        Add credits to your <span className="font-semibold text-foreground">{planInfo.label}</span> plan.
        Current balance: <span className="font-bold" style={{ color: 'hsl(var(--primary))' }}>{creditsRemaining}</span> credits
      </p>

      <div className="grid grid-cols-2 gap-2">
        {TOPUP_OPTIONS.map((opt) => (
          <button
            key={opt.credits}
            onClick={() => setTopUpCredits(opt.credits)}
            className={`rounded-xl p-3 text-center border transition-all ${
              topUpCredits === opt.credits
                ? 'border-violet-500 bg-violet-500/15'
                : 'border-border hover:border-violet-500/30 bg-card'
            }`}
          >
            <div className="flex items-center justify-center gap-1 mb-1">
              <Zap size={12} style={{ color: 'hsl(var(--primary))' }} />
              <span className="text-sm font-bold text-foreground">{opt.credits}</span>
            </div>
            <p className="text-xs text-muted-foreground">{opt.price}</p>
          </button>
        ))}
      </div>

      <div className="flex gap-2">
        <button
          onClick={() => setShowTopUpModal(false)}
          className="flex-1 h-9 rounded-xl text-sm font-medium text-muted-foreground border border-border hover:bg-accent/40 transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={handleTopUpCheckout}
          disabled={topUpLoading}
          className="flex-1 h-9 rounded-xl text-sm font-semibold text-white flex items-center justify-center gap-1.5 transition-all hover:opacity-90 disabled:opacity-50"
          style={{ background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)' }}
        >
          {topUpLoading ? <Loader2 size={14} className="animate-spin" /> : <CreditCard size={14} />}
          Buy Now
        </button>
      </div>
    </ModalOverlay>
  );
}
