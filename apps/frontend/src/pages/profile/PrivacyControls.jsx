import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ShieldOff, ChevronRight, Brain, Lock } from 'lucide-react';
import { toast } from 'sonner';
import {
  getAdsOptOut,
  setAdsOptOut,
  getInitialLocalAdsOptOut,
  hasSeenAdsCrossDeviceBanner,
  markAdsCrossDeviceBannerSeen,
} from '@/utils/adsConfig';
import { apiClient } from '@/utils/api';
import { useAuth } from '@/context/AuthContext';

export default function PrivacyControls({ profile }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [optedOut, setOptedOut] = useState(false);
  const [saving, setSaving] = useState(false);
  const announcedRef = useRef(false);

  const isPaidUser = profile?.plan && profile.plan !== 'free';

  // Hydrate from the server-side value when the profile loads.
  useEffect(() => {
    let next;
    if (profile && typeof profile.ads_opt_out === 'boolean') {
      next = profile.ads_opt_out;
    } else {
      next = getAdsOptOut();
    }
    setOptedOut(next);

    if (
      user &&
      profile &&
      typeof profile.ads_opt_out === 'boolean' &&
      !announcedRef.current &&
      !hasSeenAdsCrossDeviceBanner()
    ) {
      announcedRef.current = true;
      const hadLocalOptOut = getInitialLocalAdsOptOut();
      if (next || hadLocalOptOut) {
        toast.success(
          'Your "Opt out of ads" choice now syncs across every device you sign in on — no need to set it again on each browser.',
          { duration: 7000 }
        );
        markAdsCrossDeviceBannerSeen();
      }
    }
  }, [profile?.ads_opt_out, user]);

  const handleToggle = async () => {
    if (saving) return;

    // Free users: nudge toward upgrade instead of toggling.
    if (!isPaidUser) {
      toast.info('Upgrade to Starter or Pro to remove ads.', {
        action: { label: 'Upgrade', onClick: () => navigate('/profile?upgrade=starter') },
      });
      return;
    }

    const next = !optedOut;
    setOptedOut(next);
    setAdsOptOut(next);

    if (!user) {
      toast.info('Saved on this device. Sign in to sync this preference across all your devices.');
      return;
    }

    setSaving(true);
    try {
      await apiClient().patch('/user/profile', { ads_opt_out: next });
      toast.success(
        next
          ? 'Ads disabled across all your devices — takes effect on next page load'
          : 'Ads re-enabled across all your devices — thanks for supporting Syrabit'
      );
      markAdsCrossDeviceBannerSeen();
    } catch {
      toast.warning(
        'Saved on this device, but we couldn\'t sync it across your other devices. Try again when you\'re back online.'
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="glass-card rounded-2xl overflow-hidden" data-testid="privacy-controls">
      <div className="px-4 py-3 border-b border-border">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
          Privacy
        </p>
      </div>
      <div className="p-4 space-y-3">
        <div
          className="flex items-start gap-3 p-3 rounded-xl"
          style={{
            background: isPaidUser ? 'rgba(124,58,237,0.06)' : 'rgba(148,163,184,0.06)',
            border: isPaidUser
              ? '1px solid rgba(139,92,246,0.18)'
              : '1px solid rgba(148,163,184,0.18)',
          }}
        >
          <div
            className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
            style={
              isPaidUser
                ? { background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.25)' }
                : { background: 'rgba(148,163,184,0.10)', border: '1px solid rgba(148,163,184,0.20)' }
            }
          >
            <ShieldOff size={16} style={{ color: isPaidUser ? 'hsl(var(--primary))' : 'hsl(var(--muted-foreground))' }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <p className="text-sm font-semibold text-foreground">Opt out of ads</p>
              {!isPaidUser && (
                <span
                  className="inline-flex items-center gap-0.5 text-[10px] font-semibold px-1.5 py-0.5 rounded-full"
                  style={{ background: 'rgba(245,158,11,0.12)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.25)' }}
                >
                  <Lock size={8} /> Paid
                </span>
              )}
            </div>
            <p className="text-xs text-muted-foreground/70 mt-0.5">
              {isPaidUser
                ? 'Stop ad scripts from loading. While you\'re signed in, this preference is saved to your account and synced across all your devices, and applies on the next page you open.'
                : 'Ad-free browsing is included with Starter and Pro plans. Upgrade to remove all ads across your devices.'}
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={isPaidUser ? optedOut : false}
            aria-label={isPaidUser ? 'Opt out of ads' : 'Upgrade to opt out of ads'}
            onClick={handleToggle}
            data-testid="ads-optout-toggle"
            className="relative flex-shrink-0 w-11 h-6 rounded-full transition-colors"
            style={{
              background: isPaidUser && optedOut
                ? 'hsl(var(--primary))'
                : 'rgba(148,163,184,0.35)',
              cursor: isPaidUser ? 'pointer' : 'not-allowed',
              opacity: isPaidUser ? 1 : 0.5,
            }}
          >
            <span
              className="absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform shadow"
              style={{ transform: isPaidUser && optedOut ? 'translateX(22px)' : 'translateX(2px)' }}
            />
          </button>
        </div>

        <Link
          to="/profile/memories"
          data-testid="my-memories-link"
          className="flex items-center gap-3 p-3 rounded-xl hover:bg-foreground/5 transition-colors"
        >
          <div
            className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{ background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.25)' }}
          >
            <Brain size={16} style={{ color: 'hsl(var(--primary))' }} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-foreground">My memories</p>
            <p className="text-xs text-muted-foreground/70 mt-0.5">
              Browse and delete what Syra has saved about you.
            </p>
          </div>
          <ChevronRight size={16} className="text-muted-foreground" />
        </Link>

        <Link
          to="/privacy"
          className="flex items-center justify-between px-3 py-2 rounded-xl text-xs text-muted-foreground hover:bg-foreground/5 transition-colors"
        >
          <span>Read full privacy policy</span>
          <ChevronRight size={14} />
        </Link>
      </div>
    </div>
  );
}
