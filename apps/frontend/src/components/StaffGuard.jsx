import { Navigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';

export const StaffGuard = ({ children }) => {
  const { user, authChecked } = useAuth();

  if (!authChecked) {
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

  if (!user) return <Navigate to="/login" replace />;
  const role = user.role || '';
  if (role !== 'staff' && role !== 'admin' && !user.is_admin) {
    return <Navigate to="/login" replace />;
  }

  return children;
};
