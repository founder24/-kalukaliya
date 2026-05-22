import { useState, useCallback, useRef } from 'react';

export type Lang = 'en' | 'as';

export interface ChatSource {
  doc_id: string;
  title: string;
  score: number;
  url?: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  model?: string;
  lang?: Lang;
  latency_ms?: number;
  sources?: ChatSource[];
  isStreaming?: boolean;
  isFallback?: boolean;
}

interface UseChatOptions {
  apiUrl?: string;
  initialLang?: Lang;
  sessionId?: string;
}

interface StreamEvent {
  text?: string;
  done?: boolean;
  error?: string;
  fallback?: boolean;
  provider?: string;
  reason?: string;
  latency_ms?: number;
  model?: string;
  lang?: string;
}

export function useChat(options: UseChatOptions = {}) {
  const {
    apiUrl = '/api/v1/chat/stream',
    initialLang = 'en',
    sessionId,
  } = options;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [lang, setLang] = useState<Lang>(initialLang);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (content: string) => {
      if (!content.trim() || isStreaming) return;
      setError(null);

      // Track real user interaction for analytics (SPA page view equivalent)
      try {
        if (typeof window !== 'undefined' && (window as any).posthog) {
          (window as any).posthog.capture('chat_message_sent', { lang, session_id: sessionId });
        }
      } catch {
        // Analytics is non-critical
      }

      // Add user message
      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content: content.trim(),
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, userMsg]);

      // Create placeholder assistant message
      const assistantId = crypto.randomUUID();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: 'assistant',
        content: '',
        timestamp: Date.now(),
        isStreaming: true,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      setIsStreaming(true);

      // Abort controller for cancellation
      abortRef.current = new AbortController();

      try {
        const token = localStorage.getItem('access_token');
        const headers: Record<string, string> = {
          'Content-Type': 'application/json',
        };
        if (token) {
          headers['Authorization'] = `Bearer ${token}`;
        }

        const response = await fetch(apiUrl, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            message: content.trim(),
            lang,
            session_id: sessionId,
          }),
          signal: abortRef.current.signal,
        });

        if (!response.ok) {
          const errText = await response.text();
          throw new Error(`HTTP ${response.status}: ${errText}`);
        }

        const reader = response.body!.getReader();
        const decoder = new TextDecoder();
        let fullText = '';
        let isFallback = false;
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          // Process complete lines from buffer
          const lines = buffer.split('\n');
          // Keep incomplete last line in buffer
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;

            let data: StreamEvent;
            try {
              data = JSON.parse(line.slice(6));
            } catch {
              continue; // Skip malformed JSON
            }

            // Handle error event
            if (data.error) {
              setError(data.error);
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, content: fullText || 'An error occurred.', isStreaming: false }
                    : m
                )
              );
              setIsStreaming(false);
              return;
            }

            // Handle fallback notification
            if (data.fallback) {
              isFallback = true;
              continue;
            }

            // Handle text chunk
            if (data.text) {
              fullText += data.text;
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, content: fullText } : m
                )
              );
            }

            // Handle stream completion
            if (data.done) {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content: fullText,
                        isStreaming: false,
                        model: data.model,
                        lang: (data.lang as Lang) || lang,
                        latency_ms: data.latency_ms,
                        isFallback,
                      }
                    : m
                )
              );
            }
          }
        }
      } catch (e: unknown) {
        if (e instanceof Error && e.name === 'AbortError') {
          // User cancelled — finalize the message with what we have
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, isStreaming: false }
                : m
            )
          );
        } else {
          const errMsg = e instanceof Error ? e.message : 'Unknown error';
          setError(errMsg);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, content: `Error: ${errMsg}`, isStreaming: false }
                : m
            )
          );
        }
      } finally {
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [apiUrl, lang, sessionId, isStreaming]
  );

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setError(null);
  }, []);

  return {
    messages,
    sendMessage,
    stopStreaming,
    clearMessages,
    isStreaming,
    lang,
    setLang,
    error,
  };
}
