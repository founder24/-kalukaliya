import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { CheckCircle, ArrowRight, Mail, Receipt, Zap, CreditCard } from 'lucide-react';
import { PublicLayout } from '@/components/layout/PublicLayout';
import { PageTitle } from '@/components/PageTitle';
import { PLANS } from './profile/planConfig';

function formatAmount(raw) {
  if (!raw) return null;
  const n = parseInt(raw, 10);
  if (isNaN(n) || n <= 0) return null;
  return `₹${(n / 100).toFixed(0)}`;
}

function ReceiptRow({ icon: Icon, label, value }) {
  if (!value) return null;
  return (
    <div className="flex items-center justify-between gap-3 py-2.5 border-b border-border/50 last:border-0">
      <div className="flex items-center gap-2 text-muted-foreground text-sm">
        <Icon size={14} className="shrink-0" />
        <span>{label}</span>
      </div>
      <span className="text-sm font-medium text-foreground font-mono">{value}</span>
    </div>
  );
}

export default function PaymentSuccessPage() {
  const [searchParams] = useSearchParams();
  const [countdown, setCountdown] = useState(10);

  const type      = searchParams.get('type') || 'subscription';
  const plan      = searchParams.get('plan');
  const credits   = searchParams.get('credits');
  const orderId   = searchParams.get('order_id');
  const paymentId = searchParams.get('payment_id');
  const amount    = formatAmount(searchParams.get('amount'));

  const planInfo = plan ? PLANS[plan] : null;

  const creditsNum = credits !== null ? Number(credits) : NaN;
  const creditsDisplay = !isNaN(creditsNum) && creditsNum > 0
    ? creditsNum.toLocaleString()
    : null;

  const heading = type === 'topup'
    ? creditsDisplay ? `${creditsDisplay} Credits Added` : 'Credits Added'
    : planInfo
      ? `${planInfo.label} Plan Activated`
      : 'Payment Successful!';

  const subtext = type === 'topup'
    ? creditsDisplay
      ? `${creditsDisplay} AI credits have been added to your account.`
      : 'Credits have been added to your account.'
    : planInfo
      ? `${planInfo.creditsLabel} AI credits are now available on your account.`
      : 'Your payment was processed successfully.';

  useEffect(() => {
    const timer = setInterval(() => {
      setCountdown(c => {
        if (c <= 1) {
          clearInterval(timer);
          window.location.href = '/profile';
        }
        return c - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <PublicLayout>
      <PageTitle title="Payment Successful | Syrabit.ai" />
      <div className="min-h-[70vh] flex items-center justify-center px-4 py-10">
        <div className="max-w-sm w-full space-y-6">

          {/* Success icon */}
          <div className="flex flex-col items-center gap-4">
            <div className="w-20 h-20 rounded-full flex items-center justify-center"
              style={{ background: 'rgba(16,185,129,0.12)', border: '2px solid rgba(16,185,129,0.30)' }}>
              <CheckCircle size={40} className="text-emerald-400" />
            </div>
            <div className="text-center space-y-1">
              <h1 className="text-2xl font-bold text-foreground">{heading}</h1>
              <p className="text-muted-foreground text-sm">{subtext}</p>
            </div>
          </div>

          {/* Receipt card */}
          <div className="rounded-2xl px-5 py-1"
            style={{ background: 'rgba(124,58,237,0.06)', border: '1px solid rgba(139,92,246,0.18)' }}>
            <ReceiptRow icon={CreditCard} label="Amount paid"    value={amount} />
            <ReceiptRow icon={Zap}        label="Plan / top-up"  value={planInfo ? `${planInfo.label} Plan` : creditsDisplay ? `${creditsDisplay} credits` : null} />
            <ReceiptRow icon={Receipt}    label="Order ID"        value={orderId} />
            <ReceiptRow icon={Receipt}    label="Payment ID"      value={paymentId} />
          </div>

          {/* Email note */}
          <div className="flex items-start gap-3 rounded-xl px-4 py-3"
            style={{ background: 'rgba(16,185,129,0.07)', border: '1px solid rgba(16,185,129,0.18)' }}>
            <Mail size={16} className="text-emerald-400 mt-0.5 shrink-0" />
            <p className="text-sm text-muted-foreground">
              <span className="font-medium text-foreground">Check your email</span> — a receipt has been sent to the address on your account.
            </p>
          </div>

          {/* Countdown & CTA */}
          <div className="space-y-3">
            <p className="text-center text-xs text-muted-foreground">
              Redirecting to profile in {Math.max(0, countdown)}s…
            </p>
            <div className="flex flex-col sm:flex-row gap-2">
              <Link
                to="/profile"
                className="flex-1 inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold text-white transition-all hover:opacity-90"
                style={{ background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)' }}
              >
                Go to Profile <ArrowRight size={15} />
              </Link>
              <Link
                to="/chat"
                className="flex-1 inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium text-muted-foreground border border-border hover:bg-accent/40 transition-colors"
              >
                Start Chatting
              </Link>
            </div>
          </div>
        </div>
      </div>
    </PublicLayout>
  );
}
