/**
 * FlashcardsPage — spaced-repetition review surface (SM-2).
 *
 * Pulls due cards, shows the front, lets the learner self-rate Again/
 * Hard/Good/Easy, and posts the result. Streak banner across the top.
 */
import { useEffect, useState, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import {
  Loader2, Flame, Sparkles, RotateCw, ChevronRight, Trophy,
} from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import { PageTitle } from '@/components/PageTitle';
import { studyApi } from '@/utils/studyApi';
import PinResetBanner from '@/components/PinResetBanner';
import { getClaimSeenAt, markClaimSeen, isRecentlyClaimed } from '@/utils/claimSeen';
import { toast } from 'sonner';

const QUALITY = [
  { q: 1, label: 'Again', color: 'bg-red-500 hover:bg-red-600' },
  { q: 3, label: 'Hard',  color: 'bg-amber-500 hover:bg-amber-600' },
  { q: 4, label: 'Good',  color: 'bg-emerald-500 hover:bg-emerald-600' },
  { q: 5, label: 'Easy',  color: 'bg-sky-500 hover:bg-sky-600' },
];

export default function FlashcardsPage() {
  const [cards, setCards] = useState([]);
  const [idx, setIdx] = useState(0);
  const [showBack, setShowBack] = useState(false);
  const [loading, setLoading] = useState(true);
  const [streak, setStreak] = useState({ current_streak: 0, best_streak: 0, today: 0 });
  const [building, setBuilding] = useState(false);
  const [reviewed, setReviewed] = useState(0);
  // Snapshot the per-surface high-water mark at mount; only advance it
  // on unmount so badges remain visible the entire time the page is
  // open, regardless of "Reload deck" / build-more cycles.
  const [seenAt] = useState(() => getClaimSeenAt('cards'));
  const maxClaimedRef = useRef('');

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([studyApi.dueFlashcards(40), studyApi.streak()])
      .then(([d, s]) => {
        setCards(d.cards || []);
        setIdx(0); setShowBack(false); setReviewed(0);
        setStreak(s || streak);
      })
      .catch((e) => toast.error(e.message || 'Failed to load cards'))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const m = cards.reduce(
      (acc, c) => (c.claimed_at && c.claimed_at > acc ? c.claimed_at : acc),
      maxClaimedRef.current);
    maxClaimedRef.current = m;
  }, [cards]);

  useEffect(() => () => {
    if (maxClaimedRef.current) markClaimSeen('cards', maxClaimedRef.current);
  }, []);

  const card = cards[idx];
  const cardRecentlySynced = card && isRecentlyClaimed(card.claimed_at, seenAt);

  const grade = async (quality) => {
    if (!card) return;
    try {
      await studyApi.reviewFlashcard(card.id, quality);
      setReviewed((n) => n + 1);
      setShowBack(false);
      if (idx + 1 >= cards.length) {
        const s = await studyApi.streak().catch(() => streak);
        setStreak(s);
        setIdx(idx + 1);
      } else setIdx(idx + 1);
    } catch (e) { toast.error(e.message || 'Review failed'); }
  };

  const buildMore = async () => {
    setBuilding(true);
    try {
      const r = await studyApi.buildFlashcards();
      toast.success(`Created ${r.created || 0} flashcards`);
      load();
    } catch (e) { toast.error(e.message || 'Could not build flashcards'); }
    finally { setBuilding(false); }
  };

  return (
    <AppLayout>
      <PageTitle title="Flashcards · Syrabit.ai" />
      <div className="max-w-2xl mx-auto px-4 py-6">
        <div className="mb-3"><PinResetBanner /></div>
        <header className="mb-5">
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Sparkles className="w-6 h-6 text-primary" /> Flashcards
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Spaced repetition built from your notebook highlights.
          </p>
        </header>

        <div className="grid grid-cols-3 gap-2 mb-5">
          <div className="rounded-xl border border-border/60 bg-card p-3">
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Streak</div>
            <div className="text-xl font-bold flex items-center gap-1">
              <Flame className="w-4 h-4 text-orange-500" /> {streak.current_streak || 0}
            </div>
          </div>
          <div className="rounded-xl border border-border/60 bg-card p-3">
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Best</div>
            <div className="text-xl font-bold flex items-center gap-1">
              <Trophy className="w-4 h-4 text-amber-500" /> {streak.best_streak || 0}
            </div>
          </div>
          <div className="rounded-xl border border-border/60 bg-card p-3">
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Today</div>
            <div className="text-xl font-bold">{(streak.today || 0) + reviewed}</div>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20 text-muted-foreground">
            <Loader2 className="w-5 h-5 animate-spin mr-2" /> Loading cards…
          </div>
        ) : cards.length === 0 ? (
          <div className="text-center py-16 border border-dashed border-border/60 rounded-2xl">
            <Sparkles className="w-10 h-10 mx-auto mb-3 text-muted-foreground" />
            <p className="text-sm text-muted-foreground mb-3">
              No cards due. Save some notes, then build a deck.
            </p>
            <div className="flex justify-center gap-2">
              <Link to="/notebook" className="text-sm font-medium text-primary hover:underline">Open Notebook →</Link>
              <button
                onClick={buildMore}
                disabled={building}
                className="text-sm font-medium px-3 py-1.5 rounded-lg bg-primary text-primary-foreground disabled:opacity-50"
              >
                {building ? 'Building…' : 'Build from notes'}
              </button>
            </div>
          </div>
        ) : idx >= cards.length ? (
          <div className="text-center py-16 border border-dashed border-border/60 rounded-2xl">
            <Trophy className="w-10 h-10 mx-auto mb-3 text-amber-500" />
            <p className="text-sm font-medium">All caught up!</p>
            <p className="text-xs text-muted-foreground mb-3">You reviewed {reviewed} card{reviewed === 1 ? '' : 's'}.</p>
            <button onClick={load} className="text-sm font-medium text-primary hover:underline">Reload</button>
          </div>
        ) : (
          <>
            <div className="text-xs text-muted-foreground mb-2">
              Card {idx + 1} of {cards.length}
            </div>
            <div className="rounded-2xl border border-border/60 bg-card shadow-sm min-h-[260px] p-6 flex flex-col">
              {cardRecentlySynced && (
                <div className="self-start mb-2 inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border border-emerald-500/20">
                  <Sparkles className="w-3 h-3" /> Recently synced
                </div>
              )}
              <div className="flex-1 text-base whitespace-pre-wrap leading-relaxed">
                <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
                  {showBack ? 'Answer' : 'Prompt'}
                </div>
                {showBack ? card.back : card.front}
              </div>
              {!showBack ? (
                <button
                  onClick={() => setShowBack(true)}
                  className="self-end mt-4 inline-flex items-center gap-1 text-sm font-semibold px-3 py-2 rounded-xl bg-primary text-primary-foreground"
                >
                  Show answer <ChevronRight className="w-4 h-4" />
                </button>
              ) : (
                <div className="mt-5 grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {QUALITY.map(({ q, label, color }) => (
                    <button
                      key={q}
                      onClick={() => grade(q)}
                      className={`text-white text-sm font-semibold py-2 rounded-xl ${color}`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="mt-3 text-right">
              <button onClick={load} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
                <RotateCw className="w-3 h-3" /> Reload deck
              </button>
            </div>
          </>
        )}
      </div>
    </AppLayout>
  );
}
