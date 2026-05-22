import { useCallback, memo } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { BookOpen, MessageSquare, Clock, User } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { pageImports } from '@/utils/pageImports';
import { prefetchRoute } from '@/utils/prefetchRoute';

const NAV_ITEMS = [
  { to: '/library', icon: BookOpen,      label: 'Browser',  preloadKey: 'library' },
  { to: '/chat',    icon: MessageSquare, label: 'Chat',     preloadKey: 'chat' },
  { to: '/history', icon: Clock,         label: 'History',  preloadKey: 'history' },
  { to: '/profile', icon: User,          label: 'Profile',  preloadKey: 'profile' },
];

export const BottomNav = memo(function BottomNav() {
  const location = useLocation();
  const { user } = useAuth();

  const isActive = (path) =>
    location.pathname === path || location.pathname.startsWith(path + '/');

  const items = NAV_ITEMS;

  const handlePreload = useCallback((preloadKey, to) => {
    if (preloadKey && pageImports[preloadKey]) {
      pageImports[preloadKey]();
    }
    if (to) prefetchRoute(to);
  }, []);

  return (
    <nav
      className="md:hidden fixed bottom-0 left-0 right-0 z-50"
      role="navigation"
      aria-label="Mobile navigation"
      style={{
        background: 'rgba(255, 255, 255, 0.92)',
        backdropFilter: 'blur(28px) saturate(1.8)',
        WebkitBackdropFilter: 'blur(28px) saturate(1.8)',
        borderTop: '1px solid hsl(var(--border) / 0.25)',
        boxShadow: '0 -2px 20px rgba(0,0,0,0.06)',
        paddingBottom: 'env(safe-area-inset-bottom, 0px)',
      }}
      data-testid="app-bottom-nav"
    >
      <div className="flex items-center justify-around h-16 px-1">
        {items.map(({ to, icon: Icon, label, preloadKey }) => {
          const active = isActive(to);
          return (
            <Link
              key={to}
              to={to}
              onTouchStart={() => handlePreload(preloadKey, to)}
              onMouseEnter={() => handlePreload(preloadKey, to)}
              onFocus={() => handlePreload(preloadKey, to)}
              className="flex flex-col items-center justify-center gap-0.5 rounded-2xl text-[11px] font-medium transition-all duration-200 min-w-[60px] min-h-[48px] relative active:scale-95"
              style={active ? {
                color: 'hsl(var(--primary))',
                background: 'hsl(var(--primary) / 0.08)',
              } : {
                color: 'hsl(var(--muted-foreground))',
              }}
              aria-label={label}
              aria-current={active ? 'page' : undefined}
              data-testid={`bottom-nav-${label.toLowerCase()}`}
            >
              <div className="relative">
                <Icon
                  size={22}
                  strokeWidth={active ? 2.2 : 1.8}
                  aria-hidden="true"
                />
                {active && (
                  <span
                    className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-4 h-0.5 rounded-full"
                    style={{ background: 'hsl(var(--primary))' }}
                    aria-hidden="true"
                  />
                )}
              </div>
              <span className={active ? 'font-semibold' : ''}>{label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
});
