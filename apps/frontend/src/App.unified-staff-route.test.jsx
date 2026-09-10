import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { LegacyAdminRedirect } from './App';

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</div>;
}

describe('unified staff portal legacy routes', () => {
  it('preserves admin deep-link state when redirecting to staff', async () => {
    render(
      <MemoryRouter initialEntries={['/admin?s=analytics&t=usage#report']}>
        <Routes>
          <Route path="/admin/*" element={<LegacyAdminRedirect />} />
          <Route path="/staff" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByTestId('location')).toHaveTextContent('/staff?s=analytics&t=usage#report');
  });
});