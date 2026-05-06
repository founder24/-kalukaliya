import { useState, useCallback, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Mail, Lock, Eye, EyeOff, Loader2, User, CheckCircle, AlertCircle, BookOpen, Zap, GraduationCap } from 'lucide-react';
import { usePublicStats } from '@/hooks/usePublicStats';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAuth } from '@/context/AuthContext';
import { formatAuthError } from '@/lib/authErrors';
import { toast } from 'sonner';
import { LogoFull } from '@/components/Logo';
import GoogleSignInButton from '@/components/GoogleSignInButton';
import TurnstileWidget from '@/components/TurnstileWidget';
import { useTurnstileConfig } from '@/hooks/useTurnstile';


const getPasswordStrength = (password) => {
  if (password.length === 0) return { score: 0, label: '' };
  if (password.length < 6) return { score: 1, label: 'Too short' };
  let score = 1;
  if (password.length >= 8) score++;
  if (/[A-Z]/.test(password)) score++;
  if (/[0-9]/.test(password)) score++;
  if (/[^A-Za-z0-9]/.test(password)) score++;
  const labels = ['', 'Weak', 'Fair', 'Good', 'Strong', 'Very Strong'];
  return { score: Math.min(score, 5), label: labels[Math.min(score, 5)] };
};

const STRENGTH_COLORS = ['', 'bg-red-500', 'bg-orange-500', 'bg-yellow-500', 'bg-emerald-500', 'bg-emerald-400'];

const PERKS = [
  { icon: BookOpen, text: 'Browse all 55+ subjects — free forever' },
  { icon: Zap, text: 'Starter: 300 credits for just ₹99' },
  { icon: GraduationCap, text: 'Ask Syra — your Assam board study companion' },
  { icon: CheckCircle, text: 'Upgrade anytime — no lock-in' },
];

export default function SignupPage() {
  const publicStats = usePublicStats();
  const userCount = publicStats?.total_users || 100;
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [consentDpdp, setConsentDpdp] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [emailError, setEmailError] = useState('');
  const { signup } = useAuth();
  const navigate = useNavigate();
  const turnstileRef = useRef(null);
  const { enabled: turnstileEnabled } = useTurnstileConfig();
  const [turnstileToken, setTurnstileToken] = useState('');
  const submitBlocked = turnstileEnabled && !turnstileToken;

  const strength = getPasswordStrength(password);
  const passwordsMatch = confirmPassword && password === confirmPassword;

  const handleInputFocus = useCallback((e) => {
    setTimeout(() => {
      e.target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 300);
  }, []);

  const validateEmail = (value) => {
    if (!value) return '';
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) ? '' : 'Please enter a valid email address';
  };

  const handleEmailChange = (e) => {
    const value = e.target.value;
    setEmail(value);
    if (emailError) setEmailError(validateEmail(value));
  };

  const handleEmailBlur = (e) => {
    setEmailError(validateEmail(e.target.value));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    const emailValidationError = validateEmail(email);
    if (emailValidationError) {
      setEmailError(emailValidationError);
      return;
    }
    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }
    if (!agreed) {
      setError('Please agree to the Terms of Service');
      return;
    }
    if (!consentDpdp) {
      setError('Please provide consent for data processing under the DPDP Act');
      return;
    }
    // Task #451 — when bot-protection is enabled but the widget has
    // not yet minted a token, block at the page layer so we never
    // dispatch the signup API call without proof-of-human. This is
    // the pre-token gate that complements the backend's
    // turnstile_required dependency.
    if (submitBlocked) {
      setError('Please complete the verification challenge before creating your account.');
      return;
    }
    setLoading(true);
    // Task #404 — turnstile token captured via TurnstileWidget's
    // onToken callback (empty when the widget is disabled / not
    // rendered; backend dependency is dormant in that case so the
    // request still succeeds).
    try {
      const user = await signup(name, email, password, consentDpdp, turnstileToken);
      toast.success('Account created! Welcome to Syrabit.ai!');
      const role = user?.role || '';
      if (role === 'staff' || role === 'admin') {
        navigate('/staff');
      } else {
        navigate('/onboarding');
      }
    } catch (err) {
      setError(formatAuthError(err, 'Signup failed. Please try again.'));
    } finally {
      // Tokens are one-shot per the Turnstile contract.
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
          background: 'linear-gradient(135deg, #f0fdf4 0%, #ecfdf5 40%, #f5f3ff 100%)',
        }}
      >
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute top-[-15%] left-[-10%] w-[600px] h-[600px] rounded-full"
            style={{ background: 'radial-gradient(circle, rgba(124,58,237,0.10) 0%, transparent 70%)', filter: 'blur(40px)' }} />
          <div className="absolute bottom-[-10%] right-[-15%] w-[500px] h-[500px] rounded-full"
            style={{ background: 'radial-gradient(circle, rgba(5,150,105,0.08) 0%, transparent 70%)', filter: 'blur(50px)' }} />
          <div className="absolute top-[35%] right-[5%] w-[350px] h-[350px] rounded-full"
            style={{ background: 'radial-gradient(circle, rgba(168,85,247,0.06) 0%, transparent 70%)', filter: 'blur(35px)' }} />
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
              style={{ background: 'rgba(16,185,129,0.10)', border: '1px solid rgba(16,185,129,0.20)' }}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
              <span className="text-xs font-semibold tracking-widest text-emerald-600">
                FREE TO START
              </span>
            </div>
            <h2
              className="mb-4 text-foreground"
              style={{ fontSize: 'clamp(1.6rem, 3vw, 2.2rem)', fontWeight: 800, lineHeight: 1.18, letterSpacing: '-0.02em' }}
            >
              For AHSEC & Degree<br />
              <span style={{ background: 'linear-gradient(135deg,#7c3aed,#a78bfa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
                students
              </span>
            </h2>
            <p className="mb-10 max-w-sm leading-relaxed text-muted-foreground" style={{ fontSize: '0.95rem' }}>
              Start your AI-powered exam prep journey today. No credit card required to get started.
            </p>

            <div className="space-y-3.5">
              {PERKS.map(({ icon: Icon, text }, i) => (
                <div
                  key={text}
                  className="flex items-center gap-3.5"
                  style={{ animation: `slideInLeft 0.6s cubic-bezier(0.16,1,0.3,1) both ${0.3 + i * 0.1}s` }}
                >
                  <div
                    className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0"
                    style={{ background: 'rgba(16,185,129,0.10)', border: '1px solid rgba(16,185,129,0.18)' }}
                  >
                    <Icon size={14} className="text-emerald-600" />
                  </div>
                  <span className="text-sm text-foreground/70">{text}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="relative z-10">
          <p className="text-xs text-muted-foreground">
            Trusted by {userCount}+ Assam board students
          </p>
        </div>
      </div>

      <div className="w-full lg:w-[48%] flex items-center justify-center p-4 sm:p-6 relative overflow-y-auto" style={{ scrollPaddingBottom: '2rem' }}>
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute top-[20%] right-[10%] w-[300px] h-[300px] rounded-full opacity-60"
            style={{ background: 'radial-gradient(circle, rgba(124,58,237,0.06) 0%, transparent 70%)', filter: 'blur(40px)' }} />
        </div>

        <div className="w-full max-w-sm relative z-10 anim-slide-right py-4 lg:py-8">
          <Link to="/" className="flex items-center gap-2 mb-4 lg:hidden" aria-label="Go to Syrabit home">
            <LogoFull size="sm" textClassName="text-foreground" />
          </Link>

          <div className="rounded-2xl p-5 sm:p-7 overflow-y-auto auth-form-card glass-card">
            <div className="mb-7">
              <h1 className="text-2xl font-bold text-foreground tracking-tight">Create your account</h1>
              <p className="mt-1.5 text-sm text-muted-foreground">Start for free — no credit card required</p>
            </div>

            {error && (
              <div
                id="signup-error-message"
                role="alert"
                className="flex items-center gap-2 text-red-600 rounded-xl p-3 mb-5 text-sm"
                style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.15)' }}
              >
                <AlertCircle size={16} className="flex-shrink-0" />
                {error}
              </div>
            )}

            <div className="mb-5">
              <GoogleSignInButton
                text="signup_with"
                disabled={loading}
                onError={(err) => {
                  setError(formatAuthError(err, 'Google sign-up failed. Please try again.'));
                }}
              />
              <p className="text-[11px] text-center text-muted-foreground/80 mt-3 leading-relaxed">
                By continuing with Google, you agree to our{' '}
                <Link to="/terms" target="_blank" rel="noopener noreferrer" className="font-medium text-violet-600 hover:text-violet-700 transition-colors">Terms</Link>
                {' '}and consent to data processing per our{' '}
                <Link to="/privacy" target="_blank" rel="noopener noreferrer" className="font-medium text-violet-600 hover:text-violet-700 transition-colors">Privacy Policy</Link>
                {' '}under the DPDP Act, 2023.
              </p>
              <div className="flex items-center gap-3 mt-5 text-[11px] uppercase tracking-wider text-muted-foreground/70">
                <div className="flex-1 h-px bg-border/70" />
                <span>or sign up with email</span>
                <div className="flex-1 h-px bg-border/70" />
              </div>
            </div>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="name" className="text-sm font-medium text-foreground/70">
                  Full Name
                </Label>
                <div className="relative">
                  <User size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground/40" />
                  <Input
                    id="name"
                    name="name"
                    autoComplete="name"
                    placeholder="Your name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    onFocus={handleInputFocus}
                    className="pl-10 h-11"
                    style={{ scrollMarginBottom: '4rem' }}
                    required
                  />
                </div>
              </div>

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
                    onChange={handleEmailChange}
                    onBlur={handleEmailBlur}
                    onFocus={handleInputFocus}
                    className={`pl-10 h-11 ${emailError ? 'border-red-500/40' : ''}`}
                    style={{ scrollMarginBottom: '4rem' }}
                    required
                    aria-invalid={emailError ? true : undefined}
                    aria-describedby={emailError ? 'email-format-error' : undefined}
                    data-testid="auth-email-input"
                  />
                </div>
                {emailError && (
                  <p id="email-format-error" role="alert" className="text-xs text-red-500">{emailError}</p>
                )}
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
                    autoComplete="new-password"
                    placeholder="••••••••"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onFocus={handleInputFocus}
                    className={`pl-10 pr-11 h-11 ${confirmPassword && !passwordsMatch ? 'border-red-500/40' : ''}`}
                    style={{ scrollMarginBottom: '4rem' }}
                    required
                    aria-invalid={confirmPassword && !passwordsMatch ? true : undefined}
                    aria-describedby={password ? 'password-strength-hint' : undefined}
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
                {password && (
                  <div className="space-y-1 pt-1">
                    <div className="flex gap-1">
                      {[1, 2, 3, 4].map((i) => (
                        <div
                          key={i}
                          className={`h-1 flex-1 rounded-full transition-all duration-300 ${i <= strength.score ? STRENGTH_COLORS[strength.score] : 'bg-muted'}`}
                        />
                      ))}
                    </div>
                    <p
                      id="password-strength-hint"
                      aria-live="polite"
                      className={`text-xs ${strength.score <= 2 ? 'text-orange-500' : 'text-emerald-600'}`}
                    >
                      {strength.label}
                    </p>
                  </div>
                )}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="confirm" className="text-sm font-medium text-foreground/70">
                  Confirm Password
                </Label>
                <div className="relative">
                  <Lock size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground/40" />
                  <Input
                    id="confirm"
                    name="confirm-password"
                    type={showConfirm ? 'text' : 'password'}
                    autoComplete="new-password"
                    placeholder="••••••••"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    onFocus={handleInputFocus}
                    className={`pl-10 pr-11 h-11 ${confirmPassword && !passwordsMatch ? 'border-red-500/40' : ''}`}
                    style={{ scrollMarginBottom: '4rem' }}
                    required
                    aria-invalid={confirmPassword && !passwordsMatch ? true : undefined}
                    aria-describedby={confirmPassword && !passwordsMatch ? 'confirm-password-mismatch' : undefined}
                  />
                  <button
                    type="button"
                    onClick={() => setShowConfirm(!showConfirm)}
                    aria-label={showConfirm ? 'Hide confirm password' : 'Show confirm password'}
                    className="absolute right-1 top-1/2 -translate-y-1/2 min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground/40 hover:text-foreground transition-colors"
                  >
                    {showConfirm ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
                {confirmPassword && !passwordsMatch && (
                  <p id="confirm-password-mismatch" className="text-xs text-red-500" role="alert">Passwords don't match</p>
                )}
              </div>

              <div className="flex items-start gap-1 py-1">
                <button
                  type="button"
                  onClick={() => setAgreed(!agreed)}
                  className="-ml-3 p-3 min-w-[44px] min-h-[44px] rounded flex-shrink-0 flex items-center justify-center transition-all cursor-pointer"
                  aria-label="Agree to terms"
                  aria-invalid={error === 'Please agree to the Terms of Service' ? 'true' : undefined}
                  aria-describedby={error === 'Please agree to the Terms of Service' ? 'signup-error-message' : undefined}
                >
                  <span
                    className={`w-4 h-4 rounded border flex items-center justify-center transition-all ${agreed ? 'border-violet-500' : 'border-border bg-muted/50'}`}
                    style={agreed ? { background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)' } : {}}
                  >
                    {agreed && <svg width="10" height="8" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
                  </span>
                </button>
                <span className="text-xs text-muted-foreground">
                  I agree to the{' '}
                  <Link to="/terms" target="_blank" rel="noopener noreferrer" className="font-medium text-violet-600 hover:text-violet-700 transition-colors">Terms</Link>
                  {' '}and{' '}
                  <Link to="/privacy" target="_blank" rel="noopener noreferrer" className="font-medium text-violet-600 hover:text-violet-700 transition-colors">Privacy Policy</Link>
                </span>
              </div>

              <div className="flex items-start gap-1 py-1">
                <button
                  type="button"
                  onClick={() => setConsentDpdp(!consentDpdp)}
                  className="-ml-3 p-3 min-w-[44px] min-h-[44px] rounded flex-shrink-0 flex items-center justify-center transition-all cursor-pointer"
                  aria-label="Consent to data processing"
                  aria-invalid={error === 'Please provide consent for data processing under the DPDP Act' ? 'true' : undefined}
                  aria-describedby={error === 'Please provide consent for data processing under the DPDP Act' ? 'signup-error-message' : undefined}
                >
                  <span
                    className={`w-4 h-4 rounded border flex items-center justify-center transition-all ${consentDpdp ? 'border-violet-500' : 'border-border bg-muted/50'}`}
                    style={consentDpdp ? { background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)' } : {}}
                  >
                    {consentDpdp && <svg width="10" height="8" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
                  </span>
                </button>
                <span className="text-xs text-muted-foreground">
                  I consent to the processing of my personal data as described in the{' '}
                  <Link to="/privacy" target="_blank" rel="noopener noreferrer" className="font-medium text-violet-600 hover:text-violet-700 transition-colors">Privacy Policy</Link>
                  {' '}under the DPDP Act, 2023
                </span>
              </div>

              <TurnstileWidget
                ref={turnstileRef}
                action="signup"
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
                {loading ? 'Creating account…' : 'Create Account'}
              </button>
            </form>

            <p className="text-center text-sm mt-6 text-muted-foreground">
              Already have an account?{' '}
              <Link to="/login" className="font-semibold text-violet-600 hover:text-violet-700 transition-colors">
                Sign in
              </Link>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
