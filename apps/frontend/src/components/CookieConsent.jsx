import { useState, useEffect, useCallback } from 'react';
import { Cookie, X } from 'lucide-react';
import { Link } from 'react-router-dom';

const CONSENT_KEY = 'syrabit_cookie_consent';

export default function CookieConsent() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem(CONSENT_KEY);
    if (!stored) {
      setVisible(true);
    }
  }, []);

  const handleAccept = useCallback(() => {
    localStorage.setItem(CONSENT_KEY, 'accepted');
    setVisible(false);
  }, []);

  const handleDecline = useCallback(() => {
    localStorage.setItem(CONSENT_KEY, 'declined');
    setVisible(false);
  }, []);

  if (!visible) return null;

  return (
    <div className="fixed bottom-20 left-4 right-4 sm:bottom-6 sm:left-4 sm:right-auto sm:max-w-sm z-[998] animate-slide-up">
      <div className="border border-violet-500/15 rounded-2xl shadow-xl shadow-violet-500/5 p-4" style={{ background: 'rgba(255,255,255,0.95)', backdropFilter: 'blur(20px)' }}>
        <button onClick={handleDecline} className="absolute top-3 right-3 p-1 rounded-lg text-muted-foreground/40 hover:text-foreground transition-colors" aria-label="Dismiss">
          <X size={16} />
        </button>
        <div className="flex items-start gap-3">
          <div className="w-10 h-10 rounded-xl bg-violet-100 flex items-center justify-center flex-shrink-0">
            <Cookie size={20} className="text-violet-600" />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-foreground font-semibold text-sm">Cookie Notice</h3>
            <p className="text-muted-foreground text-xs mt-0.5 leading-relaxed">
              We use cookies to improve your experience and for analytics.{' '}
              <Link to="/privacy" className="underline hover:text-foreground transition-colors">Learn more</Link>
            </p>
            <div className="flex gap-2 mt-3">
              <button onClick={handleDecline} className="h-8 px-3 rounded-lg bg-muted hover:bg-muted/80 text-muted-foreground text-xs font-medium transition-colors">Decline</button>
              <button onClick={handleAccept} className="h-8 px-4 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-xs font-semibold transition-colors flex items-center gap-1.5">
                Accept
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
