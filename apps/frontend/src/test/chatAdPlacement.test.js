import { describe, expect, it } from 'vitest';
import { getChatSponsorIndex } from '@/utils/chatAdPlacement';

describe('chat sponsored breaks', () => {
  it('places a break after every second completed answer', () => {
    const messages = [
      { role: 'user', content: 'one' },
      { role: 'assistant', content: 'answer one', streaming: false },
      { role: 'user', content: 'two' },
      { role: 'assistant', content: 'answer two', streaming: false },
    ];
    expect(getChatSponsorIndex(messages, 1)).toBeNull();
    expect(getChatSponsorIndex(messages, 3)).toBe(1);
  });

  it('does not place a break during streaming or on an incomplete answer', () => {
    const messages = [
      { role: 'user', content: 'question' },
      { role: 'assistant', content: 'partial', streaming: true },
      { role: 'assistant', content: '', streaming: false },
    ];
    expect(getChatSponsorIndex(messages, 1)).toBeNull();
    expect(getChatSponsorIndex(messages, 2)).toBeNull();
  });

  it('is idempotent for retries because placement follows message order', () => {
    const messages = [
      { id: 'a', role: 'assistant', content: 'first', streaming: false },
      { id: 'b', role: 'assistant', content: 'second', streaming: false },
    ];
    expect(getChatSponsorIndex(messages, 1)).toBe(1);
    expect(getChatSponsorIndex(messages, 1)).toBe(1);
  });
});