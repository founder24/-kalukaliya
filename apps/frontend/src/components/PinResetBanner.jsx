/**
 * PinResetBanner — Task #611.
 *
 * Surfaced after a sign-in claim that adopted offline Strict Mode but
 * could not migrate the guardian PIN (the hash was salted with the
 * anonymous device id and can no longer be verified once the actor
 * becomes the signed-in user). Prompts the parent to set a new PIN.
 *
 * Visibility rules:
 *  · Only when the local "pin reset needed" flag is set (placed by the
 *    AuthContext claim handler when the backend reports pin_dropped).
 *  · Only for signed-in users whose server settings still report
 *    strict_mode=true and guardian_locked=false (no PIN). The banner
 *    auto-clears the flag the moment a PIN is detected, so it never
 *    lingers after the parent finishes the flow.
 *  · Dismissible via the X button — clears the flag for this browser.
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ShieldAlert, X } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { studyApi } from '@/utils/studyApi';
import {
  pinResetClear, pinResetIsNeeded,
} from '@/utils/pinReset';

// Re-export so existing imports from `@/components/PinResetBanner`
// continue to work.
export { pinResetClear } from '@/utils/pinReset';

export default function PinResetBanner({ variant = 'inline' }) {
  const { user } = useAuth();
  const [needs, setNeeds] = useState(() => pinResetIsNeeded());
  const [strict, setStrict] = useState(false);
  const [hasPin, setHasPin] = useState(false);
  const [ready, setReady] = useState(false);

  // Re-check the local flag when the user changes (sign-in flips it on
  // via AuthContext, in the same tab/mount), when the window regains
  // focus, and when another tab writes to localStorage.
  useEffect(() => {
    setNeeds(pinResetIsNeeded());
  }, [user?.id]);

  useEffect(() => {
    const recheck = () => setNeeds(pinResetIsNeeded());
    window.addEventListener('focus', recheck);
    window.addEventListener('storage', recheck);
    return () => {
      window.removeEventListener('focus', recheck);
      window.removeEventListener('storage', recheck);
    };
  }, []);

  useEffect(() => {
    if (!user?.id || !needs) { setReady(false); return; }
    let cancelled = false;
    studyApi.getSettings()
      .then((s) => {
        if (cancelled) return;
        const locked = !!s?.guardian_locked;
        setStrict(!!s?.strict_mode);
        setHasPin(locked);
        setReady(true);
        // PIN already in place → flag is obsolete.
        if (locked) {
          pinResetClear();
          setNeeds(false);
        }
      })
      .catch(() => { if (!cancelled) setReady(true); });
    return () => { cancelled = true; };
  }, [user?.id, needs]);

  if (!user?.id || !needs || !ready || !strict || hasPin) return null;

  const dismiss = () => {
    pinResetClear();
    setNeeds(false);
  };

  if (variant === 'full') {
    return (
      <div
        role="status"
        className="rounded-2xl border border-amber-300/70 bg-amber-50 dark:bg-amber-950/40 dark:border-amber-700/60 p-4 flex items-start gap-3"
      >
        <ShieldAlert className="w-5 h-5 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
        <div className="flex-1 text-sm">
          <p className="font-semibold text-amber-900 dark:text-amber-100">
            Set a new guardian PIN
          </p>
          <p className="text-amber-800 dark:text-amber-200/90 mt-1">
            Strict Mode is on for your account, but the parental PIN you set
            before signing in couldn&apos;t be carried over for security
            reasons. Choose a new PIN below to protect Strict Mode again.
          </p>
        </div>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss"
          className="text-amber-700/70 hover:text-amber-900 dark:text-amber-300/70 dark:hover:text-amber-100"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    );
  }

  return (
    <div
      role="status"
      className="rounded-xl border border-amber-300/70 bg-amber-50 dark:bg-amber-950/40 dark:border-amber-700/60 px-3 py-2 flex items-center gap-2 text-xs"
    >
      <ShieldAlert className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />
      <span className="flex-1 text-amber-900 dark:text-amber-100">
        Strict Mode is on but has no PIN.{' '}
        <Link to="/guardian" className="font-semibold underline">
          Set a new guardian PIN
        </Link>{' '}
        to lock it down.
      </span>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss"
        className="text-amber-700/70 hover:text-amber-900 dark:text-amber-300/70 dark:hover:text-amber-100"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
