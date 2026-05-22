import { useEffect } from 'react';

/**
 * PageTitle — lightweight document.title setter (no OG/canonical).
 * Used on authenticated internal pages only.
 */
export const PageTitle = ({ title }) => {
  useEffect(() => {
    const prev = document.title;
    document.title = title || 'Syrabit.ai';
    return () => {
      document.title = prev;
    };
  }, [title]);
  return null;
};
