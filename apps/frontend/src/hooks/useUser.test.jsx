import { describe, expect, it, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import axios from 'axios';
import { useToggleSavedSubject } from './useUser';

vi.mock('axios');
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));

describe('useToggleSavedSubject', () => {
  beforeEach(() => vi.clearAllMocks());

  it('rolls an anonymous 401 back to unsaved instead of retaining optimism', async () => {
    axios.post.mockRejectedValueOnce({ response: { status: 401 } });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useToggleSavedSubject(), { wrapper });

    act(() => { result.current.mutate('subject-1'); });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(client.getQueryData(['saved-subjects'])).toEqual([]);
  });
});