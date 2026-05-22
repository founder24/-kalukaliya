import { useState } from 'react';

interface Props {
  messageId: string;
  lang?: string;
  model?: string;
}

export function FeedbackButton({ messageId, lang, model }: Props) {
  const [submitted, setSubmitted] = useState<'up' | 'down' | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const submit = async (rating: 1 | -1) => {
    if (isSubmitting || submitted) return;
    setIsSubmitting(true);

    try {
      const token = localStorage.getItem('access_token');
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      await fetch('/api/v1/chat/feedback', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          message_id: messageId,
          rating,
          lang: lang || 'en',
          model_provider: model || 'unknown',
        }),
      });

      setSubmitted(rating === 1 ? 'up' : 'down');
    } catch {
      // Silently fail — feedback is non-critical
    } finally {
      setIsSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <span className="text-xs text-gray-400">
        {submitted === 'up' ? 'Thanks!' : 'Thanks for the feedback'}
      </span>
    );
  }

  return (
    <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
      <button
        onClick={() => submit(1)}
        disabled={isSubmitting}
        className="rounded p-1 text-gray-400 transition-colors hover:bg-green-50 hover:text-green-600"
        title="Good response"
      >
        <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3H14z" />
        </svg>
      </button>
      <button
        onClick={() => submit(-1)}
        disabled={isSubmitting}
        className="rounded p-1 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600"
        title="Bad response"
      >
        <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M10 15v4a3 3 0 003 3l4-9V2H5.72a2 2 0 00-2 1.7l-1.38 9a2 2 0 002 2.3H10z" />
        </svg>
      </button>
    </div>
  );
}
