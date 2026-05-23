import { Link, useLocation, useNavigate } from 'react-router-dom';
import { BookOpen, MessageSquare, Clock, User, ChevronLeft, ChevronRight, LogOut } from 'lucide-react';
import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '@/context/AuthContext';
import { cn } from '@/lib/utils';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { LogoFull, LogoMark } from '@/components/Logo';
import { pageImports } from '@/utils/pageImports';

const NAV_ITEMS = [
  { to: '/library', icon: BookOpen,      label: 'Browser',  preloadKey: 'library' },
  { to: '/chat',    icon: MessageSquare, label: 'Chat',     preloadKey: 'chat' },
  { to: '/history', icon: Clock,         label: 'History',  preloadKey: 'history' },
  { to: '/profile', icon: User,          label: 'Profile',  preloadKey: 'profile' },
];

export const Sidebar = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  // Always start uncollapsed so SSR and the client first render match.
  // After mount, hydrate from localStorage. Avoids React error #418.
  const [collapsed, setCollapsed] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    try {
      const v = localStorage.getItem('syrabit:sidebar-collapsed') === 'true';
      if (v) setCollapsed(true);
    } catch {}
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    try { localStorage.setItem('syrabit:sidebar-collapsed', String(collapsed)); } catch {}
  }, [collapsed, hydrated]);

  const handleLogout = () => { logout(); navigate('/login'); };
  const isActive = (path) =>
    location.pathname === path || location.pathname.startsWith(path + '/');

  const handlePreload = useCallback((preloadKey) => {
    if (preloadKey && pageImports[preloadKey]) {
      pageImports[preloadKey]();
    }
  }, []);

  return (
    <TooltipProvider delayDuration={0}>
      <aside
        className={cn(
          'hidden md:flex flex-col h-screen sticky top-0 border-r transition-all duration-300 z-40',
          collapsed ? 'w-[64px]' : 'w-[240px]'
        )}
        style={{
          background: 'var(--sidebar-glass)',
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          borderColor: 'hsl(var(--sidebar-border) / 0.25)',
        }}
        role="navigation"
        aria-label="Main navigation"
        data-testid="app-sidebar"
      >
        <div
          className="flex items-center h-16 px-3 overflow-hidden"
          style={{ borderBottom: '1px solid hsl(var(--sidebar-border) / 0.2)' }}
        >
          <Link to="/library" className="flex items-center min-w-0">
            {collapsed ? (
              <LogoMark size="sm" />
            ) : (
              <LogoFull size="sm" />
            )}
          </Link>
        </div>

        <nav className="flex-1 px-2 py-4 space-y-0.5 overflow-y-auto">
          {NAV_ITEMS.map(({ to, icon: Icon, label, preloadKey }) => {
            const active = isActive(to);
            return (
              <Tooltip key={to}>
                <TooltipTrigger asChild>
                  <Link
                    to={to}
                    className={cn(
                      'flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-200 relative group',
                      active
                        ? 'text-primary nav-item-active'
                        : 'text-muted-foreground hover:text-foreground hover:translate-x-0.5'
                    )}
                    style={active ? {
                      background: 'hsl(var(--primary) / 0.12)',
                      boxShadow: '0 2px 12px hsl(var(--primary) / 0.1)',
                    } : {}}
                    onFocus={() => handlePreload(preloadKey)}
                    onMouseEnter={e => {
                      handlePreload(preloadKey);
                      if (!active) e.currentTarget.style.background = 'hsl(var(--primary) / 0.06)';
                    }}
                    onMouseLeave={e => {
                      if (!active) e.currentTarget.style.background = '';
                    }}
                    data-testid={`sidebar-nav-${label.toLowerCase()}`}
                  >
                    <Icon
                      size={18}
                      className={cn('flex-shrink-0 transition-colors', active ? 'text-primary' : '')}
                    />
                    {!collapsed && <span>{label}</span>}
                    {active && !collapsed && (
                      <div
                        className="ml-auto w-1.5 h-1.5 rounded-full bg-primary"
                        style={{
                          boxShadow: '0 0 8px hsl(var(--primary) / 0.6)',
                        }}
                      />
                    )}
                  </Link>
                </TooltipTrigger>
                {collapsed && (
                  <TooltipContent side="right">{label}</TooltipContent>
                )}
              </Tooltip>
            );
          })}

        </nav>

        <div
          className="px-2 py-3 space-y-0.5"
          style={{ borderTop: '1px solid hsl(var(--sidebar-border) / 0.2)' }}
        >
          {user ? <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={handleLogout}
                className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium text-muted-foreground hover:text-red-500 transition-all duration-200"
                onMouseEnter={e => { e.currentTarget.style.background = 'rgba(239,68,68,0.06)'; }}
                onMouseLeave={e => { e.currentTarget.style.background = ''; }}
                aria-label="Log out of Syrabit.ai"
                data-testid="sidebar-logout-button"
              >
                <LogOut size={18} className="flex-shrink-0" />
                {!collapsed && <span>Logout</span>}
              </button>
            </TooltipTrigger>
            {collapsed && <TooltipContent side="right">Logout</TooltipContent>}
          </Tooltip> : <button
            onClick={() => navigate('/login')}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium text-muted-foreground hover:text-primary transition-all duration-200"
            onMouseEnter={e => { e.currentTarget.style.background = 'hsl(var(--primary) / 0.06)'; }}
            onMouseLeave={e => { e.currentTarget.style.background = ''; }}
            aria-label="Sign in to Syrabit.ai"
          >
            <LogOut size={18} className="flex-shrink-0 rotate-180" />
            {!collapsed && <span>Sign In</span>}
          </button>}

          <button
            onClick={() => setCollapsed(!collapsed)}
            className="w-full flex items-center justify-center py-2 text-muted-foreground hover:text-foreground transition-all duration-200 rounded-xl"
            onMouseEnter={e => { e.currentTarget.style.background = 'hsl(var(--primary) / 0.06)'; }}
            onMouseLeave={e => { e.currentTarget.style.background = ''; }}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            data-testid="sidebar-collapse-button"
          >
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        </div>
      </aside>
    </TooltipProvider>
  );
};
