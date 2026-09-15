import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({ get, post })),
  },
}));

vi.mock('@/utils/api', () => ({ API_BASE: '/api/v1' }));
vi.mock('@/hooks/useTokenManager', () => ({ getToken: () => null }));
vi.mock('@/utils/staffAccess', () => ({
  canStaffCapability: () => true,
}));
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import StaffOperations from './StaffOperations';

describe('StaffOperations index repair queue', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    get.mockImplementation((path) => {
      if (path.startsWith('/staff/content/chapters/')) return Promise.resolve({ data: [] });
      if (path === '/staff/content/reindex-jobs') return Promise.resolve({ data: { jobs: [] } });
      if (path.startsWith('/admin/content/ahsec-d1-import/approvals')) {
        return Promise.resolve({ data: { approvals: [] } });
      }
      if (path.startsWith('/admin/content/ahsec-d1-import/index-repair-queue')) {
        return Promise.resolve({
          data: {
            chapters: [{
              chapter_id: 'chapter-queue-1',
              operation: 'vector_upsert',
              attempts_used: 1,
              next_attempt: 2,
              attempt_limit: 3,
              repair_command: 'python3 -m scripts.ahsec_d1_import --repair-index chapter-queue-1',
              run_id: 'archived-run',
              failed_at: '2026-09-15T10:00:00+00:00',
              archived: true,
            }],
            total: 1,
            limit: 25,
            has_more: false,
            exhausted: 0,
            attempt_limit: 3,
          },
        });
      }
      return Promise.reject(new Error(`Unexpected GET ${path}`));
    });
    post.mockReset();
  });

  it('renders safe queue metadata and the manual repair command', async () => {
    render(
      <StaffOperations
        user={{ role: 'admin' }}
        subjects={[{ id: 'subject-1', name: 'Chemistry' }]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('index-repair-item-chapter-queue-1')).toBeTruthy();
    });
    expect(screen.getByText('vector_upsert')).toBeTruthy();
    expect(screen.getByText(/Attempts used: 1\/3/)).toBeTruthy();
    expect(screen.getByText('archived run')).toBeTruthy();
    expect(screen.getByText(/--repair-index chapter-queue-1/)).toBeTruthy();
    expect(get).toHaveBeenCalledWith('/admin/content/ahsec-d1-import/index-repair-queue?limit=25');
  });

  it('requires confirmation before opening an available chapter', async () => {
    const onOpenChapter = vi.fn();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(
      <StaffOperations
        user={{ role: 'admin' }}
        subjects={[{ id: 'subject-1', name: 'Chemistry' }]}
        onOpenChapter={onOpenChapter}
      />,
    );

    const button = await screen.findByTestId('button-open-repair-chapter-chapter-queue-1');
    fireEvent.click(button);
    expect(onOpenChapter).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    fireEvent.click(button);
    expect(onOpenChapter).toHaveBeenCalledWith('chapter-queue-1');
  });
});