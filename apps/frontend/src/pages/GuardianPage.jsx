/**
 * GuardianPage — parental controls.
 *
 * Surfaces:
 *  · Strict Mode toggle (PIN-required to disable, when a PIN is set)
 *  · Set / change guardian PIN
 *  · Quick links to Notebook and Flashcards
 */
import { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  ShieldCheck, Lock, Loader2, Save, Eye, EyeOff,
  NotebookPen, Sparkles,
} from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import { PageTitle } from '@/components/PageTitle';
import { studyApi } from '@/utils/studyApi';
import { useStrictMode } from '@/hooks/useStrictMode';
import PinResetBanner, { pinResetClear } from '@/components/PinResetBanner';
import { toast } from 'sonner';

export default function GuardianPage() {
  const { strict, setStrict, loading: stLoading, guardianLocked } = useStrictMode();
  const [pinSet, setPinSet] = useState(false);
  const [unlockPin, setUnlockPin] = useState('');
  const [unlockBusy, setUnlockBusy] = useState(false);
  const [hasPin, setHasPin] = useState(false);

  const [currentPin, setCurrentPin] = useState('');
  const [newPin, setNewPin] = useState('');
  const [confirmPin, setConfirmPin] = useState('');
  const [showPin, setShowPin] = useState(false);
  const [pinBusy, setPinBusy] = useState(false);

  const refresh = useCallback(() => {
    studyApi.getSettings()
      .then((s) => { setHasPin(!!s?.has_pin); })
      .catch(() => {});
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const onToggle = async (next) => {
    if (!next && hasPin) {
      const pin = window.prompt('Enter guardian PIN to disable Strict Mode');
      if (!pin) return;
      const r = await setStrict(false, pin);
      if (!r.ok) toast.error('Wrong PIN');
      else toast.success('Strict Mode off');
      return;
    }
    const r = await setStrict(next);
    if (!r.ok) toast.error(r.code || 'Update failed');
    else toast.success(next ? 'Strict Mode on' : 'Strict Mode off');
  };

  const onPinSubmit = async (e) => {
    e.preventDefault();
    if (newPin.length < 4) return toast.error('PIN must be at least 4 digits');
    if (newPin !== confirmPin) return toast.error('PINs do not match');
    setPinBusy(true);
    try {
      await studyApi.setPin(newPin, currentPin);
      toast.success('PIN updated');
      setCurrentPin(''); setNewPin(''); setConfirmPin('');
      pinResetClear();
      refresh();
    } catch (e) {
      toast.error(e.message || 'Could not update PIN');
    } finally { setPinBusy(false); }
  };

  return (
    <AppLayout>
      <PageTitle title="Guardian Controls · Syrabit.ai" />
      <div className="max-w-2xl mx-auto px-4 py-6 space-y-5">
        <header>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <ShieldCheck className="w-6 h-6 text-primary" /> Guardian Controls
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Configure Strict Mode and a 4–8 digit PIN that protects these settings.
          </p>
        </header>

        <PinResetBanner variant="full" />

        <section className="rounded-2xl border border-border/60 bg-card p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-base font-semibold">Strict Mode</h2>
              <p className="text-sm text-muted-foreground mt-1">
                Restricts the educational browser to the curated allowlist, hides
                external links in answers, and applies an extra content-safety
                check on AI responses.
              </p>
            </div>
            <button
              role="switch" aria-checked={strict}
              onClick={() => onToggle(!strict)}
              disabled={stLoading}
              className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors ${strict ? 'bg-primary' : 'bg-muted'}`}
            >
              <span className={`inline-block h-6 w-6 transform rounded-full bg-white shadow transition-transform ${strict ? 'translate-x-5' : 'translate-x-1'}`} />
            </button>
          </div>
          {hasPin && (
            <p className="text-xs text-muted-foreground mt-3 inline-flex items-center gap-1">
              <Lock className="w-3 h-3" /> PIN required to turn Strict Mode off.
            </p>
          )}
        </section>

        <section className="rounded-2xl border border-border/60 bg-card p-5">
          <h2 className="text-base font-semibold flex items-center gap-2">
            <Lock className="w-4 h-4" /> Guardian PIN
          </h2>
          <p className="text-sm text-muted-foreground mt-1 mb-4">
            {hasPin ? 'Update the existing guardian PIN.' : 'Set a guardian PIN (4–8 digits).'}
          </p>
          <form onSubmit={onPinSubmit} className="space-y-3">
            {hasPin && (
              <input
                type={showPin ? 'text' : 'password'}
                inputMode="numeric" pattern="[0-9]*" maxLength={8}
                value={currentPin} onChange={(e) => setCurrentPin(e.target.value.replace(/\D/g, ''))}
                placeholder="Current PIN"
                className="w-full px-3 py-2 rounded-xl border border-border/60 bg-background text-sm"
                required
              />
            )}
            <input
              type={showPin ? 'text' : 'password'}
              inputMode="numeric" pattern="[0-9]*" maxLength={8}
              value={newPin} onChange={(e) => setNewPin(e.target.value.replace(/\D/g, ''))}
              placeholder="New PIN"
              className="w-full px-3 py-2 rounded-xl border border-border/60 bg-background text-sm"
              required
            />
            <input
              type={showPin ? 'text' : 'password'}
              inputMode="numeric" pattern="[0-9]*" maxLength={8}
              value={confirmPin} onChange={(e) => setConfirmPin(e.target.value.replace(/\D/g, ''))}
              placeholder="Confirm new PIN"
              className="w-full px-3 py-2 rounded-xl border border-border/60 bg-background text-sm"
              required
            />
            <div className="flex items-center justify-between">
              <button type="button" onClick={() => setShowPin(s => !s)}
                      className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1">
                {showPin ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
                {showPin ? 'Hide' : 'Show'} PIN
              </button>
              <button
                type="submit" disabled={pinBusy}
                className="inline-flex items-center gap-1.5 text-sm font-semibold px-3 py-2 rounded-xl bg-primary text-primary-foreground disabled:opacity-50"
              >
                {pinBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {hasPin ? 'Update PIN' : 'Set PIN'}
              </button>
            </div>
          </form>
        </section>

        <section className="grid grid-cols-2 gap-3">
          <Link to="/notebook" className="rounded-2xl border border-border/60 bg-card p-4 hover:bg-muted/40 transition-colors">
            <NotebookPen className="w-5 h-5 text-primary mb-2" />
            <div className="font-semibold text-sm">Notebook</div>
            <div className="text-xs text-muted-foreground">Saved highlights</div>
          </Link>
          <Link to="/flashcards" className="rounded-2xl border border-border/60 bg-card p-4 hover:bg-muted/40 transition-colors">
            <Sparkles className="w-5 h-5 text-primary mb-2" />
            <div className="font-semibold text-sm">Flashcards</div>
            <div className="text-xs text-muted-foreground">Spaced repetition</div>
          </Link>
        </section>
      </div>
    </AppLayout>
  );
}
