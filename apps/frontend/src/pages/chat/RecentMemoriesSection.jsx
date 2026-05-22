import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sparkles, MessageSquare, BookCheck } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { useContentLang } from '@/context/LanguageContext';
import { getRecentMemories } from '@/utils/api';

const T = {
  en: {
    heading: 'Pick up where you left off',
    subheading: "Recent things you've explored with Syra.",
    continueLabel: 'Continue',
    masteredLabel: 'Mastered',
    emptySignedIn: 'Your recent study moments will appear here as you chat and review flashcards.',
    emptyAnon: 'Sign in to see your recent study moments here.',
  },
  as: {
    heading: 'য’ত এৰিছিলা তাৰ পৰাই আৰম্ভ কৰক',
    subheading: 'চিৰাৰ লগত শেহতীয়াকৈ অধ্যয়ন কৰা বিষয়সমূহ।',
    continueLabel: 'অব্যাহত ৰাখক',
    masteredLabel: 'আয়ত্ত কৰিছে',
    emptySignedIn: 'আপুনি কথা পাতিলে আৰু ফ্লেচকাৰ্ড পুনৰীক্ষণ কৰিলে শেহতীয়া অধ্যয়নসমূহ ইয়াত দেখা দিব।',
    emptyAnon: 'আপোনাৰ শেহতীয়া অধ্যয়নসমূহ চাবলৈ ছাইন ইন কৰক।',
  },
};

function MemoryCard({ item, t, onOpen }) {
  const isFact = item.kind === 'fact' || item.event === 'flashcard_recall';
  const Icon = isFact ? BookCheck : MessageSquare;
  const label = isFact ? t.masteredLabel : t.continueLabel;
  const subjectChip = item.subject_name || item.chapter_name || null;

  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      className="text-left p-3 rounded-xl transition-all duration-200 hover:opacity-90 active:scale-[0.99]"
      style={{
        border: '1px solid rgba(139,92,246,0.18)',
        background: 'linear-gradient(135deg,rgba(124,58,237,0.05),rgba(139,92,246,0.03))',
      }}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span
          className="inline-flex items-center justify-center w-6 h-6 rounded-md"
          style={{ background: 'rgba(124,58,237,0.12)' }}
        >
          <Icon size={13} className="text-violet-600" />
        </span>
        <span className="text-[11px] font-semibold uppercase tracking-wide text-violet-600">
          {label}
        </span>
        {subjectChip && (
          <span className="text-[11px] text-muted-foreground truncate">
            · {subjectChip}
          </span>
        )}
      </div>
      <div className="text-sm font-medium text-foreground line-clamp-2">
        {item.title || '—'}
      </div>
      {item.preview && (
        <div className="text-xs text-muted-foreground line-clamp-2 mt-1">
          {item.preview}
        </div>
      )}
    </button>
  );
}

export function RecentMemoriesSection() {
  const navigate = useNavigate();
  const { user, authChecked } = useAuth();
  const { contentLang } = useContentLang();
  const t = T[contentLang] || T.en;

  const [items, setItems] = useState([]);
  const [anon, setAnon] = useState(!user);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!authChecked) return;
    let cancelled = false;
    if (!user) {
      setItems([]);
      setAnon(true);
      setLoaded(true);
      return () => { cancelled = true; };
    }
    getRecentMemories(5)
      .then((res) => {
        if (cancelled) return;
        const data = res?.data || {};
        setItems(Array.isArray(data.items) ? data.items : []);
        setAnon(!!data.anon);
        setLoaded(true);
      })
      .catch(() => {
        // Best-effort widget — never block the empty state on a
        // memory_brain outage. Silently render nothing.
        if (cancelled) return;
        setItems([]);
        setLoaded(true);
      });
    return () => { cancelled = true; };
  }, [authChecked, user]);

  const handleOpen = (item) => {
    if (item.conversation_id) {
      navigate(`/chat?id=${encodeURIComponent(item.conversation_id)}`);
      return;
    }
    // No conversation context (e.g. flashcard fact memory). Seed the
    // chat input with the memory's question so the student can pick up
    // the thread immediately.
    if (item.title) {
      navigate('/chat', { state: { seedCardContext: item.title } });
    }
  };

  if (!authChecked || !loaded) return null;

  // Anonymous: render nothing rather than a sign-in nudge — the chat
  // empty state already nudges towards browsing the syllabus, and the
  // task spec requires a *graceful* empty state, not an upsell.
  if (anon) return null;
  if (items.length === 0) return null;

  return (
    <div className="w-full max-w-lg mx-auto pt-2">
      <div className="flex items-center gap-2 mb-2 px-1">
        <Sparkles size={14} className="text-violet-600" />
        <h3 className="text-sm font-semibold text-foreground">{t.heading}</h3>
      </div>
      <p className="text-xs text-muted-foreground mb-3 px-1">{t.subheading}</p>
      <div className="grid grid-cols-1 gap-2">
        {items.map((item) => (
          <MemoryCard key={item.id} item={item} t={t} onOpen={handleOpen} />
        ))}
      </div>
    </div>
  );
}
