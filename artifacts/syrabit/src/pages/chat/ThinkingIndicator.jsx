import { useState, useEffect, useRef, useMemo } from 'react';

/**
 * Single-line thinking indicator that cycles through three search phases:
 *   Phase 0 → "Searching Assam Board Syllabus"   (shown for ≥ 0.8 s)
 *   Phase 1 → "Searching <Subject Name>"          (shown for ≥ 0.8 s)
 *   Phase 2 → "Searching <Chapter Name>"          (cycles until response)
 *
 * Each phase replaces the previous with a quick fade-out / fade-in.
 * Discovery events from the backend can advance phases early, but the
 * 0.8-second minimum per phase is always respected.
 */

const MIN_PHASE_MS    = 800;  // each phase visible for at least 0.8 s
const PHASE_SCHEDULE  = [800, 1800]; // default timers: advance at 0.8 s, then at 1.8 s

const DOT_KEYFRAMES = `
@keyframes syra-think-pulse {
  0%, 100% { opacity: 1;    transform: scale(1);   }
  50%       { opacity: 0.3; transform: scale(0.55); }
}
`;

const DISCOVERY_EVENT_MAP = {
  'discovery:subject': 1,
  'discovery:chapter': 2,
};

export function ThinkingIndicator({
  subject         = null,
  scopedChapters  = [],
  chapterMatch    = null,
  discoveryEvents = [],
}) {
  const [phase,    setPhase]    = useState(0);
  const [fading,   setFading]   = useState(false);
  const [chapIdx,  setChapIdx]  = useState(0);

  // Track current phase + when it started so we can honour the 2-s minimum.
  const phaseRef      = useRef(0);
  const phaseStartRef = useRef(Date.now());
  const pendingRef    = useRef(null); // timeout id for the scheduled fade

  // Chapters sorted by order so the cycling is predictable.
  const sortedChapters = useMemo(
    () => [...(scopedChapters || [])].sort(
      (a, b) => (a.order_index ?? a.order ?? 0) - (b.order_index ?? b.order ?? 0),
    ),
    [scopedChapters],
  );

  // ----- label derivation ------------------------------------------------

  const chapterLabel = useMemo(() => {
    if (chapterMatch) {
      const num   = chapterMatch.chapter_number;
      const title = chapterMatch.chapter_title;
      return num ? `Chapter\u00a0${num}\u00a0\u2014\u00a0${title}` : title;
    }
    if (!sortedChapters.length) return 'relevant chapters';
    const ch  = sortedChapters[chapIdx] || sortedChapters[0];
    const num = ch.chapter_number ?? ch.order_index ?? chapIdx + 1;
    return `Chapter\u00a0${num}\u00a0\u2014\u00a0${ch.title || ch.name || 'chapter'}`;
  }, [chapterMatch, sortedChapters, chapIdx]);

  const label = useMemo(() => {
    if (phase === 0) return 'Searching Assam Board Syllabus';
    if (phase === 1) return `Searching ${subject?.name || 'your subject'}`;
    return `Searching ${chapterLabel}`;
  }, [phase, subject, chapterLabel]);

  // ----- phase advancement -----------------------------------------------

  /** Schedule a transition to `target` phase, respecting the 2-s minimum. */
  const scheduleAdvance = (target) => {
    if (phaseRef.current >= target) return; // already there
    const elapsed = Date.now() - phaseStartRef.current;
    const wait    = Math.max(0, MIN_PHASE_MS - elapsed);

    if (pendingRef.current !== null) return; // a transition is already queued

    pendingRef.current = setTimeout(() => {
      pendingRef.current = null;
      if (phaseRef.current >= target) return;

      // Fade out → swap → fade in
      setFading(true);
      setTimeout(() => {
        phaseRef.current    = target;
        phaseStartRef.current = Date.now();
        setPhase(target);
        setFading(false);
      }, 220);
    }, wait);
  };

  // Timer-based fallback: advance phases on a fixed schedule.
  useEffect(() => {
    const t0 = setTimeout(() => scheduleAdvance(1), PHASE_SCHEDULE[0]);
    const t1 = setTimeout(() => scheduleAdvance(2), PHASE_SCHEDULE[1]);
    return () => {
      clearTimeout(t0);
      clearTimeout(t1);
      if (pendingRef.current !== null) clearTimeout(pendingRef.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Discovery event-driven advance (backend signals which phase to jump to).
  useEffect(() => {
    for (const ev of discoveryEvents) {
      const target = DISCOVERY_EVENT_MAP[ev.event];
      if (target !== undefined && target > phaseRef.current) {
        scheduleAdvance(target);
      }
    }
  }, [discoveryEvents]); // eslint-disable-line react-hooks/exhaustive-deps

  // WAI chapter match → immediately signal phase 2.
  useEffect(() => {
    if (chapterMatch) scheduleAdvance(2);
  }, [chapterMatch]); // eslint-disable-line react-hooks/exhaustive-deps

  // Cycle through chapters while on phase 2 (until real match arrives).
  useEffect(() => {
    if (phase !== 2 || chapterMatch || sortedChapters.length < 2) return;
    const t = setInterval(
      () => setChapIdx((i) => (i + 1) % sortedChapters.length),
      2000,
    );
    return () => clearInterval(t);
  }, [phase, chapterMatch, sortedChapters]);

  // ----- render ----------------------------------------------------------

  return (
    <>
      <style>{DOT_KEYFRAMES}</style>
      <div
        style={{
          display:    'flex',
          alignItems: 'center',
          gap:        8,
          padding:    '6px 0',
          userSelect: 'none',
        }}
      >
        {/* Animated pulse dot */}
        <span
          aria-hidden="true"
          style={{
            display:      'inline-block',
            width:        7,
            height:       7,
            borderRadius: '50%',
            flexShrink:   0,
            background:   '#7c3aed',
            animation:    'syra-think-pulse 1.1s ease-in-out infinite',
          }}
        />

        {/* Fading label */}
        <span
          style={{
            fontSize:     13,
            lineHeight:   '20px',
            fontWeight:   500,
            color:        '#5b21b6',
            overflow:     'hidden',
            textOverflow: 'ellipsis',
            whiteSpace:   'nowrap',
            opacity:      fading ? 0 : 1,
            transition:   'opacity 0.22s ease',
          }}
        >
          {label}
          <span style={{ opacity: 0.45, marginLeft: 1 }}>\u2026</span>
        </span>
      </div>
    </>
  );
}
