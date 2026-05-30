import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ShieldCheck, Mail, Lock, Eye, EyeOff, Loader2, AlertCircle,
} from 'lucide-react';
import { adminLogin } from '@/utils/api';
import { toast } from 'sonner';

export default function AdminLoginPage() {
  const navigate = useNavigate();
  const [email, setEmail]       = useState('');
  const [password, setPassword] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState('');

  const handleLogin = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const res = await adminLogin(email, password);
      // Session is the httponly `syrabit_admin_session` cookie set by
      // the backend — do NOT mirror the JWT into localStorage. Storing
      // it there exposes it to any XSS on the admin domain (the cookie
      // is httponly precisely to defend against that) and creates a
      // second source of truth that drifts apart from the cookie's
      // expiry, leading to "logged-in UI / 401 API" states.
      toast.success(`Welcome back, ${res.data.name || 'Admin'}!`, {
        description: 'Admin session started',
      });
      navigate('/admin');
    } catch (err) {
      const detail = err.response?.data?.detail;
      const msg = (detail && typeof detail === 'object' && detail.message)
        || (typeof detail === 'string' ? detail : null)
        || 'Invalid credentials';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-[#f8f9fc]">
      <div className="w-full max-w-sm space-y-4">
        <div className="text-center mb-8">
          <div className="inline-block mb-4">
            <img src="/logo-144.webp" alt="Syrabit.ai" width="56" height="56"
              className="w-14 h-14 rounded-2xl mx-auto object-cover shadow-lg" />
          </div>
          <h1 className="text-lg font-semibold text-gray-900 tracking-tight">Administrator Access</h1>
          <p className="text-gray-500 text-sm mt-1">Syrabit.ai Control Center</p>
        </div>

        <div className="rounded-2xl p-6 bg-white shadow-sm border border-gray-200">
          {error && (
            <div className="flex items-center gap-2 text-red-600 rounded-xl p-3 mb-4 text-sm bg-red-50 border border-red-200">
              <AlertCircle size={15} />
              {error}
            </div>
          )}

          <form onSubmit={handleLogin} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-gray-600 text-xs font-medium">Email</label>
              <div className="relative">
                <Mail size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="admin@syrabit.ai"
                  className="w-full pl-9 pr-3 h-10 rounded-xl text-sm text-gray-900 outline-none transition-all border border-gray-200 bg-gray-50 focus:bg-white focus:ring-2 focus:ring-violet-500/20 focus:border-violet-400"
                  required
                  data-testid="admin-email-input"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-gray-600 text-xs font-medium">Password</label>
              <div className="relative">
                <Lock size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type={showPass ? 'text' : 'password'}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••••••"
                  className="w-full pl-9 pr-10 h-10 rounded-xl text-sm text-gray-900 outline-none transition-all border border-gray-200 bg-gray-50 focus:bg-white focus:ring-2 focus:ring-violet-500/20 focus:border-violet-400"
                  required
                  data-testid="admin-password-input"
                />
                <button
                  type="button"
                  onClick={() => setShowPass(!showPass)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors"
                >
                  {showPass ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 h-11 rounded-xl text-sm font-semibold text-white bg-violet-600 hover:bg-violet-700 transition-all active:scale-[0.98] disabled:opacity-50 shadow-sm"
              data-testid="admin-login-submit-button"
            >
              {loading
                ? <><Loader2 size={15} className="animate-spin" /> Signing in...</>
                : <><ShieldCheck size={15} /> Sign In Securely</>
              }
            </button>
          </form>

          <p className="text-center text-gray-600 text-xs mt-5 flex items-center justify-center gap-1.5">
            <ShieldCheck size={10} />
            Protected by HMAC authentication · 8-hour session
          </p>
        </div>
      </div>
    </div>
  );
}
