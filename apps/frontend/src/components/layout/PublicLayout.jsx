import { Outlet } from 'react-router-dom';
import { Link } from 'react-router-dom';
import { PublicNavbar } from './PublicNavbar';
import { PublicBottomNav } from './PublicBottomNav';

export const PublicLayout = ({ children }) => {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <PublicNavbar />
      <main className="pt-16 pb-20 md:pb-0">
        {children || <Outlet />}
      </main>
      <PublicBottomNav />
      <footer
        className="hidden md:block py-10 px-4"
        style={{ borderTop: '1px solid hsl(var(--border) / 0.3)' }}
      >
        <div className="max-w-7xl mx-auto">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <div
                className="w-7 h-7 rounded-xl flex items-center justify-center"
                style={{ background: 'linear-gradient(135deg, #7c3aed, #8b5cf6)' }}
              >
                <span className="text-white text-xs font-bold">S</span>
              </div>
              <span className="text-foreground font-bold shimmer-text">Syrabit<span className="text-violet-600">.ai</span></span>
            </div>
            <div className="flex items-center gap-4 text-sm text-muted-foreground">
              <Link to="/technology" className="hover:text-foreground transition-colors py-2 px-1 min-h-[44px] flex items-center">Technology</Link>
              <Link to="/terms"   className="hover:text-foreground transition-colors py-2 px-1 min-h-[44px] flex items-center">Terms</Link>
              <Link to="/privacy" className="hover:text-foreground transition-colors py-2 px-1 min-h-[44px] flex items-center">Privacy</Link>
              <Link to="/pricing" className="hover:text-foreground transition-colors py-2 px-1 min-h-[44px] flex items-center">Pricing</Link>
            </div>
            <p className="text-muted-foreground/50 text-xs">
              &copy; {new Date().getFullYear()} Syrabit.ai. All rights reserved.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
};
