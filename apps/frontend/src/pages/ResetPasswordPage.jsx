import { useState, useEffect } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Mail, Loader2, ArrowLeft, CheckCircle, Lock, Eye, EyeOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from 'sonner';

import axios from 'axios';
import { LogoFull } from '@/components/Logo';
import { API_BASE } from '@/utils/api';
import { formatAuthError } from '@/lib/authErrors';

export default function ResetPasswordPage() {
  const [email, setEmail] = useState('');
  const [token, setToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [step, setStep] = useState('request');
  const [loading, setLoading] = useState(false);
  const [searchParams] = useSearchParams();

  useEffect(() => {
    const urlToken = searchParams.get('token');
    if (urlToken) {
      setToken(urlToken);
      setStep('confirm');
    }
  }, [searchParams]);

  const handleRequest = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await axios.post(
        `${API_BASE}/auth/forgot-password`,
        { email },
      );
      setStep('confirm');
      toast.success('Reset token sent! Check your email or ask admin.');
    } catch (err) {
      toast.error(formatAuthError(err, 'Request failed. Please try again.'));
    } finally {
      setLoading(false);
    }
  };

  const handleConfirm = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await axios.post(
        `${API_BASE}/auth/reset-password`,
        { token, new_password: newPassword },
      );
      setStep('done');
      toast.success('Password updated!');
    } catch (err) {
      toast.error(formatAuthError(err, 'Reset failed. Please try again.'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-start sm:items-center justify-center bg-background p-4 pt-12 sm:pt-4">
      <div className="w-full max-w-sm">
        <Link to="/login" className="flex items-center gap-2 mb-6">
          <LogoFull size="sm" textClassName="text-foreground" />
        </Link>

        <div className="glass-card rounded-2xl p-6">
          {step === 'request' && (
            <>
              <div className="mb-6">
                <h1 className="text-xl font-semibold text-foreground">Reset Password</h1>
                <p className="text-muted-foreground text-sm mt-1">Enter your email — we'll send you a reset token</p>
              </div>
              <form onSubmit={handleRequest} className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-foreground/70 text-sm">Email</Label>
                  <div className="relative">
                    <Mail size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground/40" />
                    <Input
                      type="email"
                      name="email"
                      autoComplete="email"
                      placeholder="your@email.com"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      className="pl-9"
                      required
                    />
                  </div>
                </div>
                <Button
                  type="submit"
                  disabled={loading}
                  className="w-full bg-violet-600 hover:bg-violet-500 text-white"
                >
                  {loading ? <Loader2 size={16} className="animate-spin mr-2" /> : null}
                  Send Reset Link
                </Button>
              </form>
            </>
          )}

          {step === 'confirm' && (
            <>
              <div className="mb-6">
                <h1 className="text-xl font-semibold text-foreground">Enter Reset Token</h1>
                <p className="text-muted-foreground text-sm mt-1">Enter the token sent to your email and choose a new password</p>
                <p className="text-muted-foreground/50 text-xs mt-2">Didn't receive an email? Contact admin@syrabit.ai with your email address.</p>
              </div>
              <form onSubmit={handleConfirm} className="space-y-4">
                <div className="space-y-1.5">
                  <Label className="text-foreground/70 text-sm">Reset Token</Label>
                  <Input
                    placeholder="Paste your reset token"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    required
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-foreground/70 text-sm">New Password</Label>
                  <div className="relative">
                    <Lock size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground/40" />
                    <Input
                      type={showPass ? 'text' : 'password'}
                      name="new-password"
                      autoComplete="new-password"
                      placeholder="••••••••"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      className="pl-9 pr-10"
                      required
                      minLength={6}
                    />
                    <button type="button" onClick={() => setShowPass(!showPass)} className="absolute right-1 top-1/2 -translate-y-1/2 min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground/40 hover:text-foreground transition-colors">
                      {showPass ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </div>
                <Button
                  type="submit"
                  disabled={loading}
                  className="w-full bg-violet-600 hover:bg-violet-500 text-white"
                >
                  {loading ? <Loader2 size={16} className="animate-spin mr-2" /> : null}
                  Update Password
                </Button>
              </form>
            </>
          )}

          {step === 'done' && (
            <div className="text-center py-4">
              <CheckCircle size={48} className="text-emerald-500 mx-auto mb-4" />
              <h2 className="text-lg font-semibold text-foreground mb-2">Password Updated!</h2>
              <p className="text-muted-foreground text-sm mb-6">You can now sign in with your new password.</p>
              <Link to="/login">
                <Button className="bg-violet-600 hover:bg-violet-500 text-white">Sign In</Button>
              </Link>
            </div>
          )}

          <p className="text-center mt-4">
            <Link to="/login" className="text-violet-600 hover:text-violet-700 text-sm flex items-center justify-center gap-1 transition-colors">
              <ArrowLeft size={14} /> Back to Login
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
