import { useRef, useEffect } from 'react';
import { useChat } from '../hooks/useChat';
import { ChatMessage } from './ChatMessage';
import { ChatInput } from './ChatInput';
import { LangSelector } from './LangSelector';

export function ChatContainer() {
  const {
    messages,
    sendMessage,
    stopStreaming,
    clearMessages,
    isStreaming,
    lang,
    setLang,
    error,
  } = useChat();

  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  return (
    <div className="flex h-screen flex-col bg-white">
      {/* ── Header ── */}
      <header className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold text-gray-900">Syrabit</h1>
          <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-600">
            v3.0
          </span>
        </div>

        <div className="flex items-center gap-3">
          <LangSelector lang={lang} setLang={setLang} disabled={isStreaming} />
          {messages.length > 0 && (
            <button
              onClick={clearMessages}
              disabled={isStreaming}
              className="rounded-md px-2 py-1 text-xs text-gray-500 transition-colors
                         hover:bg-gray-100 hover:text-gray-700 disabled:opacity-50"
              title="New chat"
            >
              New Chat
            </button>
          )}
        </div>
      </header>

      {/* ── Messages area ── */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-4 py-6"
      >
        <div className="mx-auto max-w-3xl space-y-6">
          {messages.length === 0 && <EmptyState lang={lang} />}

          {messages.map((msg) => (
            <ChatMessage key={msg.id} message={msg} />
          ))}

          {/* Error display */}
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {error}
            </div>
          )}
        </div>
      </div>

      {/* ── Input area ── */}
      <ChatInput
        onSend={sendMessage}
        onStop={stopStreaming}
        isStreaming={isStreaming}
        placeholder={
          lang === 'en'
            ? 'Ask a question about your studies...'
            : 'আপোনাৰ পঢ়াৰ বিষয়ে প্ৰশ্ন সোধক...'
        }
      />
    </div>
  );
}

function EmptyState({ lang }: { lang: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-blue-50">
        <span className="text-3xl">📚</span>
      </div>
      <h2 className="mb-2 text-xl font-semibold text-gray-800">
        {lang === 'en' ? 'Welcome to Syrabit' : 'Syrabit-লৈ স্বাগতম'}
      </h2>
      <p className="max-w-md text-sm text-gray-500">
        {lang === 'en'
          ? 'Your AI-powered educational assistant for Assamese students. Ask questions in English or Assamese.'
          : 'অসমীয়া ছাত্ৰ-ছাত্ৰীৰ বাবে AI-চালিত শিক্ষা সহায়ক। ইংৰাজী বা অসমীয়াত প্ৰশ্ন সোধক।'}
      </p>
      <div className="mt-6 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {(lang === 'en'
          ? [
              'Explain photosynthesis simply',
              'History of Ahom Kingdom',
              'Solve: 2x + 5 = 15',
              'What is the Brahmaputra River?',
            ]
          : [
              'সালোকসংশ্লেষণ বুজাই দিয়ক',
              'আহোম ৰাজ্যৰ ইতিহাস',
              'সমাধান: 2x + 5 = 15',
              'ব্ৰহ্মপুত্ৰ নদী কি?',
            ]
        ).map((suggestion) => (
          <button
            key={suggestion}
            className="rounded-lg border border-gray-200 px-3 py-2 text-left text-xs text-gray-600
                       transition-colors hover:border-blue-200 hover:bg-blue-50"
          >
            {suggestion}
          </button>
        ))}
      </div>
    </div>
  );
}
