/**
 * TrustpilotReviewModal — Task #155
 *
 * Opens when the user clicks "Rate us on Trustpilot".
 * Shows a pre-written, subject-specific review script the user can edit,
 * then a "Copy & Open Trustpilot" button that copies the text and opens
 * the invitation link (or generic profile URL fallback) in a new tab.
 *
 * Accessibility: focus-trapped, closeable via Escape or click-outside.
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import { X, Copy, Check, ExternalLink, Loader2 } from 'lucide-react';
import { buildReviewScript } from '@/utils/trustpilotReviewScript';
import { generateTrustpilotInvitationLink } from '@/utils/api';

const FALLBACK_URL = 'https://www.trustpilot.com/review/syrabit.ai';

export default function TrustpilotReviewModal({
  open,
  onClose,
  subjectName = '',
  boardName = '',
  className = '',
}) {
  const overlayRef = useRef(null);
  const dialogRef = useRef(null);
  const textareaRef = useRef(null);

  const [text, setText] = useState('');
  const [inviteUrl, setInviteUrl] = useState(FALLBACK_URL);
  const [loadingUrl, setLoadingUrl] = useState(false);
  const [copied, setCopied] = useState(false);
  // null | 'auth' | 'api'
  const [urlError, setUrlError] = useState(null);

  useEffect(() => {
    if (!open) return;
    const script = buildReviewScript({ subjectName, boardName, className });
    setText(script);
    setCopied(false);
    setUrlError(null);

    let cancelled = false;
    setLoadingUrl(true);
    setInviteUrl(FALLBACK_URL);
    generateTrustpilotInvitationLink()
      .then((url) => {
        if (!cancelled) {
          setInviteUrl(url || FALLBACK_URL);
          setUrlError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setInviteUrl(FALLBACK_URL);
          setUrlError(err?.response?.status === 401 ? 'auth' : 'api');
        }
      })
      .finally(() => { if (!cancelled) setLoadingUrl(false); });

    return () => { cancelled = true; };
  }, [open, subjectName, boardName, className]);

  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement;
    const t = setTimeout(() => textareaRef.current?.focus(), 50);
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'Tab') trapTab(e);
    };
    document.addEventListener('keydown', onKey);
    return () => {
      clearTimeout(t);
      document.removeEventListener('keydown', onKey);
      prev?.focus();
    };
  }, [open, onClose]);

  function trapTab(e) {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = dialog.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey ? document.activeElement === first : document.activeElement === last) {
      e.preventDefault();
      (e.shiftKey ? last : first)?.focus();
    }
  }

  const handleOverlayClick = useCallback((e) => {
    if (e.target === overlayRef.current) onClose();
  }, [onClose]);

  const handleCopyAndOpen = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
    }
    window.open(inviteUrl, '_blank', 'noopener,noreferrer');
  }, [text, inviteUrl]);

  if (!open) return null;

  const contextLabel = [boardName, className, subjectName].filter(Boolean).join(' ') || 'Syrabit.ai';

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
      style={{ background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(2px)' }}
      aria-modal="true"
      role="dialog"
      aria-labelledby="tp-modal-title"
    >
      <div
        ref={dialogRef}
        className="relative w-full sm:max-w-lg rounded-t-3xl sm:rounded-3xl bg-background border border-border/40 shadow-2xl flex flex-col"
        style={{ maxHeight: '90dvh' }}
      >
        <div className="flex items-start justify-between p-5 pb-4 border-b border-border/30 shrink-0">
          <div>
            <h2
              id="tp-modal-title"
              className="text-base font-bold text-foreground"
            >
              Share your experience
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Edit the draft below, then copy and paste it on Trustpilot.
            </p>
          </div>
          <button
            onClick={onClose}
            className="ml-3 shrink-0 p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 pb-4">
          <p className="text-xs text-muted-foreground mb-2">
            A review for{' '}
            <span className="font-medium text-foreground">{contextLabel}</span>
            {' '}— feel free to edit or rewrite entirely.
          </p>
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={7}
            className="w-full rounded-xl border border-border bg-muted/20 px-3.5 py-3 text-sm text-foreground placeholder:text-muted-foreground resize-none outline-none focus:border-violet-500/40 focus:ring-1 focus:ring-violet-500/20 transition-colors"
            aria-label="Review draft — edit before copying"
          />
          {urlError === 'auth' && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-2" role="alert">
              Sign in to get a personalised review link — the button below will open our general Trustpilot page instead.
            </p>
          )}
          {urlError === 'api' && (
            <p className="text-[11px] text-muted-foreground mt-2" role="alert">
              Couldn't generate a personalised link — the button below will open our Trustpilot page directly.
            </p>
          )}
          {!urlError && (
            <p className="text-[11px] text-muted-foreground mt-2">
              Clicking the button below copies this text and opens Trustpilot in a new tab.
              Paste the text into the Trustpilot review form.
            </p>
          )}
        </div>

        <div className="shrink-0 p-5 pt-3 border-t border-border/30 flex flex-col gap-2">
          <button
            onClick={handleCopyAndOpen}
            disabled={loadingUrl}
            className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-[#00b67a] hover:bg-[#00a368] active:bg-[#008f5a] disabled:opacity-60 transition-colors px-5 py-3 text-sm font-semibold text-white shadow-sm"
          >
            {loadingUrl ? (
              <Loader2 size={16} className="animate-spin shrink-0" />
            ) : copied ? (
              <Check size={16} className="shrink-0" />
            ) : (
              <Copy size={16} className="shrink-0" />
            )}
            {copied ? 'Copied! Opening Trustpilot…' : 'Copy & Open Trustpilot'}
            {!loadingUrl && !copied && <ExternalLink size={13} className="shrink-0 opacity-70" />}
          </button>
          <button
            onClick={onClose}
            className="w-full rounded-xl border border-border/40 px-5 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted/30 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
