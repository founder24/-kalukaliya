import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const axiosGet = vi.hoisted(() => vi.fn());
vi.mock('axios', () => ({ default: { get: axiosGet } }));
vi.mock('@/utils/api', () => ({ WORKER_API: 'https://api.example/api/v1' }));
vi.mock('@/hooks/useTokenManager', () => ({ getToken: () => 'staff-token' }));

import { COMMAND_SECTIONS, commandCenterAuthConfig } from './CommandCenter';
import CommandCenter from './CommandCenter';

describe('staff command-center navigation', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/staff?section=overview&days=7');
    axiosGet.mockResolvedValue({
      data: {
        generated_at: '2026-09-07T00:00:00.000Z',
        audit: { actions: 3, publish_actions: 2 },
      },
    });
  });

  it('keeps every operational deep-link section available', () => {
    expect(COMMAND_SECTIONS.map(([id]) => id)).toEqual([
      'overview', 'users', 'content', 'rag', 'chat', 'ads', 'reliability', 'audit',
    ]);
  });

  it('maps every section to a canonical Worker response group', () => {
    expect(COMMAND_SECTIONS.map(([, , group]) => group)).toEqual([
      'users', 'users', 'content', 'rag', 'chat', 'ads', 'incidents', 'audit',
    ]);
  });

  it('supports bearer-authenticated staff while retaining admin-cookie credentials', () => {
    expect(commandCenterAuthConfig('staff-token')).toEqual({
      withCredentials: true,
      headers: { Authorization: 'Bearer staff-token' },
    });
    expect(commandCenterAuthConfig(null)).toEqual({
      withCredentials: true,
    });
  });

  it('renders audit aggregates in the Audit section', async () => {
    render(<CommandCenter />);
    fireEvent.click(screen.getByRole('button', { name: 'Audit' }));
    expect(await screen.findByText('Actions')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('Publish Actions')).toBeInTheDocument();
    expect(axiosGet).toHaveBeenCalledWith(
      'https://api.example/api/v1/staff/analytics/command-center',
      expect.objectContaining({ params: { days: 7 } }),
    );
  });
});