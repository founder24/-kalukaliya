import { useState, useEffect, useCallback } from 'react';
import { Download, X } from 'lucide-react';
import { Analytics } from '@/utils/analytics';
import { API_BASE } from '@/utils/api';

const DISMISS_KEY = 'syrabit_pwa_dismiss';
const DISMISS_DAYS = 7;

function trackPwaEvent(action, meta = {}) {
  try {
    const body = {
      event_type: 'pwa_install',
      metadata: {
        action,
        platform: navigator.userAgentData?.platform || navigator.platform || 'unknown',
        standalone: window.matchMedia('(display-mode: standalone)').matches,
        ...meta,
      },
    };
    navigator.sendBeacon?.(
      `${API_BASE}/analytics/track`,
      new Blob([JSON.stringify(body)], { type: 'application/json' })
    ) || fetch(`${API_BASE}/analytics/track`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {});
  } catch {}
}

export default function PWAInstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [visible, setVisible] = useState(false);
  const [installing, setInstalling] = useState(false);

  useEffect(() => {
    if (window.matchMedia('(display-mode: standalone)').matches) return;
    if (navigator.standalone) return;

    const dismissed = localStorage.getItem(DISMISS_KEY);
    if (dismissed && Date.now() - Number(dismissed) < DISMISS_DAYS * 24 * 60 * 60 * 1000) return;

    const handler = (e) => {
      e.preventDefault();
      setDeferredPrompt(e);
      setTimeout(() => {
        setVisible(true);
        Analytics.pwaPromptShown();
        trackPwaEvent('prompt_shown');
      }, 2500);
    };

    window.addEventListener('beforeinstallprompt', handler);

    const installedHandler = () => {
      Analytics.pwaInstalled();
      trackPwaEvent('installed');
      setVisible(false);
    };
    window.addEventListener('appinstalled', installedHandler);

    return () => {
      window.removeEventListener('beforeinstallprompt', handler);
      window.removeEventListener('appinstalled', installedHandler);
    };
  }, []);

  const handleInstall = useCallback(async () => {
    if (!deferredPrompt) return;
    setInstalling(true);
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    if (outcome === 'accepted') {
      setVisible(false);
    } else {
      trackPwaEvent('rejected');
    }
    setInstalling(false);
    setDeferredPrompt(null);
  }, [deferredPrompt]);

  const handleDismiss = useCallback(() => {
    setVisible(false);
    localStorage.setItem(DISMISS_KEY, String(Date.now()));
    Analytics.pwaPromptDismissed();
    trackPwaEvent('dismissed');
  }, []);

  if (!visible) return null;

  return (
    <div className="fixed bottom-20 left-4 right-4 sm:bottom-6 sm:left-auto sm:right-6 sm:max-w-sm z-[999] animate-slide-up">
      <div className="border border-violet-500/15 rounded-2xl shadow-xl shadow-violet-500/5 p-4" style={{ background: 'rgba(255,255,255,0.95)', backdropFilter: 'blur(20px)' }}>
        <button onClick={handleDismiss} className="absolute top-3 right-3 p-1 rounded-lg text-muted-foreground/40 hover:text-foreground transition-colors" aria-label="Dismiss">
          <X size={16} />
        </button>
        <div className="flex items-start gap-3">
          <img src="/icons/icon-96x96.png" alt="Syrabit.ai" className="w-12 h-12 rounded-xl flex-shrink-0" width="48" height="48" loading="lazy" />
          <div className="flex-1 min-w-0">
            <h3 className="text-foreground font-semibold text-sm">Install Syrabit.ai</h3>
            <p className="text-muted-foreground text-xs mt-0.5 leading-relaxed">Get instant access from your home screen. Works offline too!</p>
            <div className="flex gap-2 mt-3">
              <button onClick={handleDismiss} className="h-8 px-3 rounded-lg bg-muted hover:bg-muted/80 text-muted-foreground text-xs font-medium transition-colors">Not now</button>
              <button onClick={handleInstall} disabled={installing} className="h-8 px-4 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-xs font-semibold transition-colors flex items-center gap-1.5 disabled:opacity-50">
                <Download size={13} />
                {installing ? 'Installing...' : 'Install App'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
