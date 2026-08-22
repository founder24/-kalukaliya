import { createContext, useContext, useState, useCallback, useEffect } from 'react';

const LanguageContext = createContext(null);

const STORAGE_KEY = 'syrabit:content_lang';

export function LanguageProvider({ children }) {
  // ALWAYS start with 'en' on first render so SSR and client first render
  // match. Reading localStorage in the lazy initializer makes the SSR
  // output ('en') differ from the client first render (whatever the user
  // saved) → React error #418 hydration mismatch on every prerendered
  // page that has any `contentLang === 'as'` ternary in the render tree
  // (ChapterPage / SubjectPage / library, etc.).
  const [contentLang, setContentLang] = useState('en');
  // The library is the public catalog entry point and should open in English
  // by default. Keep this check here as well as in LibraryPage so a direct
  // prerendered load cannot briefly restore a previously saved Assamese value.
  const isLibraryDefaultRoute = typeof window !== 'undefined'
    && /^\/(?:library|browser)(?:\/|$)/.test(window.location.pathname || '');

  // After mount, hydrate from localStorage. This re-renders into the
  // user's saved language without breaking hydration.
  useEffect(() => {
    if (isLibraryDefaultRoute) return;
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === 'as' || stored === 'en') setContentLang(stored);
    } catch {}
  }, [isLibraryDefaultRoute]);

  const switchLang = useCallback((lang) => {
    const val = lang === 'as' ? 'as' : 'en';
    setContentLang(val);
    try { localStorage.setItem(STORAGE_KEY, val); } catch {}
  }, []);

  const toggleLang = useCallback(() => {
    setContentLang((prev) => {
      const next = prev === 'en' ? 'as' : 'en';
      try { localStorage.setItem(STORAGE_KEY, next); } catch {}
      return next;
    });
  }, []);

  return (
    <LanguageContext.Provider value={{ contentLang, switchLang, toggleLang }}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useContentLang() {
  const ctx = useContext(LanguageContext);
  if (!ctx) return { contentLang: 'en', switchLang: () => {}, toggleLang: () => {} };
  return ctx;
}
