import { memo } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Home, BookOpen, MessageCircle, CreditCard, LogIn, Sparkles } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { prefetchRoute } from '@/utils/prefetchRoute';

const NAV_ITEMS = [
  { to: '/home', icon: Home, label: 'Home' },
  { to: '/library', icon: BookOpen, label: 'Library' },
  { to: '/chat', icon: MessageCircle, label: 'Chat' },
  { to: '/pricing', icon: CreditCard, label: 'Pricing' },
];

export const PublicBottomNav = memo(function PublicBottomNav() {
  const location = useLocation();
  const { user } = useAuth();

  const isActive = (path) =>
    location.pathname === path || location.pathname.startsWith(path + '/');

  // Signed-out users see Home/Library/Chat/Pricing + a Sign-Up CTA.
  // Signed-in users keep the same four nav tiles (so Library/Chat/Pricing
  // remain present on every public page) and get an "Open App" CTA that
  // also routes to /chat — same destination, distinct visual role.
  const ctaItem = user
    ? { to: '/chat', icon: Sparkles, label: 'Open App', isCta: true, key: 'cta-open-app' }
    : { to: '/signup', icon: LogIn, label: 'Sign Up', isCta: true, key: 'cta-signup' };

  const items = [
    ...NAV_ITEMS.map((it) => ({ ...it, key: `nav-${it.to}` })),
    ctaItem,
  ];

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
      data-testid="public-bottom-nav"
    >
      <div className="flex items-center justify-around h-16 px-1 gap-0.5">
        {items.map(({ to, icon: Icon, label, isCta, key }) => {
          const active = isActive(to);
          if (isCta) {
            return (
              <Link
                key={key}
                to={to}
                onTouchStart={() => prefetchRoute(to)}
                className="flex items-center gap-1 px-3 py-2 rounded-full text-[11px] font-semibold text-white transition-all duration-200 active:scale-95"
                style={{
                  background: 'linear-gradient(135deg, #7c3aed, #8b5cf6)',
                  boxShadow: '0 2px 12px rgba(124,58,237,0.3)',
                }}
                aria-label={label}
              >
                <Icon size={16} aria-hidden="true" />
                <span>{label}</span>
              </Link>
            );
          }
          return (
            <Link
              key={key}
              to={to}
              onTouchStart={() => prefetchRoute(to)}
              onMouseEnter={() => prefetchRoute(to)}
              className="flex flex-col items-center justify-center gap-0.5 px-2 py-2 rounded-xl text-[10px] font-medium transition-all duration-200 min-w-[48px] min-h-[44px]"
              style={active ? {
                color: 'hsl(var(--primary))',
              } : {
                color: 'hsl(var(--muted-foreground))',
              }}
              aria-label={label}
              aria-current={active ? 'page' : undefined}
            >
              <Icon
                size={20}
                strokeWidth={active ? 2.2 : 1.8}
                aria-hidden="true"
              />
              <span>{label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
});
