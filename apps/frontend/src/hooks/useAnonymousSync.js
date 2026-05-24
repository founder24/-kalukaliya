import { useEffect } from 'react';
import { toast } from 'sonner';
import { studyApi } from '@/utils/studyApi';
import { pinResetMarkNeeded } from '@/utils/pinReset';

export function useAnonymousSync(userId) {
  useEffect(() => {
    if (!userId) return;
    if (typeof window === 'undefined') return;

    let anonId = '';
    try { anonId = localStorage.getItem('syrabit_anon_id') || ''; } catch {}
    if (!anonId || anonId === userId) return;

    const toastFlagKey = `syrabit:claimed_toast:${anonId}->${userId}`;
    let cancelled = false;

    (async () => {
      try {
        const res = await studyApi.claimAnonData();
        if (cancelled) return;
        const moved = (res?.notes || 0) + (res?.flashcards || 0) + (res?.settings_merged ? 1 : 0);

        if (res?.pin_dropped) {
          try { pinResetMarkNeeded(); } catch {}
        }

        let alreadyToasted = false;
        try { alreadyToasted = !!sessionStorage.getItem(toastFlagKey); } catch {}

        if (moved > 0 && !alreadyToasted) {
          try { sessionStorage.setItem(toastFlagKey, '1'); } catch {}
          const parts = [];
          if (res.notes) parts.push(`${res.notes} note${res.notes === 1 ? '' : 's'}`);
          if (res.flashcards) parts.push(`${res.flashcards} flashcard${res.flashcards === 1 ? '' : 's'}`);
          const detail = parts.length ? ` (${parts.join(' & ')})` : '';
          try {
            toast.success(`Your offline study items are now synced to your account${detail}.`);
          } catch {}
        }
      } catch {
        // Silent - sync will be retried on next sign-in
      }
    })();

    return () => { cancelled = true; };
  }, [userId]);
}
