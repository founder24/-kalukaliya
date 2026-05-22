import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { Sparkles, ChevronRight, Twitter, Github, Mail, Globe } from 'lucide-react';
import { LogoMark, LogoFull } from '@/components/Logo';
import Reveal from './Reveal';
import GlowOrb from './GlowOrb';

const TRUSTPILOT_BU_ID = __TRUSTPILOT_BU_ID__;

const _t = {
  en: {
    testimonialsHeading: 'Students love Syrabit.ai',
    testimonialsSub: 'Real feedback from students across Assam',
    trustpilotLink: 'See our reviews on Trustpilot',
    trustpilotMini: 'Rated on Trustpilot',
    ctaHeading: 'Ready to ace your boards?',
    ctaSub: 'Join hundreds of AssamBoard students (AHSEC, DEGREE & SEBA) who study smarter with Syrabit.ai. Free forever — no credit card required.',
    ctaPrimary: 'Create Free Account',
    ctaSecondary: 'View all plans',
    footerDesc: 'AI-powered exam prep for AssamBoard students in Assam — AHSEC (Class 11–12), DEGREE (B.Com, B.A, B.Sc), and SEBA.',
    product: 'PRODUCT',
    company: 'COMPANY',
    contact: 'CONTACT',
    boards: 'BOARDS',
    features: 'Features',
    pricing: 'Pricing',
    subjects: 'Library',
    curriculum: 'Curriculum',
    examRoutine: 'Exam Routine',
    chat: 'Ask Syra (Chat)',
    ahsec12: 'AHSEC Class 12',
    ahsec11: 'AHSEC Class 11',
    seba: 'SEBA Class 10',
    degree: 'Degree (B.Com / B.A / B.Sc)',
    aboutUs: 'About Us',
    technology: 'Technology',
    privacyPolicy: 'Privacy Policy',
    terms: 'Terms of Service',
    status: 'System Status',
    adminPortal: 'Admin Portal →',
    copyright: (y) => `© ${y} Syrabit.ai · Built for AssamBoard students in Assam, India (AHSEC · DEGREE · SEBA)`,
    madeWith: 'Made with ♥ for Class 11 & 12 exam warriors',
  },
  as: {
    testimonialsHeading: 'ছাত্ৰ-ছাত্ৰীয়ে Syrabit.ai ভাল পায়',
    testimonialsSub: 'অসমৰ ছাত্ৰ-ছাত্ৰীৰ প্ৰকৃত মতামত',
    trustpilotLink: 'Trustpilot-ত আমাৰ পৰ্যালোচনা চাওক',
    trustpilotMini: 'Trustpilot-ত ৰেটিং',
    ctaHeading: 'আপোনাৰ বোৰ্ড পৰীক্ষাত উত্তীৰ্ণ হ\'বলৈ সাজু?',
    ctaSub: 'শত শত অসম বোৰ্ডৰ ছাত্ৰ-ছাত্ৰী (AHSEC, DEGREE আৰু SEBA)ৰ সৈতে যোগদান কৰক যিয়ে Syrabit.ai-ৰ সৈতে স্মাৰ্টকৈ অধ্যয়ন কৰে। চিৰদিনৰ বাবে বিনামূলীয়া — ক্ৰেডিট কাৰ্ডৰ প্ৰয়োজন নাই।',
    ctaPrimary: 'বিনামূলীয়া একাউণ্ট তৈয়াৰ কৰক',
    ctaSecondary: 'সকলো পৰিকল্পনা চাওক',
    footerDesc: 'অসমৰ অসম বোৰ্ডৰ ছাত্ৰ-ছাত্ৰীৰ বাবে AI-চালিত পৰীক্ষা প্ৰস্তুতি — AHSEC (একাদশ-দ্বাদশ শ্ৰেণী), DEGREE (B.Com, B.A, B.Sc), আৰু SEBA।',
    product: 'সামগ্ৰী',
    company: 'কোম্পানী',
    contact: 'যোগাযোগ',
    boards: 'বোৰ্ডসমূহ',
    features: 'সুবিধাসমূহ',
    pricing: 'মূল্য নিৰ্ধাৰণ',
    subjects: 'লাইব্ৰেৰী',
    curriculum: 'পাঠ্যক্ৰম',
    examRoutine: 'পৰীক্ষাৰ সময়সূচী',
    chat: 'Syra ক সোধক (চেট)',
    ahsec12: 'AHSEC দ্বাদশ শ্ৰেণী',
    ahsec11: 'AHSEC একাদশ শ্ৰেণী',
    seba: 'SEBA দশম শ্ৰেণী',
    degree: 'ডিগ্ৰী (B.Com / B.A / B.Sc)',
    aboutUs: 'আমাৰ বিষয়ে',
    technology: 'প্ৰযুক্তি',
    privacyPolicy: 'গোপনীয়তা নীতি',
    terms: 'সেৱাৰ চৰ্তাৱলী',
    status: 'চিষ্টেম স্থিতি',
    adminPortal: 'এডমিন পৰ্টেল →',
    copyright: (y) => `© ${y} Syrabit.ai · অসম, ভাৰতৰ অসম বোৰ্ডৰ ছাত্ৰ-ছাত্ৰীৰ বাবে নিৰ্মিত (AHSEC · DEGREE · SEBA)`,
    madeWith: 'একাদশ আৰু দ্বাদশ শ্ৰেণীৰ পৰীক্ষা যোদ্ধাসকলৰ বাবে ♥ৰে নিৰ্মিত',
  },
};

function TrustpilotCarousel({ label }) {
  const ref = useRef(null);

  useEffect(() => {
    if (typeof window !== 'undefined' && window.Trustpilot && ref.current) {
      window.Trustpilot.loadFromElement(ref.current, true);
    }
  }, []);

  return (
    <div
      ref={ref}
      className="trustpilot-widget"
      data-locale="en-US"
      data-template-id="53aa8912dec7e10d38f59f36"
      data-businessunit-id={TRUSTPILOT_BU_ID}
      data-style-height="140px"
      data-style-width="100%"
      data-theme="light"
      data-stars="4,5"
      data-review-languages="en"
    >
      <a
        href="https://www.trustpilot.com/review/syrabit.ai"
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-2 text-sm font-medium text-muted-foreground"
      >
        {label}
      </a>
    </div>
  );
}

function TrustpilotMini({ label }) {
  const ref = useRef(null);

  useEffect(() => {
    if (typeof window !== 'undefined' && window.Trustpilot && ref.current) {
      window.Trustpilot.loadFromElement(ref.current, true);
    }
  }, []);

  return (
    <div className="mt-8 flex justify-center">
      <div
        ref={ref}
        className="trustpilot-widget"
        data-locale="en-US"
        data-template-id="56278e9abfbd13b10015e694"
        data-businessunit-id={TRUSTPILOT_BU_ID}
        data-style-height="52px"
        data-style-width="100%"
      >
        <a
          href="https://www.trustpilot.com/review/syrabit.ai"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 text-sm font-medium text-muted-foreground"
        >
          {label}
        </a>
      </div>
    </div>
  );
}

export default function TestimonialsFooter({ year, contentLang = 'en' }) {
  const t = _t[contentLang] || _t.en;

  useEffect(() => {
    if (document.getElementById('trustpilot-widget-script')) return;
    const script = document.createElement('script');
    script.id = 'trustpilot-widget-script';
    script.src = 'https://widget.trustpilot.com/bootstrap/v5/tp.widget.bootstrap.min.js';
    script.async = true;
    script.onload = () => {
      const els = document.querySelectorAll('.trustpilot-widget');
      els.forEach((el) => {
        if (window.Trustpilot) window.Trustpilot.loadFromElement(el, true);
      });
    };
    document.head.appendChild(script);
  }, []);

  return (
    <>
      <section className="py-28 max-w-5xl mx-auto px-5">
        <Reveal className="text-center mb-14">
          <h2 className="text-foreground mb-3" style={{ fontSize: 'clamp(1.8rem,4vw,2.8rem)', fontWeight: 800, letterSpacing: '-0.02em' }}>
            {t.testimonialsHeading}
          </h2>
          <p className="text-muted-foreground">
            {t.testimonialsSub}
          </p>
        </Reveal>

        <Reveal>
          <TrustpilotCarousel label={t.trustpilotLink} />
        </Reveal>

        <TrustpilotMini label={t.trustpilotMini} />
      </section>

      <section className="py-28 relative overflow-hidden">
        <div className="absolute inset-0 pointer-events-none">
          <GlowOrb color="radial-gradient(circle,#7c3aed,transparent)" size={700} x="20%" y="0%" blur={140} opacity={0.06} animRange={20} duration={20} />
          <GlowOrb color="radial-gradient(circle,#4f46e5,transparent)" size={500} x="60%" y="40%" blur={120} opacity={0.04} animRange={15} duration={16} />
        </div>

        <Reveal className="relative z-10 max-w-2xl mx-auto px-5 text-center">
          <div
            className="flex justify-center mb-8"
            style={{ animation: 'float 4s ease-in-out infinite' }}
          >
            <LogoMark size="lg" />
          </div>

          <h2 className="text-foreground mb-4" style={{ fontSize: 'clamp(2rem,4vw,3rem)', fontWeight: 900, letterSpacing: '-0.02em' }}>
            {t.ctaHeading}
          </h2>
          <p className="mb-10 text-lg text-muted-foreground">
            {t.ctaSub}
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <Link
              to="/signup"
              className="flex items-center gap-2 text-white font-bold transition-transform duration-200 ease-out hover:scale-[1.04] hover:-translate-y-0.5 active:scale-[0.97]"
              style={{
                height: 56,
                padding: '0 2.5rem',
                borderRadius: '1rem',
                fontSize: '1.125rem',
                background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)',
                boxShadow: '0 8px 40px rgba(139,92,246,0.25)',
              }}
              data-testid="landing-final-cta-button"
            >
              <Sparkles size={20} />
              {t.ctaPrimary}
            </Link>
            <Link
              to="/pricing"
              className="flex items-center gap-2 font-semibold text-muted-foreground transition-transform duration-200 ease-out hover:scale-[1.04] hover:-translate-y-0.5 active:scale-[0.97]"
              style={{
                height: 56,
                padding: '0 2rem',
                borderRadius: '1rem',
                fontSize: '1rem',
                border: '1px solid hsl(var(--border))',
                background: 'hsl(var(--muted) / 0.3)',
              }}
            >
              {t.ctaSecondary} <ChevronRight size={18} />
            </Link>
          </div>
        </Reveal>
      </section>

      <footer
        className="border-t py-12"
        style={{ borderColor: 'hsl(var(--border) / 0.3)', background: 'hsl(var(--muted) / 0.3)' }}
      >
        <div className="max-w-6xl mx-auto px-5">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4 sm:gap-6 md:gap-8 mb-12">
            <div className="col-span-2 md:col-span-1 space-y-4">
              <LogoFull size="sm" textClassName="text-foreground" />
              <p className="text-sm leading-relaxed text-muted-foreground">
                {t.footerDesc}
              </p>
              <div className="flex items-center gap-2">
                {[{ icon: Twitter, label: 'Twitter' }, { icon: Github, label: 'GitHub' }, { icon: Mail, label: 'Email' }].map(({ icon: Icon, label }) => (
                  <button
                    key={label}
                    aria-label={label}
                    className="w-11 h-11 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground transition-all duration-200 ease-out hover:scale-110 active:scale-95"
                    style={{ background: 'hsl(var(--muted) / 0.5)', border: '1px solid hsl(var(--border) / 0.3)' }}
                  >
                    <Icon size={16} />
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1">
              <p className="text-xs font-bold tracking-[0.10em] pb-1 text-muted-foreground">{t.product}</p>
              <Link to="/home#features" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.features}</Link>
              <Link to="/pricing" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.pricing}</Link>
              <Link to="/library" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.subjects}</Link>
              <Link to="/curriculum" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.curriculum}</Link>
              <Link to="/exam-routine" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.examRoutine}</Link>
              <Link to="/chat" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.chat}</Link>
            </div>

            <div className="space-y-1">
              <p className="text-xs font-bold tracking-[0.10em] pb-1 text-muted-foreground">{t.boards}</p>
              <Link to="/ahsec/class-12/physics" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.ahsec12}</Link>
              <Link to="/ahsec/class-11/physics" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.ahsec11}</Link>
              <Link to="/seba/class-10/science" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.seba}</Link>
              <Link to="/library?stream=commerce" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.degree}</Link>
            </div>

            <div className="space-y-1">
              <p className="text-xs font-bold tracking-[0.10em] pb-1 text-muted-foreground">{t.company}</p>
              <Link to="/about" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.aboutUs}</Link>
              <Link to="/technology" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.technology}</Link>
              <Link to="/privacy" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.privacyPolicy}</Link>
              <Link to="/terms" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.terms}</Link>
              <Link to="/status" className="block text-sm text-muted-foreground hover:text-foreground transition-colors min-h-[44px] flex items-center">{t.status}</Link>
            </div>

            <div className="space-y-1">
              <p className="text-xs font-bold tracking-[0.10em] pb-1 text-muted-foreground">{t.contact}</p>
              <div className="flex items-center gap-1.5 text-sm min-h-[44px] text-muted-foreground">
                <Mail size={14} /><span>admin@syrabit.ai</span>
              </div>
              <div className="flex items-center gap-1.5 text-sm min-h-[44px] text-muted-foreground">
                <Globe size={14} /><span>syrabit.ai</span>
              </div>
              <div className="mt-4">
                <Link
                  to="/admin/login"
                  className="text-xs text-muted-foreground/20 hover:text-muted-foreground/40 transition-colors"
                >
                  {t.adminPortal}
                </Link>
              </div>
            </div>
          </div>

          <div
            className="border-t pt-6 flex flex-col md:flex-row items-center justify-between gap-3"
            style={{ borderColor: 'hsl(var(--border) / 0.3)' }}
          >
            <p className="text-xs text-muted-foreground">
              {t.copyright(year)}
            </p>
            <p className="text-xs text-muted-foreground">
              {t.madeWith}
            </p>
          </div>
        </div>
      </footer>
    </>
  );
}
