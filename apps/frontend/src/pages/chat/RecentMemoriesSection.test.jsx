import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor, fireEvent } from '@testing-library/react';

// ── Mocks (must be declared before importing the SUT) ──────────────

const mockNavigate = vi.fn();
const mockGetRecentMemories = vi.fn();

let mockUser = null;
let mockAuthChecked = true;
let mockContentLang = 'en';

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ user: mockUser, authChecked: mockAuthChecked }),
}));

vi.mock('@/context/LanguageContext', () => ({
  useContentLang: () => ({ contentLang: mockContentLang }),
}));

vi.mock('@/utils/api', () => ({
  getRecentMemories: (...args) => mockGetRecentMemories(...args),
}));

import { RecentMemoriesSection } from './RecentMemoriesSection';

beforeEach(() => {
  mockNavigate.mockReset();
  mockGetRecentMemories.mockReset();
  mockUser = null;
  mockAuthChecked = true;
  mockContentLang = 'en';
});

describe('RecentMemoriesSection — anonymous + empty-state hide rules', () => {
  it('renders nothing for anonymous users (no API call, no DOM)', async () => {
    mockUser = null;
    const { container } = render(<RecentMemoriesSection />);
    // The effect synchronously sets `loaded = true` for the anonymous
    // branch, so by the time the next microtask flushes the component
    // has decided not to render anything.
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
    expect(mockGetRecentMemories).not.toHaveBeenCalled();
  });

  it('renders nothing while auth has not yet been checked', () => {
    mockUser = null;
    mockAuthChecked = false;
    const { container } = render(<RecentMemoriesSection />);
    expect(container.firstChild).toBeNull();
    expect(mockGetRecentMemories).not.toHaveBeenCalled();
  });

  it('renders nothing for signed-in users when the API returns no items', async () => {
    mockUser = { id: 'u1' };
    mockGetRecentMemories.mockResolvedValueOnce({
      data: { items: [], anon: false },
    });

    const { container } = render(<RecentMemoriesSection />);
    await waitFor(() => {
      expect(mockGetRecentMemories).toHaveBeenCalledWith(5);
    });
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
  });

  it('renders nothing when the API call fails (best-effort widget)', async () => {
    mockUser = { id: 'u1' };
    mockGetRecentMemories.mockRejectedValueOnce(new Error('boom'));

    const { container } = render(<RecentMemoriesSection />);
    await waitFor(() => {
      expect(mockGetRecentMemories).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
  });
});

describe('RecentMemoriesSection — populated state', () => {
  const items = [
    {
      id: 'mem-1',
      kind: 'qa',
      event: 'chat_turn',
      title: 'What is photosynthesis?',
      preview: 'Plants convert CO2 + H2O into glucose.',
      subject_name: 'Biology',
      chapter_name: 'Photosynthesis',
      conversation_id: 'conv-abc',
    },
    {
      id: 'mem-2',
      kind: 'fact',
      event: 'flashcard_recall',
      title: 'Mitochondria are the powerhouse of the cell.',
      preview: '',
      subject_name: 'Biology',
      chapter_name: 'Cells',
      conversation_id: null,
    },
  ];

  it('renders the heading and one card per item, with subject/chapter chip', async () => {
    mockUser = { id: 'u1' };
    mockGetRecentMemories.mockResolvedValueOnce({
      data: { items, anon: false },
    });

    const { findByText, findAllByRole } = render(<RecentMemoriesSection />);

    await findByText('Pick up where you left off');

    // Both card titles are present.
    await findByText('What is photosynthesis?');
    await findByText('Mitochondria are the powerhouse of the cell.');

    // Card chips (subject_name preferred over chapter_name).
    const chips = await findAllByRole('button');
    expect(chips).toHaveLength(2);
    // Subject chip text rendered with the leading separator.
    expect(chips[0].textContent).toContain('· Biology');

    // Chat-turn card surfaces the "Continue" affordance; flashcard
    // recall surfaces "Mastered".
    expect(chips[0].textContent).toMatch(/Continue/i);
    expect(chips[1].textContent).toMatch(/Mastered/i);
  });

  it('navigates to /chat?id=… when a card with conversation_id is clicked', async () => {
    mockUser = { id: 'u1' };
    mockGetRecentMemories.mockResolvedValueOnce({
      data: { items, anon: false },
    });

    const { findAllByRole } = render(<RecentMemoriesSection />);
    const buttons = await findAllByRole('button');
    fireEvent.click(buttons[0]);

    expect(mockNavigate).toHaveBeenCalledWith(
      '/chat?id=conv-abc',
    );
  });

  it('seeds the chat input when clicking a card with no conversation_id', async () => {
    mockUser = { id: 'u1' };
    mockGetRecentMemories.mockResolvedValueOnce({
      data: { items, anon: false },
    });

    const { findAllByRole } = render(<RecentMemoriesSection />);
    const buttons = await findAllByRole('button');
    fireEvent.click(buttons[1]);

    expect(mockNavigate).toHaveBeenCalledWith('/chat', {
      state: { seedCardContext: 'Mitochondria are the powerhouse of the cell.' },
    });
  });

  it('renders nothing when the API reports anon:true even if items are present', async () => {
    // Defensive: the backend short-circuits anonymous callers to an
    // empty list, but if anon were ever returned with items the
    // component must still hide itself rather than leak someone
    // else's memories.
    mockUser = { id: 'u1' };
    mockGetRecentMemories.mockResolvedValueOnce({
      data: { items, anon: true },
    });

    const { container } = render(<RecentMemoriesSection />);
    await waitFor(() => {
      expect(mockGetRecentMemories).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
  });
});
