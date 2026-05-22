/**
 * Task #404 — Cloudflare Turnstile widget for public auth forms.
 *
 * Renders the Cloudflare Turnstile challenge inside the parent form
 * when the backend reports ``TURNSTILE_ON`` is on (see
 * ``GET /api/turnstile/config``). Renders nothing otherwise so the
 * existing dev / preview environments keep working without any
 * Cloudflare config.
 *
 * Usage:
 *
 *   const turnstileRef = useRef(null);
 *   ...
 *   <TurnstileWidget ref={turnstileRef} action="login" />
 *   ...
 *   const token = turnstileRef.current?.getToken?.() || '';
 *   await login(email, password, token);
 *   turnstileRef.current?.reset?.();
 *
 * The token is one-shot per the Turnstile contract — call ``reset()``
 * after every form submit (success or failure) so the next attempt
 * mints a fresh token.
 */
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import {
  loadTurnstileScript,
  useTurnstileConfig,
} from '@/hooks/useTurnstile';

const TurnstileWidget = forwardRef(function TurnstileWidget(
  {
    action,
    theme = 'auto',
    size = 'flexible',
    className = '',
    onToken,
    onError,
    onExpire,
  },
  ref,
) {
  const { enabled, siteKey, ready: configReady } = useTurnstileConfig();
  const containerRef = useRef(null);
  const widgetIdRef = useRef(null);
  const tokenRef = useRef('');
  const onTokenRef = useRef(onToken);
  const onErrorRef = useRef(onError);
  const onExpireRef = useRef(onExpire);
  const [scriptReady, setScriptReady] = useState(
    () =>
      typeof window !== 'undefined' && !!window.turnstile,
  );
  const [errored, setErrored] = useState(false);

  // Keep latest callbacks in refs so the render effect doesn't have
  // to re-mount the widget every time the parent re-renders with a
  // fresh inline lambda.
  useEffect(() => { onTokenRef.current = onToken; }, [onToken]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);
  useEffect(() => { onExpireRef.current = onExpire; }, [onExpire]);

  // Load the CF script lazily — only when the backend says the flag
  // is on. A third-party 404 / network blip surfaces as ``errored``
  // so the form can still submit (the dormant ``require_turnstile``
  // dependency lets it through when the flag is off; once the flag
  // flips on, the backend will 403 with ``turnstile_required`` which
  // the form's existing error banner already surfaces).
  useEffect(() => {
    if (!enabled || !siteKey) return undefined;
    let cancelled = false;
    loadTurnstileScript()
      .then((ts) => {
        if (cancelled) return;
        if (ts) setScriptReady(true);
      })
      .catch(() => {
        if (cancelled) return;
        setErrored(true);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, siteKey]);

  // Render the widget once the script is ready. The cleanup removes
  // the widget so route navigation doesn't leak orphaned iframes.
  useEffect(() => {
    if (!scriptReady) return undefined;
    if (!enabled || !siteKey) return undefined;
    if (typeof window === 'undefined' || !window.turnstile) return undefined;
    if (!containerRef.current) return undefined;
    if (widgetIdRef.current) return undefined;
    try {
      widgetIdRef.current = window.turnstile.render(containerRef.current, {
        sitekey: siteKey,
        action: action || undefined,
        theme,
        size,
        callback: (token) => {
          tokenRef.current = token || '';
          if (onTokenRef.current) {
            try { onTokenRef.current(token || ''); } catch { /* noop */ }
          }
        },
        'error-callback': () => {
          tokenRef.current = '';
          setErrored(true);
          if (onErrorRef.current) {
            try { onErrorRef.current(); } catch { /* noop */ }
          }
        },
        'expired-callback': () => {
          tokenRef.current = '';
          if (onExpireRef.current) {
            try { onExpireRef.current(); } catch { /* noop */ }
          }
        },
        'timeout-callback': () => {
          tokenRef.current = '';
        },
      });
    } catch {
      setErrored(true);
    }
    return () => {
      const id = widgetIdRef.current;
      widgetIdRef.current = null;
      if (id && typeof window !== 'undefined' && window.turnstile) {
        try { window.turnstile.remove(id); } catch { /* noop */ }
      }
    };
  }, [scriptReady, enabled, siteKey, action, theme, size]);

  useImperativeHandle(
    ref,
    () => ({
      getToken: () => tokenRef.current || '',
      reset: () => {
        tokenRef.current = '';
        const id = widgetIdRef.current;
        if (id && typeof window !== 'undefined' && window.turnstile) {
          try { window.turnstile.reset(id); } catch { /* noop */ }
        }
      },
      enabled: !!enabled,
      ready: !!scriptReady && !!widgetIdRef.current,
    }),
    [enabled, scriptReady],
  );

  // While config is loading or the flag is off, render nothing so
  // forms collapse to their pre-Turnstile layout.
  if (!configReady || !enabled || !siteKey) return null;

  return (
    <div className={className}>
      <div
        ref={containerRef}
        data-testid="turnstile-widget"
        data-sitekey={siteKey}
      />
      {errored && (
        <p
          role="alert"
          className="text-xs text-red-500 mt-1"
        >
          Verification failed. Please refresh the page and try again.
        </p>
      )}
    </div>
  );
});

export default TurnstileWidget;
