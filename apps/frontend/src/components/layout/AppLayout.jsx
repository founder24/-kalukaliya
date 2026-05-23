import { useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { BottomNav } from './BottomNav';
import { Navbar } from './Navbar';

export function AppLayout(props) {
  const { pageTitle, hideNavbar, children } = props;
  const location = useLocation();

  useEffect(() => {
    const mainEl = document.getElementById('main-content');
    if (mainEl) mainEl.scrollTop = 0;
  }, [location.pathname]);

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {!hideNavbar && (
          <header>
            <Navbar pageTitle={pageTitle} />
          </header>
        )}
        <main
          id="main-content"
          role="main"
          className="flex-1 overflow-y-auto app-main-scroll"
          tabIndex={-1}
        >
          {children ? children : <Outlet />}
        </main>
      </div>
      <BottomNav />
    </div>
  );
}
