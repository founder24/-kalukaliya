import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookOpen } from 'lucide-react';
import { useContentLang } from '@/context/LanguageContext';
import { RecentMemoriesSection } from './RecentMemoriesSection';

// Static EmptyState copy for the chat page. Assamese strings are picked
// up only after `LanguageProvider` hydrates from localStorage (post-
// mount), which matches the SSR-safe pattern in LanguageContext.
const EMPTY_STATE_T = {
  en: {
    askAboutSubject: (name) => `Ask me about ${name}`,
    headingLine1: "Hi! I'm Syra — Educational Browser",
    headingLine2: 'For Assam Board Students',
    subjectSubtitle: 'Syllabus-first answers powered by web search.',
    documentSubtitle: 'Document loaded as primary source. Ask any question.',
    browseSyllabus: 'Browse Syllabus →',
  },
  as: {
    askAboutSubject: (name) => `${name} বিষয়ে সুধক`,
    headingLine1: 'নমস্কাৰ! মই চিৰা — শৈক্ষিক ব্ৰাউজাৰ',
    headingLine2: 'আছাম ব’ৰ্ডৰ ছাত্ৰ-ছাত্ৰীৰ বাবে',
    subjectSubtitle: 'ৱেব সন্ধানৰ সহায়ত পাঠ্যক্ৰম-প্ৰথম উত্তৰ।',
    documentSubtitle: 'ডকুমেণ্ট প্ৰাথমিক উৎস হিচাপে লোড হৈছে। যিকোনো প্ৰশ্ন সুধক।',
    browseSyllabus: 'পাঠ্যক্ৰম চাওক →',
  },
};

export function EmptyState({ subject, documentId, defaultPrompts, setInput, textareaRef }) {
  const navigate = useNavigate();
  const { contentLang } = useContentLang();
  const t = EMPTY_STATE_T[contentLang] || EMPTY_STATE_T.en;
  // Defer URL-search-param-dependent text until after hydration. The SSR
  // snapshot is rendered for /chat with no query string, so reading
  // `documentId` here on the first client render would drift if the user
  // landed on /chat?document_id=… and break hydration. (Task #387 —
  // architect review.)
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);
  const showDocumentText = mounted && documentId;

  return (
    <div className="flex flex-col items-center justify-center text-center space-y-5 py-8">
      <div>
        <div
          className="w-16 h-16 rounded-2xl flex items-center justify-center"
          style={{
            background: 'linear-gradient(135deg,rgba(124,58,237,0.20),rgba(139,92,246,0.15))',
            border: '1px solid rgba(139,92,246,0.25)',
          }}
        >
          <BookOpen size={36} className="text-violet-600" />
        </div>
      </div>

      <div>
        <h2
          className="text-foreground mb-1.5 shimmer-text"
          style={{ fontSize: '1.2rem', fontWeight: 700 }}
        >
          {subject ? t.askAboutSubject(subject.name) : <>{t.headingLine1}<br />{t.headingLine2}</>}
        </h2>
        <p className="text-muted-foreground text-sm max-w-sm mx-auto">
          {showDocumentText
            ? t.documentSubtitle
            : subject
            ? t.subjectSubtitle
            : ''
          }
        </p>
      </div>

      {!subject && (
        <button
          onClick={() => navigate('/library')}
          className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold transition-all duration-200 hover:opacity-90 active:scale-95"
          style={{
            background: 'linear-gradient(135deg,rgba(124,58,237,0.15),rgba(139,92,246,0.15))',
            border: '1px solid rgba(139,92,246,0.25)',
            color: 'hsl(var(--primary))',
          }}
        >
          <BookOpen size={15} />
          {t.browseSyllabus}
        </button>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full max-w-lg">
        {defaultPrompts.map((prompt) => (
          <button
            key={prompt}
            onClick={() => { setInput(prompt); textareaRef.current?.focus(); }}
            className="p-3 rounded-xl text-left text-sm text-muted-foreground hover:text-foreground transition-all duration-200"
            style={{ border: '1px solid rgba(139,92,246,0.12)', background: 'rgba(124,58,237,0.03)' }}
          >
            {prompt}
          </button>
        ))}
      </div>

      {/* Task #415 — "Pick up where you left off" widget. Only shown
          on the bare dashboard surface (no subject and no loaded
          document) so it doesn't compete with the subject-scoped
          empty state. The component itself no-ops for anonymous
          users and on memory_brain failures. */}
      {!subject && !showDocumentText && <RecentMemoriesSection />}
    </div>
  );
}
