import { type ReactNode } from 'react';
import { type ChatMessage as ChatMessageType, type ChatSource } from '../hooks/useChat';
import { FeedbackButton } from './FeedbackButton';
import clsx from 'clsx';

interface Props {
  message: ChatMessageType;
}

export function ChatMessage({ message }: Props) {
  const isUser = message.role === 'user';

  return (
    <div
      className={clsx(
        'group flex gap-3',
        isUser ? 'justify-end' : 'justify-start'
      )}
    >
      {/* Avatar */}
      {!isUser && (
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-100 text-sm font-bold text-blue-600">
          S
        </div>
      )}

      <div className={clsx('max-w-[75%] space-y-1', isUser && 'order-first')}>
        {/* Message bubble */}
        <div
          className={clsx(
            'rounded-2xl px-4 py-2.5 text-sm leading-relaxed',
            isUser
              ? 'bg-blue-600 text-white'
              : 'bg-gray-100 text-gray-800',
            message.isStreaming && 'streaming-cursor'
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            <div className="prose prose-sm max-w-none">
              {parseCitations(message.content, message.sources)}
            </div>
          )}
        </div>

        {/* Meta info + feedback (assistant only, after streaming) */}
        {!isUser && !message.isStreaming && message.content && (
          <div className="flex items-center gap-3 px-1">
            {message.latency_ms && (
              <span className="text-[10px] text-gray-400">
                {message.latency_ms}ms
              </span>
            )}
            {message.model && (
              <span className="text-[10px] text-gray-400">
                {message.model}
              </span>
            )}
            {message.isFallback && (
              <span className="rounded bg-yellow-100 px-1.5 py-0.5 text-[10px] text-yellow-700">
                fallback
              </span>
            )}
            <FeedbackButton
              messageId={message.id}
              lang={message.lang}
              model={message.model}
            />
          </div>
        )}

        {/* Source citations card */}
        {!isUser && !message.isStreaming && message.sources && message.sources.length > 0 && (
          <div className="flex flex-wrap gap-1 px-1 pt-1">
            {message.sources.map((source, i) => (
              <SourceChip key={i} index={i + 1} source={source} />
            ))}
          </div>
        )}
      </div>

      {/* User avatar */}
      {isUser && (
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-200 text-sm font-medium text-gray-600">
          U
        </div>
      )}
    </div>
  );
}

/**
 * Parse [#] citations in text and render them as superscript links.
 */
function parseCitations(text: string, sources?: ChatSource[]): ReactNode {
  if (!text) return null;

  const parts = text.split(/(\[\d+\])/g);
  return (
    <span>
      {parts.map((part, i) => {
        const match = part.match(/^\[(\d+)\]$/);
        if (match) {
          const idx = parseInt(match[1], 10) - 1;
          const source = sources?.[idx];
          if (source?.url) {
            return (
              <a
                key={i}
                href={source.url}
                target="_blank"
                rel="noopener noreferrer"
                className="ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded bg-blue-100 text-[10px] font-bold text-blue-600 no-underline hover:bg-blue-200"
                title={source.title}
              >
                {match[1]}
              </a>
            );
          }
          return (
            <span
              key={i}
              className="ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded bg-gray-200 text-[10px] font-bold text-gray-600"
              title={source?.title || `Source ${match[1]}`}
            >
              {match[1]}
            </span>
          );
        }
        // Render regular text with basic whitespace preservation
        return <span key={i} className="whitespace-pre-wrap">{part}</span>;
      })}
    </span>
  );
}

function SourceChip({ index, source }: { index: number; source: ChatSource }) {
  const content = (
    <span className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-0.5 text-[11px] text-gray-600 transition-colors hover:bg-gray-50">
      <span className="font-semibold text-blue-600">[{index}]</span>
      <span className="max-w-[120px] truncate">{source.title}</span>
    </span>
  );

  if (source.url) {
    return (
      <a href={source.url} target="_blank" rel="noopener noreferrer" className="no-underline">
        {content}
      </a>
    );
  }
  return content;
}
