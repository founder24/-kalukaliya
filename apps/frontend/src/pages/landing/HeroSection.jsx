import { useState, useCallback, lazy, Suspense } from 'react';
import { Link } from 'react-router-dom';
import { Sparkles, BookOpen } from 'lucide-react';
import { prefetchRoute } from '@/utils/prefetchRoute';

const HeroBelowFold = lazy(() => import('./HeroBelowFold'));

const _t = {
  en: {
    heroLine1: 'Educational Browser',
    heroLine2Pre: 'For ',
    heroLine2Highlight: 'AssamBoard Students',
    subtitle: 'Syrabit gives AssamBoard students (AHSEC, DEGREE & SEBA) instant, syllabus-aligned AI answers, PYQ insights, and structured subject notes — all in one place.',
    ctaPrimary: 'Browse Library',
    ctaSecondary: 'Ask Syra AI',
    ctaTertiary: 'or start free → no card needed',
    freeNote: 'Free plan · No credits needed to browse · Upgrade from ₹99',
    statDivisions: 'AssamBoard Divisions',
    statStudents: 'Students',
    statPlans: 'Plans',
  },
  as: {
    heroLine1: 'শৈক্ষিক ব্ৰাউজাৰ',
    heroLine2Pre: '',
    heroLine2Highlight: 'অসম বোৰ্ডৰ ছাত্ৰ-ছাত্ৰীৰ বাবে',
    subtitle: 'Syrabit-এ অসম বোৰ্ডৰ ছাত্ৰ-ছাত্ৰীক (AHSEC, DEGREE আৰু SEBA) তাৎক্ষণিক, পাঠ্যক্ৰম-সামঞ্জস্যপূৰ্ণ AI উত্তৰ, PYQ অন্তৰ্দৃষ্টি, আৰু গাঁথনিমূলক বিষয়ৰ টোকা প্ৰদান কৰে — সকলো এটা ঠাইতে।',
    ctaPrimary: 'লাইব্ৰেৰী ব্ৰাউজ কৰক',
    ctaSecondary: 'Syra AI ক সোধক',
    ctaTertiary: 'বা বিনামূলীয়াকৈ আৰম্ভ কৰক → কাৰ্ডৰ প্ৰয়োজন নাই',
    freeNote: 'বিনামূলীয়া পৰিকল্পনা · ব্ৰাউজ কৰিবলৈ ক্ৰেডিট নালাগে · ₹99ৰ পৰা আপগ্ৰেড',
    statDivisions: 'অসম বোৰ্ড বিভাগ',
    statStudents: 'ছাত্ৰ-ছাত্ৰী',
    statPlans: 'পৰিকল্পনা',
  },
};

const HERO_ENTRANCE = 'revealUp 0.8s cubic-bezier(0.16,1,0.3,1) both';

export default function HeroSection({ contentLang = 'en' }) {
  const t = _t[contentLang] || _t.en;
  const [browserPath, setBrowserPath] = useState('chat');
  const handleUrlChange = useCallback((path) => setBrowserPath(path), []);

  return (
    <>
      <section className="relative min-h-screen flex items-center justify-center pt-16 overflow-hidden">
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            opacity: 0.035,
            backgroundImage: 'linear-gradient(rgba(139,92,246,1) 1px,transparent 1px),linear-gradient(to right,rgba(139,92,246,1) 1px,transparent 1px)',
            backgroundSize: '60px 60px',
          }}
        />

        <div
          className="absolute inset-0 pointer-events-none"
          style={{ background: 'radial-gradient(ellipse 80% 50% at 50% -5%,rgba(124,58,237,0.12),transparent)' }}
        />

        <div className="relative z-10 max-w-5xl mx-auto px-5 text-center py-24">
          <h1
            key={contentLang + '-h1'}
            className="mb-6"
            style={{
              fontSize: 'clamp(1.75rem,5vw,5rem)',
              fontWeight: 900,
              lineHeight: 1.06,
              letterSpacing: '-0.03em',
              animation: HERO_ENTRANCE,
              animationDelay: '0.2s',
            }}
          >
            <span className="text-foreground">{t.heroLine1}</span>
            <br />
            <span>
              {t.heroLine2Pre && <span className="text-foreground">{t.heroLine2Pre}</span>}
              <span style={{
                background: 'linear-gradient(135deg,#7c3aed 0%,#a78bfa 40%,#6d28d9 80%)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
                backgroundClip: 'text',
                filter: 'drop-shadow(0 0 30px rgba(139,92,246,0.20))',
              }}>
                {t.heroLine2Highlight}
              </span>
            </span>
          </h1>

          <p
            key={contentLang + '-sub'}
            className="max-w-2xl mx-auto mb-8 leading-relaxed text-muted-foreground"
            style={{
              fontSize: 'clamp(1rem,2vw,1.18rem)',
              letterSpacing: '0.01em',
              animation: HERO_ENTRANCE,
              animationDelay: '0.35s',
            }}
          >
            {t.subtitle}
          </p>

          <div
            className="flex flex-col sm:flex-row items-center justify-center gap-3"
            style={{ animation: HERO_ENTRANCE, animationDelay: '0.48s' }}
          >
            <Link
              to="/library"
              onMouseEnter={() => prefetchRoute('/library')}
              onTouchStart={() => prefetchRoute('/library')}
              className="hero-cta-primary flex flex-wrap items-center justify-center gap-2.5 text-white font-bold btn-gradient"
              style={{
                minHeight: 54,
                padding: '0.75rem 1.5rem',
                borderRadius: '1rem',
                fontSize: 'clamp(0.875rem, 2.5vw, 1rem)',
                boxShadow: '0 8px 36px rgba(139,92,246,0.35), 0 0 0 1px rgba(139,92,246,0.15) inset',
                textAlign: 'center',
              }}
              data-testid="landing-hero-primary-cta-button"
            >
              <BookOpen size={18} className="flex-shrink-0" />
              <span>{t.ctaPrimary}</span>
            </Link>
            <Link
              to="/chat"
              onMouseEnter={() => prefetchRoute('/chat')}
              onTouchStart={() => prefetchRoute('/chat')}
              className="hero-cta-secondary flex items-center gap-2.5 font-semibold transition-all duration-200 hover:bg-violet-500/[0.06]"
              style={{
                minHeight: 54,
                padding: '0.75rem 1.5rem',
                borderRadius: '1rem',
                fontSize: 'clamp(0.875rem, 2.5vw, 1rem)',
                color: 'hsl(var(--foreground))',
                border: '1px solid hsl(var(--border))',
                background: 'hsl(var(--muted) / 0.3)',
              }}
              data-testid="landing-hero-secondary-cta-button"
            >
              <Sparkles size={15} />
              {t.ctaSecondary}
            </Link>
          </div>

          <p
            className="text-sm mt-4 text-muted-foreground"
            style={{ animation: 'fadeIn 0.8s ease 0.6s both' }}
          >
            <Link
              to="/signup"
              onMouseEnter={() => prefetchRoute('/signup')}
              className="underline-offset-4 hover:underline hover:text-violet-600 transition-colors"
              data-testid="landing-hero-tertiary-cta"
            >
              {t.ctaTertiary}
            </Link>
          </p>

          <p
            className="text-xs mt-3 text-muted-foreground/80"
            style={{ animation: 'fadeIn 0.8s ease 0.7s both' }}
          >
            {t.freeNote}
          </p>

          <Suspense fallback={<div style={{ minHeight: 460 }} aria-hidden="true" />}>
            <HeroBelowFold
              contentLang={contentLang}
              browserPath={browserPath}
              onUrlChange={handleUrlChange}
            />
          </Suspense>
        </div>
      </section>
    </>
  );
}
