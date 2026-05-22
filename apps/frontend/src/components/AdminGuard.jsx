import { useState, useEffect } from 'react';
import { Navigate, Outlet } from 'react-router-dom';
import { adminVerify } from '@/utils/api';

export const AdminGuard = ({ children }) => {
  const [status, setStatus] = useState('checking'); // 'checking' | 'ok' | 'denied'

  useEffect(() => {
    // Cookie-only auth — `adminVerify` sends `withCredentials: true`
    // so the httponly `syrabit_admin_session` cookie is what proves
    // the session. Reading a mirrored token from localStorage was
    // removed because (a) it was XSS-readable and (b) it could drift
    // out of sync with the cookie, leading to stale "logged in" gates.
    adminVerify()
      .then(() => setStatus('ok'))
      .catch(() => setStatus('denied'));
  }, []);

  if (status === 'checking') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="w-6 h-6 border-2 rounded-full animate-spin" style={{ borderColor: 'hsl(var(--primary))', borderTopColor: 'transparent' }} />
      </div>
    );
  }

  if (status === 'denied') {
    return <Navigate to="/admin/login" replace />;
  }

  return children || <Outlet />;
};
