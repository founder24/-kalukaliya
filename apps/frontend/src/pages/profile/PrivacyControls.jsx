import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ShieldOff, ChevronRight, Brain } from 'lucide-react';
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
  const [optedOut, setOptedOut] = useState(false);
  const [saving, setSaving] = useState(false);
  const announcedRef = useRef(false);


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
            background: 'rgba(124,58,237,0.06)',
            border: '1px solid rgba(139,92,246,0.18)',
          }}
        >
          <div
            className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
            style={{ background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.25)' }}
          >
            <ShieldOff size={16} style={{ color: 'hsl(var(--primary))' }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <p className="text-sm font-semibold text-foreground">Opt out of ads</p>
            </div>
            <p className="text-xs text-muted-foreground/70 mt-0.5">
              Stop ad scripts from loading. Your preference is saved to your account and applies on the next page you open.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={optedOut}
            aria-label="Opt out of ads"
            onClick={handleToggle}
            data-testid="ads-optout-toggle"
            className="relative flex-shrink-0 w-11 h-6 rounded-full transition-colors"
            style={{
              background: optedOut
                ? 'hsl(var(--primary))'
                : 'rgba(148,163,184,0.35)',
              cursor: 'pointer',
              opacity: 1,
            }}
          >
            <span
              className="absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform shadow"
              style={{ transform: optedOut ? 'translateX(22px)' : 'translateX(2px)' }}
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
