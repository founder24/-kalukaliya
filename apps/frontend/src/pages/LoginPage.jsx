import { useState, useCallback, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Mail, Lock, Eye, EyeOff, Loader2, MessageSquare, BarChart3, AlertCircle, Sparkles } from 'lucide-react';
import { usePublicStats } from '@/hooks/usePublicStats';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAuth } from '@/context/AuthContext';
import { formatAuthError } from '@/lib/authErrors';
import { toast } from 'sonner';
import { LogoFull } from '@/components/Logo';
import TurnstileWidget from '@/components/TurnstileWidget';
import { useTurnstileConfig } from '@/hooks/useTurnstile';


const BENEFITS = [
  {
    icon: Sparkles,
    title: 'AI-Powered Tutor',
    desc: 'Instant, syllabus-aligned answers for AssamBoard students',
    color: '#7c3aed',
    bg: 'rgba(124,58,237,0.08)',
    border: 'rgba(139,92,246,0.18)',
  },
  {
    icon: MessageSquare,
    title: 'Chat History',
    desc: 'Every conversation saved and searchable — never start over',
    color: '#0891b2',
    bg: 'rgba(6,182,212,0.06)',
    border: 'rgba(6,182,212,0.15)',
  },
  {
    icon: BarChart3,
    title: 'Track Progress',
    desc: 'Transparent credit system — see exactly how much you use',
    color: '#059669',
    bg: 'rgba(16,185,129,0.06)',
    border: 'rgba(16,185,129,0.15)',
  },
];

export default function LoginPage() {
  const publicStats = usePublicStats();
  const userCount = publicStats?.total_users || 100;
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const { login } = useAuth();
  const navigate = useNavigate();
  const turnstileRef = useRef(null);
  const { enabled: turnstileEnabled } = useTurnstileConfig();
  const [turnstileToken, setTurnstileToken] = useState('');
  const submitBlocked = turnstileEnabled && !turnstileToken;

  const handleInputFocus = useCallback((e) => {
    setTimeout(() => {
      e.target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 300);
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    // Task #451 — when bot-protection is enabled but the widget has
    // not yet minted a token, block the submit at the page layer so
    // we never call the auth API without proof-of-human. This is the
    // pre-token gate that complements the backend's turnstile_required
    // dependency: even if the disabled-button is bypassed (devtools,
    // assistive tech, fireEvent.submit, etc.) handleSubmit still
    // refuses to dispatch.
    if (submitBlocked) {
      setError('Please complete the verification challenge before signing in.');
      return;
    }
    setLoading(true);
    // Task #404 — collect Turnstile token (empty when the widget is
    // disabled / not rendered, in which case the backend dependency
    // is dormant too so the request still succeeds).
    try {
      const user = await login(email, password, turnstileToken);
      toast.success('Welcome back!');
      setTimeout(() => {
        const role = user.role || '';
        if (role === 'staff' || role === 'admin') {
          navigate('/staff');
        } else if (!user.onboarding_done) {
          navigate('/onboarding');
        } else {
          navigate('/library');
        }
      }, 100);
    } catch (err) {
      setError(formatAuthError(err, 'Login failed. Please check your credentials.'));
    } finally {
      // Tokens are one-shot per the Turnstile contract — reset so the
      // next attempt mints a fresh one.
      turnstileRef.current?.reset?.();
      setTurnstileToken('');
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex bg-background">

      <div
        className="hidden lg:flex lg:w-[52%] relative flex-col justify-between p-12 overflow-hidden"
        style={{
          background: 'linear-gradient(135deg, #f5f3ff 0%, #ede9fe 40%, #f3f0ff 100%)',
        }}
      >
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute top-[-15%] left-[-10%] w-[600px] h-[600px] rounded-full"
            style={{ background: 'radial-gradient(circle, rgba(124,58,237,0.12) 0%, transparent 70%)', filter: 'blur(40px)' }} />
          <div className="absolute bottom-[-10%] right-[-15%] w-[500px] h-[500px] rounded-full"
            style={{ background: 'radial-gradient(circle, rgba(99,102,241,0.10) 0%, transparent 70%)', filter: 'blur(50px)' }} />
          <div className="absolute top-[40%] right-[10%] w-[300px] h-[300px] rounded-full"
            style={{ background: 'radial-gradient(circle, rgba(168,85,247,0.08) 0%, transparent 70%)', filter: 'blur(30px)' }} />
        </div>

        <div className="absolute inset-0 pointer-events-none opacity-[0.04]"
          style={{
            backgroundImage: 'linear-gradient(rgba(139,92,246,1) 1px,transparent 1px),linear-gradient(to right,rgba(139,92,246,1) 1px,transparent 1px)',
            backgroundSize: '60px 60px',
          }} />

        <div className="relative z-10">
          <Link to="/" className="inline-block mb-14" aria-label="Go to Syrabit home">
            <LogoFull size="md" textClassName="text-foreground text-2xl" />
          </Link>

          <div className="anim-slide-left">
            <div
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full mb-6"
              style={{ background: 'rgba(124,58,237,0.10)', border: '1px solid rgba(139,92,246,0.20)' }}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-violet-500 animate-pulse" />
              <span className="text-xs font-semibold tracking-widest text-violet-600">
                AI EXAM PREP
              </span>
            </div>
            <h2
              className="mb-4 text-foreground"
              style={{ fontSize: 'clamp(1.6rem, 3vw, 2.2rem)', fontWeight: 800, lineHeight: 1.18, letterSpacing: '-0.02em' }}
            >
              Educational Browser For<br />
              <span style={{ background: 'linear-gradient(135deg,#7c3aed,#a78bfa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
                AssamBoard Students
              </span>
            </h2>
            <p className="mb-10 max-w-sm leading-relaxed text-muted-foreground" style={{ fontSize: '0.95rem' }}>
              Get instant, syllabus-aligned answers for Class 11–12 subjects. Study smarter, not harder.
            </p>

            <div className="space-y-3">
              {BENEFITS.map(({ icon: Icon, title, desc, color, bg, border }, i) => (
                <div
                  key={title}
                  className="flex items-start gap-3.5 rounded-2xl p-4 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-lg"
                  style={{
                    background: bg,
                    border: `1px solid ${border}`,
                    animation: `slideInLeft 0.6s cubic-bezier(0.16,1,0.3,1) both ${0.3 + i * 0.12}s`,
                  }}
                >
                  <div
                    className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 mt-0.5"
                    style={{ background: 'rgba(124,58,237,0.06)', border: `1px solid ${border}` }}
                  >
                    <Icon size={16} style={{ color }} />
                  </div>
                  <div>
                    <p className="text-foreground text-sm font-semibold">{title}</p>
                    <p className="text-xs mt-0.5 leading-relaxed text-muted-foreground">{desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="relative z-10">
          <p className="text-xs text-muted-foreground">
            Trusted by {userCount}+ Assam board students · Free to start
          </p>
        </div>
      </div>

      <div className="w-full lg:w-[48%] flex items-center justify-center p-4 sm:p-6 relative overflow-y-auto" style={{ scrollPaddingBottom: '2rem' }}>
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute top-[20%] right-[10%] w-[300px] h-[300px] rounded-full opacity-60"
            style={{ background: 'radial-gradient(circle, rgba(124,58,237,0.06) 0%, transparent 70%)', filter: 'blur(40px)' }} />
        </div>

        <div className="w-full max-w-sm relative z-10 anim-slide-right">
          <Link to="/" className="flex items-center gap-2 mb-4 lg:hidden" aria-label="Go to Syrabit home">
            <LogoFull size="sm" textClassName="text-foreground" />
          </Link>

          <div
            className="rounded-2xl p-5 sm:p-7 overflow-y-auto auth-form-card glass-card"
          >
            <div className="mb-7">
              <h1 className="text-2xl font-bold text-foreground tracking-tight">Welcome back</h1>
              <p className="mt-1.5 text-sm text-muted-foreground">Sign in to your account to continue</p>
            </div>

            {error && (
              <div
                id="login-error-message"
                role="alert"
                className="flex items-center gap-2 text-red-600 rounded-xl p-3 mb-5 text-sm"
                style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.15)' }}
              >
                <AlertCircle size={16} className="flex-shrink-0" />
                {error}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="email" className="text-sm font-medium text-foreground/70">
                  Email address
                </Label>
                <div className="relative">
                  <Mail size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground/40" />
                  <Input
                    id="email"
                    name="email"
                    type="email"
                    autoComplete="email"
                    placeholder="your@email.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    onFocus={handleInputFocus}
                    className="pl-10 h-11"
                    style={{ scrollMarginBottom: '4rem' }}
                    required
                    aria-invalid={error ? true : undefined}
                    aria-describedby={error ? 'login-error-message' : undefined}
                    data-testid="auth-email-input"
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="password" className="text-sm font-medium text-foreground/70">
                  Password
                </Label>
                <div className="relative">
                  <Lock size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground/40" />
                  <Input
                    id="password"
                    name="password"
                    type={showPass ? 'text' : 'password'}
                    autoComplete="current-password"
                    placeholder="••••••••"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onFocus={handleInputFocus}
                    className="pl-10 pr-11 h-11"
                    style={{ scrollMarginBottom: '4rem' }}
                    required
                    aria-invalid={error ? true : undefined}
                    aria-describedby={error ? 'login-error-message' : undefined}
                    data-testid="auth-password-input"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPass(!showPass)}
                    aria-label={showPass ? 'Hide password' : 'Show password'}
                    className="absolute right-1 top-1/2 -translate-y-1/2 min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground/40 hover:text-foreground transition-colors"
                  >
                    {showPass ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
              </div>

              <div className="flex justify-end">
                <Link to="/reset-password" className="text-xs font-medium text-violet-600 hover:text-violet-700 transition-colors">
                  Forgot password?
                </Link>
              </div>

              <TurnstileWidget
                ref={turnstileRef}
                action="login"
                onToken={setTurnstileToken}
                onExpire={() => setTurnstileToken('')}
                onError={() => setTurnstileToken('')}
              />

              <button
                type="submit"
                disabled={loading || submitBlocked}
                aria-disabled={loading || submitBlocked ? true : undefined}
                className="w-full flex items-center justify-center gap-2 h-11 rounded-xl text-sm font-bold text-white transition-all duration-150 active:scale-[0.97] disabled:opacity-60 btn-gradient"
                data-testid="auth-submit-button"
              >
                {loading ? <Loader2 size={17} className="animate-spin" /> : null}
                {loading ? 'Signing in…' : 'Sign In'}
              </button>
            </form>

            <p className="text-center text-sm mt-6 text-muted-foreground">
              Don't have an account?{' '}
              <Link to="/signup" className="font-semibold text-violet-600 hover:text-violet-700 transition-colors">
                Sign up free
              </Link>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
