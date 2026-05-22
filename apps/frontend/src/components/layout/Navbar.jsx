import { useLocation } from 'react-router-dom';

const PAGE_TITLES = {
  '/library': 'Browser',
  '/chat':    'AI Chat',
  '/history': 'History',
  '/profile': 'Profile',
};

export const Navbar = ({ pageTitle }) => {
  const location = useLocation();
  const title = pageTitle || PAGE_TITLES[location.pathname] || 'Syrabit.ai';

  return (
    <header
      className="sticky top-0 z-40 h-14 flex items-center border-b border-border/60 gap-4 px-4 sm:px-6"
      style={{
        background: 'var(--popover-glass)',
        backdropFilter: 'blur(24px) saturate(1.8)',
        WebkitBackdropFilter: 'blur(24px) saturate(1.8)',
      }}
      data-testid="app-navbar"
    >
      <div className="flex-1">
        <h1 className="text-sm font-semibold text-foreground">{title}</h1>
      </div>
    </header>
  );
};
