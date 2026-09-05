/**
 * Sponsored breaks are derived from the message list, not stream events.
 * This keeps retries and React re-renders idempotent.
 */
export function getChatSponsorIndex(messages, messageIndex) {
  const message = messages[messageIndex];
  if (!message || message.role !== 'assistant' || message.streaming || !message.content) return null;
  const completed = messages
    .slice(0, messageIndex + 1)
    .filter((item) => item.role === 'assistant' && !item.streaming && item.content).length;
  return completed > 0 && completed % 2 === 0 ? completed / 2 : null;
}