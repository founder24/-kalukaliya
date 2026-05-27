import { lazy, Suspense } from 'react';
import PageMeta from '@/components/seo/PageMeta';
import { PublicNavbar } from '@/components/layout/PublicNavbar';
import { PublicBottomNav } from '@/components/layout/PublicBottomNav';
import { useAuth } from '@/context/AuthContext';
import { useContentLang } from '@/context/LanguageContext';
import LangToggle from '@/components/ui/LangToggle';
import HeroSection from './landing/HeroSection';
import PopularSubjects from './landing/PopularSubjects';
import TrustpilotReviewsSection from '@/components/content/TrustpilotReviewsSection';
import { RecentMemoriesSection } from './chat/RecentMemoriesSection';
const FeaturesGrid = lazy(() => import('./landing/FeaturesGrid'));
const PricingSection = lazy(() => import('./landing/PricingSection'));
const PlatformSection = lazy(() => import('./landing/PlatformSection'));
const TestimonialsFooter = lazy(() => import('./landing/TestimonialsFooter'));

const _meta = {
  en: {
    title: "Syrabit.ai — Educational Browser For AssamBoard Students",
    description: "Syrabit.ai is the educational browser for AssamBoard students. Browse AHSEC Class 11-12, Degree (B.Com, B.A, B.Sc), and SEBA syllabus content, get instant answers, PYQs, notes, and MCQs — free to start. Trusted by students across Assam.",
  },
  as: {
    title: "Syrabit.ai — অসম বোৰ্ডৰ ছাত্ৰ-ছাত্ৰীৰ বাবে শৈক্ষিক ব্ৰাউজাৰ",
    description: "Syrabit.ai হৈছে অসম বোৰ্ডৰ ছাত্ৰ-ছাত্ৰীৰ বাবে শৈক্ষিক ব্ৰাউজাৰ। AHSEC একাদশ-দ্বাদশ শ্ৰেণী, ডিগ্ৰী (B.Com, B.A, B.Sc), আৰু SEBA পাঠ্যক্ৰমৰ বিষয়বস্তু ব্ৰাউজ কৰক, তাৎক্ষণিক উত্তৰ, PYQ, টোকা, আৰু MCQ পাওক — বিনামূলীয়াকৈ আৰম্ভ কৰক।",
  },
};

const faqJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'FAQPage',
  mainEntity: [
    {
      '@type': 'Question',
      name: 'How do I get started with Syrabit.ai?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Getting started is free and takes seconds. Visit syrabit.ai, browse your board and subject, and start reading notes or asking Syra, the AI tutor, questions right away — no signup required. You receive 30 free credits every day to explore syllabus-aligned notes, PYQs, MCQs, and AI-powered answers.',
      },
    },
    {
      '@type': 'Question',
      name: 'Which boards and courses does Syrabit.ai support?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Syrabit.ai supports AHSEC (Class 11 and Class 12 Science, Commerce, and Arts), SEBA, and undergraduate Degree programmes (B.Com, B.A, B.Sc) under Gauhati University and Dibrugarh University. Content is mapped chapter-wise to the official syllabus for each board and course.',
      },
    },
    {
      '@type': 'Question',
      name: 'Is Syrabit.ai free for students in Assam?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Yes, Syrabit.ai offers a generous free tier. Every visitor gets 30 AI credits per day at no cost — enough to browse notes, read PYQs, and ask Syra study questions daily. Premium plans with unlimited credits are available for students who need heavier usage during exam season.',
      },
    },
    {
      '@type': 'Question',
      name: 'How does Syrabit.ai help with exam preparation?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Syrabit.ai combines chapter-wise study notes, previous year questions with solutions, MCQ practice, and an AI tutor into a single platform. Syra answers questions grounded in your actual syllabus with source citations, so every response is exam-relevant. Students can review important questions, practise PYQs, and clarify doubts instantly — all aligned to AHSEC, SEBA, and university curricula.',
      },
    },
    {
      '@type': 'Question',
      name: 'Can I use Syrabit.ai in Assamese?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Yes. Syrabit.ai supports bilingual study in both English and Assamese. You can switch the interface language with a single tap, and Syra the AI tutor can answer questions in Assamese as well. Study notes, PYQs, and other content are available in both languages where applicable.',
      },
    },
  ],
};

export default function LandingPage() {
  const { user } = useAuth();
  const { contentLang, switchLang } = useContentLang();

  // Task #441 — signed-in students no longer auto-redirect to /chat.
  // Instead they see a personalised "Pick up where you left off"
  // dashboard inline at the top of the landing surface, then the
  // marketing sections below as a familiar fallback. Anonymous
  // visitors continue to see the marketing landing as before
  // (RecentMemoriesSection no-ops when not signed in).

  const year = new Date().getFullYear();
  const m = _meta[contentLang] || _meta.en;

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'WebPage',
    name: m.title,
    url: 'https://syrabit.ai/',
    inLanguage: contentLang === 'as' ? ['as', 'en'] : ['en', 'as'],
    description: m.description,
    isPartOf: { '@type': 'WebSite', name: 'Syrabit.ai', url: 'https://syrabit.ai' },
  };

  return (
    <div className="min-h-screen text-foreground overflow-x-hidden bg-background">
      <PageMeta
        title={m.title}
        description={m.description}
        url="https://syrabit.ai/home"
        keywords="AssamBoard educational browser, AHSEC study app, SEBA study tool, Class 11 12 exam prep, AHSEC syllabus browser, degree exam prep Assam, B.Com B.A B.Sc notes, AssamBoard 2025 study tool, free educational browser India"
        pageType="home"
        jsonLd={[jsonLd, faqJsonLd]}
      />
      <PublicNavbar />
      <LangToggle contentLang={contentLang} switchLang={switchLang} variant="floating" />
      {user && (
        <div className="px-4 pt-6">
          <RecentMemoriesSection />
        </div>
      )}
      <HeroSection contentLang={contentLang} />
      <PopularSubjects contentLang={contentLang} />
      {/* Reserve vertical space for each lazy section so Suspense fall-in does
          not cause CLS (was 0.18 — mostly from these four collapsing to 0px). */}
      <Suspense fallback={<div style={{ minHeight: '720px' }} aria-hidden />}>
        <div style={{ minHeight: '720px', contentVisibility: 'auto', containIntrinsicSize: '0 720px' }}>
          <FeaturesGrid contentLang={contentLang} />
        </div>
      </Suspense>
      <Suspense fallback={<div style={{ minHeight: '640px' }} aria-hidden />}>
        <div style={{ minHeight: '640px', contentVisibility: 'auto', containIntrinsicSize: '0 640px' }}>
          <PlatformSection contentLang={contentLang} />
        </div>
      </Suspense>
      <Suspense fallback={<div style={{ minHeight: '720px' }} aria-hidden />}>
        <div style={{ minHeight: '720px', contentVisibility: 'auto', containIntrinsicSize: '0 720px' }}>
          <PricingSection contentLang={contentLang} />
        </div>
      </Suspense>
      <TrustpilotReviewsSection
        subheading="Enjoying Syrabit.ai? Help other students across Assam discover it — leave us a quick review."
      />
      <Suspense fallback={<div style={{ minHeight: '480px' }} aria-hidden />}>
        <div style={{ minHeight: '480px', contentVisibility: 'auto', containIntrinsicSize: '0 480px' }}>
          <TestimonialsFooter year={year} contentLang={contentLang} />
        </div>
      </Suspense>
      <PublicBottomNav />
    </div>
  );
}
