/**
 * ReviewPrompt — Task #652 (rewired for Trustpilot in Task #724)
 *
 * Friendly, dismissible in-app prompt asking engaged students to leave
 * a review on Trustpilot. Mounted once at the app root. Other surfaces
 * (the QuizModal result screen, ChapterPage on the 3rd chapter view in
 * a rolling 7-day window) call `requestReviewPrompt(reason)` to ask
 * for it to appear. The prompt itself enforces all throttling so
 * callers don't have to.
 *
 * Throttling rules:
 *  - Never shown to users who already dismissed it (permanent dismissal).
 *  - Never shown to users who already clicked through to Trustpilot.
 *  - Otherwise shown at most once per 30 days per browser.
 *  - Skipped entirely if the backend doesn't expose a writeReviewUrl
 *    (i.e. TRUSTPILOT_BUSINESS_UNIT_ID isn't configured).
 *
 * NOTE: Analytics event names (`review_prompt_shown/clicked/dismissed`)
 * are intentionally provider-agnostic and remain unchanged so that the
 * existing admin funnel dashboards, weekly digest, and alerting loops
 * (Task #654 + #678) keep working without a schema migration.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { MessageSquarePlus, Star, X } from 'lucide-react';
import Analytics from '@/utils/analytics';
import { fetchTrustpilotConfigOnce } from '@/components/content/TrustpilotReviewsSection';

const SHOWN_KEY = 'syrabit_review_prompt_shown_at';
const DISMISSED_KEY = 'syrabit_review_prompt_dismissed';
const CLICKED_KEY = 'syrabit_review_prompt_clicked';
const THROTTLE_MS = 30 * 24 * 60 * 60 * 1000;

let _pendingReason = null;
let _listener = null;

function safeGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function safeSet(key, value) {
  try { localStorage.setItem(key, value); } catch {}
}

function canShow() {
  if (typeof window === 'undefined') return false;
  if (safeGet(DISMISSED_KEY)) return false;
  if (safeGet(CLICKED_KEY)) return false;
  const lastShown = Number(safeGet(SHOWN_KEY) || 0);
  if (lastShown && Date.now() - lastShown < THROTTLE_MS) return false;
  return true;
}

/**
 * Public API. Call from any "happy moment" surface (e.g. quiz finished
 * with a high score, 3rd chapter read in a week). Safe to call often —
 * the prompt enforces its own throttling and no-op when ineligible.
 */
export function requestReviewPrompt(reason = 'unknown') {
  if (!canShow()) return false;
  if (_listener) {
    _listener(reason);
  } else {
    _pendingReason = reason;
  }
  return true;
}

export default function ReviewPrompt() {
  const [open, setOpen] = useState(false);
  const [writeReviewUrl, setWriteReviewUrl] = useState('');
  const writeReviewUrlRef = useRef('');
  const reasonRef = useRef('unknown');
  const showTimerRef = useRef(null);

  // Resolve the writeReviewUrl from the same /config/trustpilot endpoint
  // the marketing section already calls — avoids hard-coding the
  // business unit id on the client and reuses the in-memory cache so
  // this is free.
  useEffect(() => {
    let cancelled = false;
    fetchTrustpilotConfigOnce()
      .then(json => {
        if (cancelled) return;
        const url = (json && json.writeReviewUrl) || '';
        writeReviewUrlRef.current = url;
        setWriteReviewUrl(url);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const showNow = useCallback((reason) => {
    if (!canShow()) return;
    reasonRef.current = reason || 'unknown';
    // Small delay so the prompt doesn't crash into the moment of joy
    // (e.g. the quiz "Trophy" screen) — feels less interruptive.
    if (showTimerRef.current) clearTimeout(showTimerRef.current);
    showTimerRef.current = setTimeout(() => {
      if (!canShow()) return;
      // Don't burn the 30-day throttle if we don't actually have a
      // place to send the user — e.g. backend hasn't responded yet or
      // TRUSTPILOT_BUSINESS_UNIT_ID isn't configured. Better to
      // silently skip and wait for the next eligible "happy moment".
      if (!writeReviewUrlRef.current) return;
      safeSet(SHOWN_KEY, String(Date.now()));
      Analytics.reviewPromptShown(reasonRef.current);
      setOpen(true);
    }, 1200);
  }, []);

  // Wire the singleton trigger.
  useEffect(() => {
    _listener = showNow;
    if (_pendingReason) {
      const r = _pendingReason;
      _pendingReason = null;
      showNow(r);
    }
    return () => {
      _listener = null;
      if (showTimerRef.current) clearTimeout(showTimerRef.current);
    };
  }, [showNow]);

  const dismiss = useCallback(() => {
    safeSet(DISMISSED_KEY, '1');
    Analytics.reviewPromptDismissed(reasonRef.current);
    setOpen(false);
  }, []);

  const clickThrough = useCallback(() => {
    safeSet(CLICKED_KEY, '1');
    Analytics.reviewPromptClicked(reasonRef.current);
    setOpen(false);
  }, []);

  if (!open || !writeReviewUrl) return null;

  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-label="Leave a Trustpilot review"
      className="fixed z-[110] bottom-4 right-4 left-4 sm:left-auto sm:max-w-sm rounded-2xl border border-border/60 bg-card text-foreground shadow-2xl p-4 animate-in fade-in slide-in-from-bottom-4"
    >
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss review prompt"
        className="absolute top-2 right-2 p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted"
      >
        <X className="w-4 h-4" />
      </button>
      <div className="flex items-start gap-3 pr-6">
        <div className="shrink-0 w-10 h-10 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center">
          <Star className="w-5 h-5 fill-emerald-500 text-emerald-500" />
        </div>
        <div className="min-w-0">
          <div className="text-sm font-semibold leading-snug">
            Enjoying Syrabit? Help other students find us.
          </div>
          <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
            Sharing a quick review on Trustpilot takes 30 seconds and means the world to our team.
          </p>
          <div className="flex items-center gap-2 mt-3">
            <a
              href={writeReviewUrl}
              target="_blank"
              rel="noopener noreferrer nofollow"
              onClick={clickThrough}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold transition-colors"
            >
              <MessageSquarePlus className="w-3.5 h-3.5" />
              Leave a review on Trustpilot
            </a>
            <button
              type="button"
              onClick={dismiss}
              className="text-xs text-muted-foreground hover:text-foreground px-2 py-2"
            >
              Maybe later
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
