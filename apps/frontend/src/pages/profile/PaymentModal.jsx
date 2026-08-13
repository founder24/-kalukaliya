import { useState, useEffect, useCallback, useRef } from 'react';
import { CheckCircle, Loader2, RefreshCw, QrCode } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { PLANS, PLAN_FEATURES } from './planConfig';
import { DOC_ACCESS_CONFIG } from '@/utils/plans';
import ModalOverlay from '@/components/ui/ModalOverlay';
import { apiClient } from '@/utils/api';
import { toast } from 'sonner';

const POLL_INTERVAL_MS = 3000;

export default function PaymentModal({
  showPaymentModal,
  paymentPlan,
  setShowPaymentModal,
}) {
  const navigate = useNavigate();
  const [qrState, setQrState] = useState('idle'); // idle | loading | ready | error | paid
  const [qrData, setQrData]   = useState(null);   // { qr_code_id, image_url, amount, expires_in }
  const [elapsed, setElapsed] = useState(0);
  const pollRef  = useRef(null);
  const timerRef = useRef(null);

  const cleanup = () => {
    clearInterval(pollRef.current);
    clearInterval(timerRef.current);
    pollRef.current  = null;
    timerRef.current = null;
  };

  const fetchQR = useCallback(async () => {
    if (!paymentPlan) return;
    cleanup();
    setQrState('loading');
    setQrData(null);
    setElapsed(0);

    try {
      const { data } = await apiClient().post('/payments/create-qr', { plan: paymentPlan });
      setQrData(data);
      setQrState('ready');

      // Countdown (seconds elapsed since QR was generated)
      timerRef.current = setInterval(() => setElapsed(e => e + 1), 1000);

      // Poll for payment
      pollRef.current = setInterval(async () => {
        try {
          const { data: poll } = await apiClient().get(`/payments/poll-qr/${data.qr_code_id}`);
          if (poll.status === 'paid') {
            cleanup();
            setQrState('paid');
            if (poll.receipt_token) {
              sessionStorage.setItem('receipt_token', poll.receipt_token);
            }
            setTimeout(() => {
              const params = new URLSearchParams({
                type:       'subscription',
                plan:       paymentPlan,
                order_id:   data.qr_code_id,
                payment_id: poll.payment_id || '',
                amount:     String(data.amount),
              });
              navigate(`/payment/success?${params.toString()}`);
            }, 1000);
          }
        } catch {
          // Network hiccup — keep polling
        }
      }, POLL_INTERVAL_MS);

    } catch (err) {
      setQrState('error');
      toast.error(
        err?.response?.data?.detail || 'Failed to generate QR code. Please try again.'
      );
    }
  }, [paymentPlan, navigate]);

  // Fetch QR when modal opens; clean up when it closes / unmounts
  useEffect(() => {
    if (showPaymentModal && paymentPlan) {
      fetchQR();
    } else {
      cleanup();
    }
    return cleanup;
  }, [showPaymentModal, paymentPlan]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleClose = () => {
    cleanup();
    setShowPaymentModal(false);
    setQrState('idle');
    setQrData(null);
  };

  if (!showPaymentModal || !paymentPlan) return null;

  const plan = PLANS[paymentPlan];
  const expiresIn  = qrData ? Math.max(0, qrData.expires_in - elapsed) : 0;
  const mm = String(Math.floor(expiresIn / 60)).padStart(2, '0');
  const ss = String(expiresIn % 60).padStart(2, '0');
  const expired = qrData && expiresIn === 0 && qrState === 'ready';

  return (
    <ModalOverlay
      open={showPaymentModal && !!paymentPlan}
      onClose={handleClose}
      title={`Upgrade to ${plan.label}`}
      borderColor="rgba(139,92,246,0.25)"
      backdropOpacity="0.7"
    >
      {/* Plan summary */}
      <div
        className="rounded-xl p-4 text-center"
        style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(139,92,246,0.20)' }}
      >
        <p className="text-3xl font-bold" style={{ color: paymentPlan === 'pro' ? '#f59e0b' : 'hsl(var(--primary))' }}>
          {plan.price}
        </p>
        <p className="text-muted-foreground text-sm">{plan.period.trim()}</p>
        <p className="text-foreground font-medium mt-1">
          {plan.credits.toLocaleString()} AI credits
        </p>
        <p className={`text-sm font-semibold mt-1 ${DOC_ACCESS_CONFIG[plan.docAccess]?.color}`}>
          {DOC_ACCESS_CONFIG[plan.docAccess]?.icon} {DOC_ACCESS_CONFIG[plan.docAccess]?.label}
        </p>
      </div>

      {/* Feature list */}
      <ul className="space-y-2">
        {PLAN_FEATURES[paymentPlan].map((f) => (
          <li key={f} className="flex items-center gap-2 text-sm text-muted-foreground/80">
            <CheckCircle size={14} className="text-emerald-600 flex-shrink-0" />
            {f}
          </li>
        ))}
      </ul>

      {/* QR code section */}
      <div
        className="rounded-xl overflow-hidden"
        style={{ border: '1px solid rgba(139,92,246,0.20)' }}
      >
        {/* Loading */}
        {qrState === 'loading' && (
          <div className="flex flex-col items-center justify-center gap-3 py-10 px-4">
            <Loader2 size={32} className="animate-spin text-violet-500" />
            <p className="text-sm text-muted-foreground">Generating payment QR…</p>
          </div>
        )}

        {/* Ready — show QR image */}
        {qrState === 'ready' && qrData && !expired && (
          <div className="flex flex-col items-center gap-3 p-4">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              Scan with any UPI app to pay
            </p>

            {/* QR image hosted by Razorpay */}
            <div className="rounded-xl overflow-hidden bg-white p-2 shadow-sm">
              <img
                src={qrData.image_url}
                alt="UPI Payment QR Code"
                width={200}
                height={200}
                className="block"
                draggable={false}
              />
            </div>

            <p className="text-base font-bold text-foreground">
              ₹{(qrData.amount / 100).toFixed(0)} — {plan.label} Plan
            </p>

            <p className="text-xs text-muted-foreground text-center leading-relaxed">
              Open <strong>Google Pay, PhonePe, Paytm</strong> or any UPI app<br />
              → tap <em>Scan QR</em> → scan above → approve
            </p>

            {/* Live polling indicator + countdown */}
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
              Waiting for payment · expires in {mm}:{ss}
            </div>
          </div>
        )}

        {/* QR expired */}
        {qrState === 'ready' && expired && (
          <div className="flex flex-col items-center justify-center gap-3 py-8 px-4">
            <QrCode size={32} className="text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground text-center">QR code expired.</p>
            <button
              onClick={fetchQR}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium text-violet-600 border border-violet-200 hover:bg-violet-50 transition-colors"
            >
              <RefreshCw size={14} /> Generate new QR
            </button>
          </div>
        )}

        {/* Error */}
        {qrState === 'error' && (
          <div className="flex flex-col items-center justify-center gap-3 py-8 px-4">
            <QrCode size={32} className="text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground text-center">
              Could not generate QR code.<br />
              <span className="text-xs">Check your connection and try again.</span>
            </p>
            <button
              onClick={fetchQR}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium text-violet-600 border border-violet-200 hover:bg-violet-50 transition-colors"
            >
              <RefreshCw size={14} /> Try again
            </button>
          </div>
        )}

        {/* Paid */}
        {qrState === 'paid' && (
          <div className="flex flex-col items-center justify-center gap-3 py-8 px-4">
            <div
              className="w-14 h-14 rounded-full flex items-center justify-center"
              style={{ background: 'rgba(16,185,129,0.12)', border: '2px solid rgba(16,185,129,0.30)' }}
            >
              <CheckCircle size={28} className="text-emerald-400" />
            </div>
            <p className="text-sm font-semibold text-emerald-600">Payment received! Redirecting…</p>
          </div>
        )}
      </div>

      <p className="text-center text-xs text-muted-foreground">
        Secured by Razorpay · UPI QR · ₹{plan.price.replace('₹', '')} pre-filled
      </p>
    </ModalOverlay>
  );
}
