import { Navigate } from 'react-router-dom';
import { cloneElement, useEffect, useState } from 'react';
import { useAuth } from '@/context/AuthContext';
import { adminVerify } from '@/utils/api';
import { getToken } from '@/hooks/useTokenManager';

export const StaffGuard = ({ children }) => {
  const { user, authChecked } = useAuth();
  const [cookieAdmin, setCookieAdmin] = useState(null);

  const hasStaffRole = user?.role === 'staff' || user?.role === 'admin';

  // Admin sessions are intentionally HttpOnly-cookie based in some deployments,
  // so `/users/me` cannot be the only authority for the staff route. Verify the
  // admin cookie once the normal auth probe has completed.
  useEffect(() => {
    if (!authChecked || hasStaffRole) {
      setCookieAdmin(false);
      return;
    }
    let active = true;
    adminVerify(getToken())
      .then(() => { if (active) setCookieAdmin(true); })
      .catch(() => { if (active) setCookieAdmin(false); });
    return () => { active = false; };
  }, [authChecked, hasStaffRole]);

  if (!authChecked || (!hasStaffRole && cookieAdmin === null)) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-5">
          <div className="relative">
            <div className="w-14 h-14 rounded-2xl flex items-center justify-center shadow-2xl overflow-hidden">
              <img src="/logo-144.webp" alt="" width="56" height="56" className="w-14 h-14 object-cover" />
            </div>
          </div>
          <div
            className="w-5 h-5 border-2 rounded-full animate-spin"
            style={{ borderColor: 'hsl(var(--primary))', borderTopColor: 'transparent' }}
          />
        </div>
      </div>
    );
  }

  if (!hasStaffRole && !cookieAdmin) {
    return <Navigate to="/login?next=/staff" replace />;
  }
  // Keep the cookie-derived privilege available to the staff shell. The admin
  // cookie is HttpOnly, so it cannot be copied into the AuthContext token.
  return cloneElement(children, { adminCookieAccess: Boolean(cookieAdmin && !hasStaffRole) });
};
