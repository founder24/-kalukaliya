import { useState, useEffect, useRef, useCallback } from 'react';
import { GraduationCap, ExternalLink, BookOpen, ArrowLeft, Clock, Share2, MousePointer2 } from 'lucide-react';
import { LogoMark } from '@/components/Logo';

const _demo = {
  en: {
    studentMsg: "Explain the photoelectric effect and Einstein's equation",
    aiAnswer: 'The photoelectric effect is the emission of electrons from a metal surface when light of sufficient frequency strikes it.',
    thinking: 'Thinking',
    sourceBadge: 'Chapter 11 · Wave Optics',
    backToChat: 'Back to chat',
    pageTitle: 'Photoelectric Effect',
    pageSub: 'AHSEC Class 12 · Physics · Chapter 11',
    syllabusBadge: 'AHSEC Syllabus',
    pageDesc: 'The photoelectric effect occurs when electromagnetic radiation (light) of sufficient frequency strikes a metallic surface, causing the emission of electrons. This phenomenon was first explained by Albert Einstein in 1905 using the quantum theory of light.',
    eqTitle: "Einstein's Photoelectric Equation",
    eqLabels: [
      ['E', 'energy of incident photon'],
      ['h', "Planck's constant (6.626 × 10⁻³⁴ J·s)"],
      ['ν', 'frequency of incident light'],
      ['φ', 'work function of the metal'],
      ['½mv²', 'max kinetic energy of photoelectron'],
    ],
    creditsUsed: '2 credits used',
    tag1: 'Wave Optics',
    tag2: 'Quantum Theory',
  },
  as: {
    studentMsg: 'ফটোইলেক্ট্ৰিক প্ৰভাৱ আৰু আইনষ্টাইনৰ সমীকৰণ ব্যাখ্যা কৰক',
    aiAnswer: 'ফটোইলেক্ট্ৰিক প্ৰভাৱ হৈছে যেতিয়া পৰ্যাপ্ত কম্পাংকৰ পোহৰ ধাতুৰ পৃষ্ঠত আঘাত কৰে তেতিয়া ইলেক্ট্ৰন নিৰ্গমন হোৱা পৰিঘটনা।',
    thinking: 'চিন্তা কৰি আছে',
    sourceBadge: 'অধ্যায় ১১ · তৰংগ আলোকবিজ্ঞান',
    backToChat: 'চেটলৈ উভতি যাওক',
    pageTitle: 'ফটোইলেক্ট্ৰিক প্ৰভাৱ',
    pageSub: 'AHSEC দ্বাদশ শ্ৰেণী · পদাৰ্থ বিজ্ঞান · অধ্যায় ১১',
    syllabusBadge: 'AHSEC পাঠ্যক্ৰম',
    pageDesc: 'ফটোইলেক্ট্ৰিক প্ৰভাৱ ঘটে যেতিয়া পৰ্যাপ্ত কম্পাংকৰ বিদ্যুৎচুম্বকীয় বিকিৰণ (পোহৰ) ধাতুৰ পৃষ্ঠত আঘাত কৰে, যাৰ ফলত ইলেক্ট্ৰন নিৰ্গমন হয়। এই পৰিঘটনা প্ৰথমে আলবাৰ্ট আইনষ্টাইনে ১৯০৫ চনত পোহৰৰ কোৱান্টাম তত্ত্ব ব্যৱহাৰ কৰি ব্যাখ্যা কৰিছিল।',
    eqTitle: 'আইনষ্টাইনৰ ফটোইলেক্ট্ৰিক সমীকৰণ',
    eqLabels: [
      ['E', 'আপতিত ফটনৰ শক্তি'],
      ['h', 'প্লাংকৰ ধ্ৰুৱক (6.626 × 10⁻³⁴ J·s)'],
      ['ν', 'আপতিত পোহৰৰ কম্পাংক'],
      ['φ', 'ধাতুৰ কাৰ্য-ফলন'],
      ['½mv²', 'ফটোইলেক্ট্ৰনৰ সৰ্বোচ্চ গতিশক্তি'],
    ],
    creditsUsed: '২ ক্ৰেডিট ব্যৱহৃত',
    tag1: 'তৰংগ আলোকবিজ্ঞান',
    tag2: 'কোৱান্টাম তত্ত্ব',
  },
};

const TYPING_SPEED = 35;
const THINKING_DURATION = 3000;
const CLICK_DELAY = 2500;
const LOOP_PAUSE = 5000;
const FADE_DURATION = 600;

const CLICKING_DURATION = 800;

const PHASES = {
  IDLE: 'idle',
  TYPING: 'typing',
  THINKING: 'thinking',
  SHORT_ANSWER: 'short_answer',
  CLICKING: 'clicking',
  PAGE_VIEW: 'page_view',
  COMPLETE: 'complete',
  FADING: 'fading',
};

function TypingCursor() {
  return (
    <span
      className="inline-block w-0.5 h-3.5 ml-0.5 align-middle"
      style={{
        background: 'rgba(255,255,255,0.60)',
        animation: 'blink 1.2s ease-in-out infinite',
      }}
    />
  );
}

function ThinkingIndicator({ label }) {
  return (
    <div className="space-y-2 py-1" style={{ width: '100%', maxWidth: 180 }}>
      <div className="flex items-center gap-1.5 mb-2">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="w-1.5 h-1.5 rounded-full"
            style={{
              background: '#a78bfa',
              animation: 'chatDotPulse 0.8s ease-in-out infinite',
              animationDelay: `${i * 0.15}s`,
            }}
          />
        ))}
        <span className="text-xs ml-1" style={{ color: 'rgba(255,255,255,0.60)' }}>{label}</span>
      </div>
      {[1, 0.7, 0.4].map((widthFrac, i) => (
        <div
          key={i}
          className="rounded"
          style={{
            height: 8,
            width: `${widthFrac * 100}%`,
            background: 'linear-gradient(90deg, rgba(139,92,246,0.08) 25%, rgba(139,92,246,0.18) 50%, rgba(139,92,246,0.08) 75%)',
            backgroundSize: '200% 100%',
            animation: 'chatBarShimmer 1.5s linear infinite',
            animationDelay: `${i * 0.15}s`,
          }}
        />
      ))}
    </div>
  );
}

function SourceBadge({ pulsing, clicking, label }) {
  let wrapperAnim = 'none';
  if (clicking) {
    wrapperAnim = 'chatBadgeClick 0.35s ease-in-out';
  } else if (pulsing) {
    wrapperAnim = 'chatBadgePulse 0.8s ease-in-out 2';
  }
  return (
    <div
      className="relative flex items-center gap-1.5 mt-2"
      style={{ animation: wrapperAnim }}
    >
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full relative overflow-hidden"
        style={{
          fontSize: 12,
          background: clicking ? 'rgba(139,92,246,0.45)' : 'rgba(139,92,246,0.20)',
          color: '#a78bfa',
          border: clicking ? '1px solid rgba(139,92,246,0.50)' : '1px solid rgba(139,92,246,0.22)',
          transition: 'background 0.2s, border 0.2s',
          boxShadow: clicking ? '0 0 12px rgba(139,92,246,0.4)' : 'none',
        }}
      >
        <BookOpen size={9} />
        {label}
        {clicking && (
          <span
            className="absolute inset-0 rounded-full"
            style={{
              background: 'rgba(167,139,250,0.4)',
              transformOrigin: 'center',
              animation: 'chatRipple 0.5s ease-out both',
            }}
          />
        )}
      </span>
      <ExternalLink size={10} style={{ color: '#a78bfa', opacity: 0.7 }} />

      {clicking && (
        <div
          className="absolute"
          style={{
            zIndex: 10,
            left: 0,
            top: 0,
            animation: 'chatCursorIn 0.3s ease-out both',
          }}
        >
          <MousePointer2 size={14} style={{ color: 'white', filter: 'drop-shadow(0 1px 3px rgba(0,0,0,0.5))' }} />
        </div>
      )}
    </div>
  );
}

const slideUpAnim = 'slideUp 0.3s ease-out both';
const fadeInAnim = 'fadeIn 0.3s ease-out both';

function ContentPageView({ t }) {
  return (
    <div className="p-5 space-y-4">
      <div
        className="flex items-center gap-2"
        style={{ animation: fadeInAnim, animationDelay: '0.1s' }}
      >
        <ArrowLeft size={14} style={{ color: 'rgba(255,255,255,0.60)' }} />
        <span className="text-xs" style={{ color: 'rgba(255,255,255,0.60)' }}>{t.backToChat}</span>
      </div>

      <div
        className="flex items-start justify-between"
        style={{ animation: slideUpAnim, animationDelay: '0.15s' }}
      >
        <div>
          <h3 className="text-sm font-bold text-white mb-1">{t.pageTitle}</h3>
          <p className="text-xs" style={{ color: 'rgba(255,255,255,0.60)' }}>{t.pageSub}</p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full"
            style={{ fontSize: 12, background: 'rgba(34,197,94,0.12)', color: 'rgba(34,197,94,0.7)', border: '1px solid rgba(34,197,94,0.15)' }}
          >
            {t.syllabusBadge}
          </span>
          <Share2 size={11} style={{ color: 'rgba(255,255,255,0.60)' }} />
        </div>
      </div>

      <div
        style={{
          height: 1,
          background: 'rgba(255,255,255,0.06)',
          animation: fadeInAnim,
          animationDelay: '0.25s',
        }}
      />

      <div style={{ animation: slideUpAnim, animationDelay: '0.3s' }}>
        <p className="text-xs leading-relaxed mb-3" style={{ color: 'rgba(255,255,255,0.65)' }}>
          {t.pageDesc}
        </p>
      </div>

      <div
        className="rounded-lg p-3"
        style={{
          background: 'rgba(139,92,246,0.08)',
          border: '1px solid rgba(139,92,246,0.12)',
          animation: slideUpAnim,
          animationDelay: '0.45s',
        }}
      >
        <p className="text-xs font-semibold mb-2" style={{ color: 'rgba(255,255,255,0.60)' }}>{t.eqTitle}</p>
        <code
          className="text-sm font-mono block text-center py-1"
          style={{ color: '#c4b5fd' }}
        >
          E = hν = φ + ½mv²
        </code>
        <div className="mt-2 space-y-0.5">
          {t.eqLabels.map(([sym, desc]) => (
            <p key={sym} className="text-xs" style={{ color: 'rgba(255,255,255,0.60)' }}><span style={{ color: '#a78bfa' }}>{sym}</span> = {desc}</p>
          ))}
        </div>
      </div>

      <div
        className="flex items-center gap-3 pt-1"
        style={{ animation: fadeInAnim, animationDelay: '0.6s' }}
      >
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full"
          style={{ fontSize: 12, background: 'rgba(139,92,246,0.15)', color: '#a78bfa', border: '1px solid rgba(139,92,246,0.18)' }}
        >
          <Clock size={10} />
          {t.creditsUsed}
        </span>
        <span
          className="px-2 py-0.5 rounded-full"
          style={{ fontSize: 12, background: 'rgba(255,255,255,0.04)', color: 'rgba(255,255,255,0.60)', border: '1px solid rgba(255,255,255,0.06)' }}
        >
          {t.tag1}
        </span>
        <span
          className="px-2 py-0.5 rounded-full"
          style={{ fontSize: 12, background: 'rgba(255,255,255,0.04)', color: 'rgba(255,255,255,0.60)', border: '1px solid rgba(255,255,255,0.06)' }}
        >
          {t.tag2}
        </span>
      </div>
    </div>
  );
}

export default function AnimatedChatDemo({ onUrlChange, contentLang = 'en' }) {
  const t = _demo[contentLang] || _demo.en;
  const STUDENT_MESSAGE = t.studentMsg;
  const AI_SHORT_ANSWER = t.aiAnswer;
  const [phase, setPhase] = useState(PHASES.IDLE);
  const [typedCount, setTypedCount] = useState(0);
  const [shortAnswerTyped, setShortAnswerTyped] = useState(0);
  const [containerOpacity, setContainerOpacity] = useState(1);
  const [isInView, setIsInView] = useState(false);
  const containerRef = useRef(null);
  const timeoutRef = useRef(null);
  const intervalRef = useRef(null);

  const clearTimers = useCallback(() => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    if (intervalRef.current) clearInterval(intervalRef.current);
  }, []);

  const resetState = useCallback(() => {
    clearTimers();
    setPhase(PHASES.IDLE);
    setTypedCount(0);
    setShortAnswerTyped(0);
    setContainerOpacity(1);
    onUrlChange?.('chat');
  }, [clearTimers, onUrlChange]);

  useEffect(() => {
    resetState();
  }, [contentLang, resetState]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => setIsInView(entry.isIntersecting),
      { threshold: 0.3 }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (isInView && phase === PHASES.IDLE) {
      timeoutRef.current = setTimeout(() => setPhase(PHASES.TYPING), 800);
    }
    if (!isInView && phase !== PHASES.IDLE) {
      resetState();
    }
    return () => { if (timeoutRef.current) clearTimeout(timeoutRef.current); };
  }, [isInView, phase, resetState]);

  useEffect(() => {
    if (phase !== PHASES.TYPING) return;
    intervalRef.current = setInterval(() => {
      setTypedCount((prev) => {
        if (prev >= STUDENT_MESSAGE.length) {
          clearInterval(intervalRef.current);
          timeoutRef.current = setTimeout(() => setPhase(PHASES.THINKING), 400);
          return prev;
        }
        return prev + 1;
      });
    }, TYPING_SPEED);
    return () => clearInterval(intervalRef.current);
  }, [phase]);

  useEffect(() => {
    if (phase !== PHASES.THINKING) return;
    timeoutRef.current = setTimeout(() => setPhase(PHASES.SHORT_ANSWER), THINKING_DURATION);
    return () => clearTimeout(timeoutRef.current);
  }, [phase]);

  useEffect(() => {
    if (phase !== PHASES.SHORT_ANSWER) return;
    intervalRef.current = setInterval(() => {
      setShortAnswerTyped((prev) => {
        if (prev >= AI_SHORT_ANSWER.length) {
          clearInterval(intervalRef.current);
          timeoutRef.current = setTimeout(() => {
            setPhase(PHASES.CLICKING);
          }, CLICK_DELAY);
          return prev;
        }
        return prev + 2;
      });
    }, 18);
    return () => { clearInterval(intervalRef.current); if (timeoutRef.current) clearTimeout(timeoutRef.current); };
  }, [phase, onUrlChange]);

  useEffect(() => {
    if (phase !== PHASES.CLICKING) return;
    timeoutRef.current = setTimeout(() => {
      onUrlChange?.('library');
      setPhase(PHASES.PAGE_VIEW);
    }, CLICKING_DURATION);
    return () => clearTimeout(timeoutRef.current);
  }, [phase, onUrlChange]);

  useEffect(() => {
    if (phase !== PHASES.PAGE_VIEW) return;
    timeoutRef.current = setTimeout(() => setPhase(PHASES.COMPLETE), 400);
    return () => clearTimeout(timeoutRef.current);
  }, [phase]);

  useEffect(() => {
    if (phase !== PHASES.COMPLETE) return;
    timeoutRef.current = setTimeout(() => {
      setPhase(PHASES.FADING);
      setContainerOpacity(0);
      timeoutRef.current = setTimeout(() => resetState(), FADE_DURATION);
    }, LOOP_PAUSE);
    return () => clearTimeout(timeoutRef.current);
  }, [phase, resetState]);

  const showStudent = phase !== PHASES.IDLE && phase !== PHASES.PAGE_VIEW && phase !== PHASES.COMPLETE && phase !== PHASES.FADING;
  const showThinking = phase === PHASES.THINKING;
  const showShortAnswer = phase === PHASES.SHORT_ANSWER;
  const showPageView = phase === PHASES.PAGE_VIEW || phase === PHASES.COMPLETE || phase === PHASES.FADING;

  const showClicking = phase === PHASES.CLICKING;

  const aiBubbleStyle = {
    background: 'linear-gradient(135deg,rgba(124,58,237,0.22),rgba(109,40,217,0.16))',
    border: '1px solid rgba(139,92,246,0.22)',
    borderRadius: '0 1rem 1rem 1rem',
  };

  return (
    <div
      ref={containerRef}
      style={{ minHeight: 200, transition: `opacity ${FADE_DURATION}ms ease`, opacity: containerOpacity }}
    >
      {!showPageView ? (
        <div key="chat-view" className="p-6 text-left space-y-4">
          {showStudent && (
            <div
              className="flex items-start gap-3 flex-row-reverse"
              style={{ animation: slideUpAnim }}
            >
              <div
                className="w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center"
                style={{ background: 'rgba(124,58,237,0.20)', border: '1px solid rgba(139,92,246,0.30)' }}
              >
                <GraduationCap size={14} className="text-violet-400" />
              </div>
              <div
                className="px-4 py-3 text-sm max-w-xs"
                style={{ background: 'rgba(255,255,255,0.06)', borderRadius: '1rem 0 1rem 1rem', color: 'rgba(255,255,255,0.80)' }}
              >
                {STUDENT_MESSAGE.slice(0, typedCount)}
                {phase === PHASES.TYPING && <TypingCursor />}
              </div>
            </div>
          )}

          {showThinking && (
            <div
              className="flex items-start gap-3"
              style={{ animation: slideUpAnim }}
            >
              <div
                className="w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center"
                style={{ background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)' }}
              >
                <LogoMark size="xs" style={{ filter: 'none' }} />
              </div>
              <div className="px-4 py-3 text-sm max-w-sm" style={aiBubbleStyle}>
                <ThinkingIndicator label={t.thinking} />
              </div>
            </div>
          )}

          {(showShortAnswer || showClicking) && (
            <div
              className="flex items-start gap-3"
              style={{ animation: slideUpAnim }}
            >
              <div
                className="w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center"
                style={{ background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)' }}
              >
                <LogoMark size="xs" style={{ filter: 'none' }} />
              </div>
              <div className="px-4 py-3 text-sm max-w-sm" style={aiBubbleStyle}>
                <p className="text-xs leading-relaxed" style={{ color: 'rgba(255,255,255,0.78)' }}>
                  {AI_SHORT_ANSWER.slice(0, shortAnswerTyped)}
                  {shortAnswerTyped < AI_SHORT_ANSWER.length && <TypingCursor />}
                </p>
                {shortAnswerTyped >= AI_SHORT_ANSWER.length && (
                  <div style={{ animation: slideUpAnim }}>
                    <SourceBadge pulsing={showShortAnswer} clicking={showClicking} label={t.sourceBadge} />
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div
          key="page-view"
          style={{ animation: 'chatSlideInRight 0.4s cubic-bezier(0.16, 1, 0.3, 1) both' }}
        >
          <ContentPageView t={t} />
        </div>
      )}
    </div>
  );
}
