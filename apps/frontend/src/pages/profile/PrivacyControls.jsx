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

  // Hydrate from the server-side value when the profile loads, so the
  // toggle reflects the cross-device preference instead of whatever
  // localStorage happened to hold on this device.
  useEffect(() => {
    let next;
    if (profile && typeof profile.ads_opt_out === 'boolean') {
      next = profile.ads_opt_out;
    } else {
      next = getAdsOptOut();
    }
    setOptedOut(next);

    // Task #532: one-time announcement that the opt-out preference now
    // syncs across the user's devices. Only shown to signed-in users
    // who already had ads opted out (locally OR on the server) — i.e.
    // the population whose existing choice has just been "promoted"
    // to a cross-device account preference. Mark seen on first run so
    // it never reappears, even if they toggle the setting later.
    if (
      user &&
      profile &&
      typeof profile.ads_opt_out === 'boolean' &&
      !announcedRef.current &&
      !hasSeenAdsCrossDeviceBanner()
    ) {
      announcedRef.current = true;
      // IMPORTANT: read the *initial* local value (snapshotted at JS
      // bundle load), not the live localStorage flag — by the time
      // this effect runs, ProfilePage's `hydrateAdsOptOutFromServer`
      // has already overwritten localStorage with the server value, so
      // checking it now would miss legacy local-only opt-outs.
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
    // Optimistic local update so the UI is instant.
    setOptedOut(next);
    setAdsOptOut(next);

    // Signed-out fallback: this control is normally only reachable from
    // the auth-gated profile page, but if the session has expired in
    // the background we still want to honour the local toggle and tell
    // the user how to make it stick across devices.
    if (!user) {
      toast.info(
        'Saved on this device. Sign in to sync this preference across all your devices.'
      );
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
      // The user has now made an explicit cross-device choice; suppress
      // the one-time announcement on subsequent visits.
      markAdsCrossDeviceBannerSeen();
    } catch {
      // Server save failed — keep the local change but warn the user
      // that other devices won't pick it up until they're online.
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
            <p className="text-sm font-semibold text-foreground">Opt out of ads</p>
            <p className="text-xs text-muted-foreground/70 mt-0.5">
              Stop ad scripts from loading. While you're signed in, this preference is saved to
              your account and synced across all your devices, and applies on the next page you open.
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
              background: optedOut ? 'hsl(var(--primary))' : 'rgba(148,163,184,0.35)',
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
