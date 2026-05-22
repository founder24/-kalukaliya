import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { Menu, X, ArrowRight } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { LogoFull } from '@/components/Logo';
import { prefetchRoute } from '@/utils/prefetchRoute';

export const PublicNavbar = () => {
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const { user } = useAuth();

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 30);
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  const handleEscape = useCallback((e) => {
    if (e.key === 'Escape' && menuOpen) setMenuOpen(false);
  }, [menuOpen]);

  useEffect(() => {
    if (menuOpen) {
      document.addEventListener('keydown', handleEscape);
      document.body.style.overflow = 'hidden';
      return () => {
        document.removeEventListener('keydown', handleEscape);
        document.body.style.overflow = '';
      };
    }
  }, [menuOpen, handleEscape]);

  const navLinks = [
    { label: 'Library',      href: '/library',      internal: true  },
    { label: 'Curriculum',   href: '/curriculum',   internal: true  },
    { label: 'Chat',         href: '/chat',         internal: true  },
    { label: 'Exam Routine', href: '/exam-routine', internal: true  },
    { label: 'Pricing',      href: '/pricing',      internal: true  },
    { label: 'About',        href: '/about',        internal: true  },
  ];

  const handlePrefetch = useCallback((path) => {
    prefetchRoute(path);
  }, []);

  return (
    <nav
      className="fixed top-0 left-0 right-0 z-50 transition-all duration-400"
      style={
        scrolled
          ? {
              background: 'rgba(240,240,245,0.88)',
              backdropFilter: 'blur(28px) saturate(1.6)',
              WebkitBackdropFilter: 'blur(28px) saturate(1.6)',
              borderBottom: '1px solid rgba(139,92,246,0.14)',
              boxShadow: '0 4px 32px rgba(0,0,0,0.06), 0 0 0 1px rgba(139,92,246,0.05) inset',
            }
          : { background: 'transparent' }
      }
      data-testid="public-navbar"
    >
      <div className="max-w-6xl mx-auto px-5">
        <div className="flex items-center justify-between h-16">

          <Link to="/" onClick={() => setMenuOpen(false)}>
            <LogoFull size="sm" textClassName="text-foreground" hideText={false} hideIcon={true} />
          </Link>

          <div className="hidden lg:flex items-center gap-0.5">
            {navLinks.map((link) =>
              link.internal ? (
                <Link
                  key={link.label}
                  to={link.href}
                  onMouseEnter={() => handlePrefetch(link.href)}
                  onTouchStart={() => handlePrefetch(link.href)}
                  onFocus={() => handlePrefetch(link.href)}
                  className="px-4 py-2 rounded-xl text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-primary/[0.06] transition-all duration-150"
                >
                  {link.label}
                </Link>
              ) : (
                <a
                  key={link.label}
                  href={link.href}
                  className="px-4 py-2 rounded-xl text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-primary/[0.06] transition-all duration-150"
                >
                  {link.label}
                </a>
              )
            )}
          </div>

          <div className="hidden lg:flex items-center gap-2">
            {user ? (
              <Link
                to="/library"
                onMouseEnter={() => handlePrefetch('/library')}
                onTouchStart={() => handlePrefetch('/library')}
                onFocus={() => handlePrefetch('/library')}
                className="flex items-center gap-1.5 h-9 px-4 rounded-xl text-sm text-white font-semibold transition-all duration-150 hover:opacity-90 active:scale-95 hover:-translate-y-px btn-gradient"
              >
                Go to App <ArrowRight size={14} />
              </Link>
            ) : (
              <>
                <Link
                  to="/login"
                  onMouseEnter={() => handlePrefetch('/login')}
                  onTouchStart={() => handlePrefetch('/login')}
                  className="h-9 px-4 rounded-xl text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-primary/[0.06] transition-all duration-150"
                >
                  Sign In
                </Link>
                <Link
                  to="/signup"
                  onMouseEnter={() => handlePrefetch('/signup')}
                  onTouchStart={() => handlePrefetch('/signup')}
                  className="flex items-center gap-1.5 h-9 px-4 rounded-xl text-sm text-white font-semibold transition-all duration-150 active:scale-95 btn-gradient"
                  data-testid="landing-nav-cta-button"
                >
                  Get Started Free <ArrowRight size={14} />
                </Link>
              </>
            )}
          </div>

          <button
            className="lg:hidden min-w-[44px] min-h-[44px] rounded-xl text-muted-foreground hover:text-foreground hover:bg-primary/[0.06] flex items-center justify-center transition-all"
            onClick={() => setMenuOpen(!menuOpen)}
            aria-label="Toggle menu"
            data-testid="mobile-menu-button"
          >
            {menuOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>

      {menuOpen && (
        <>
          <div
            className="fixed inset-0 z-40 lg:hidden"
            style={{ background: 'rgba(0,0,0,0.20)' }}
            onClick={() => setMenuOpen(false)}
            aria-hidden="true"
          />
          <div
            className="fixed top-16 left-0 right-0 z-50 lg:hidden px-5 py-4 space-y-1 mobile-menu-slide"
            style={{
              background: 'rgba(240,240,245,0.97)',
              backdropFilter: 'blur(28px)',
              WebkitBackdropFilter: 'blur(28px)',
              borderTop: '1px solid rgba(139,92,246,0.12)',
              maxHeight: 'calc(100vh - 4rem)',
              overflowY: 'auto',
            }}
            role="menu"
            aria-modal="true"
            aria-label="Navigation menu"
          >
            {navLinks.map((link) =>
              link.internal ? (
                <Link
                  key={link.label}
                  to={link.href}
                  role="menuitem"
                  className="block px-3 py-3 rounded-xl text-sm text-muted-foreground hover:text-foreground hover:bg-primary/[0.06] transition-all min-h-[44px] flex items-center"
                  onClick={() => setMenuOpen(false)}
                >
                  {link.label}
                </Link>
              ) : (
                <a
                  key={link.label}
                  href={link.href}
                  role="menuitem"
                  className="block px-3 py-3 rounded-xl text-sm text-muted-foreground hover:text-foreground hover:bg-primary/[0.06] transition-all min-h-[44px] flex items-center"
                  onClick={() => setMenuOpen(false)}
                >
                  {link.label}
                </a>
              )
            )}
            <div className="pt-3 space-y-2 mt-2" style={{ borderTop: '1px solid rgba(139,92,246,0.10)' }}>
              {user ? (
                <Link
                  to="/library"
                  role="menuitem"
                  onMouseEnter={() => handlePrefetch('/library')}
                  onTouchStart={() => handlePrefetch('/library')}
                  onFocus={() => handlePrefetch('/library')}
                  className="flex items-center justify-center gap-2 w-full min-h-[44px] rounded-xl text-sm text-white font-semibold btn-gradient"
                  onClick={() => setMenuOpen(false)}
                >
                  Go to App <ArrowRight size={14} />
                </Link>
              ) : (
                <>
                  <Link
                    to="/login"
                    role="menuitem"
                    className="flex items-center justify-center w-full min-h-[44px] rounded-xl text-sm text-muted-foreground border border-border/30 hover:bg-primary/[0.06] transition-all"
                    onClick={() => setMenuOpen(false)}
                  >
                    Sign In
                  </Link>
                  <Link
                    to="/signup"
                    role="menuitem"
                    className="flex items-center justify-center gap-2 w-full min-h-[44px] rounded-xl text-sm text-white font-semibold btn-gradient"
                    onClick={() => setMenuOpen(false)}
                  >
                    Get Started Free <ArrowRight size={14} />
                  </Link>
                </>
              )}
            </div>
          </div>
        </>
      )}
    </nav>
  );
};
