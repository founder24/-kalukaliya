import { useEffect, useState } from 'react';
import AdSlot from './AdSlot';
import { adsConsentGranted, getAdConfig } from '@/utils/adsConfig';

export default function ChatSponsoredCard({ placementIndex }) {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    const apply = () => setAvailable(getAdConfig('chat.afterAssistant').enabled && adsConsentGranted());
    apply();
    window.addEventListener('syrabit:ads-consent-changed', apply);
    return () => window.removeEventListener('syrabit:ads-consent-changed', apply);
  }, []);
  if (!available) return null;
  return (
    <aside
      className="my-5 rounded-2xl border border-amber-200/70 bg-amber-50/70 px-3 py-2.5 sm:px-4"
      aria-label="Sponsored content"
      data-testid={`chat-sponsored-${placementIndex}`}
    >
      <div className="mb-1 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-amber-800/75">
        <span className="h-1.5 w-1.5 rounded-full bg-amber-500" aria-hidden="true" />
        Sponsored
      </div>
      <p className="mb-2 text-xs text-amber-950/65">Learning stays free with support from our sponsors.</p>
      <AdSlot placement="chat.afterAssistant" className="!my-0" />
    </aside>
  );
}