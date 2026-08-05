import { useState, useEffect } from 'react';
import { Receipt, RefreshCw, Loader2, CheckCircle, XCircle, Clock, AlertTriangle, ChevronDown, ChevronUp, CreditCard, Zap, Mail, X } from 'lucide-react';
import { getPaymentHistory, requestRefund } from '@/utils/api';
import { toast } from 'sonner';

const STATUS_CONFIG = {
  completed: { icon: CheckCircle, color: 'text-emerald-600', bg: 'bg-emerald-400/10', label: 'Completed' },
  failed:    { icon: XCircle,     color: 'text-red-600',     bg: 'bg-red-400/10',     label: 'Failed' },
  skipped:   { icon: AlertTriangle, color: 'text-amber-700', bg: 'bg-amber-400/10',   label: 'Skipped' },
  unknown:   { icon: Clock,       color: 'text-slate-600',   bg: 'bg-slate-400/10',   label: 'Pending' },
};

function formatDate(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
  } catch {
    return isoStr;
  }
}

function formatAmount(paise) {
  if (!paise && paise !== 0) return null;
  const n = parseInt(paise, 10);
  if (isNaN(n)) return null;
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
      <span className="text-sm font-medium text-foreground font-mono break-all text-right max-w-[55%]">{value}</span>
    </div>
  );
}

function ReceiptModal({ payment, onClose }) {
  if (!payment) return null;

  const amount = formatAmount(payment.amount);
  const orderId = payment.razorpay_order_id || payment.order_id || null;
  const paymentId = payment.razorpay_payment_id || payment.payment_id || null;

  // Derive plan/credits label from payment data
  let planLabel = null;
  if (payment.credits_added > 0) {
    planLabel = `${Number(payment.credits_added).toLocaleString()} credits`;
  } else if (payment.plan_label) {
    planLabel = `${payment.plan_label} Plan`;
  } else if (payment.plan) {
    planLabel = `${payment.plan.charAt(0).toUpperCase() + payment.plan.slice(1)} Plan`;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.55)' }}
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-sm rounded-2xl p-6 space-y-5"
        style={{ background: 'hsl(var(--background))', border: '1px solid hsl(var(--border))' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent/40 transition-colors"
          aria-label="Close receipt"
        >
          <X size={16} />
        </button>

        {/* Header */}
        <div className="flex flex-col items-center gap-3 text-center">
          <div
            className="w-14 h-14 rounded-full flex items-center justify-center"
            style={{ background: 'rgba(16,185,129,0.12)', border: '2px solid rgba(16,185,129,0.30)' }}
          >
            <CheckCircle size={28} className="text-emerald-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-foreground">Payment Receipt</h2>
            <p className="text-xs text-muted-foreground mt-0.5">{payment.description}</p>
          </div>
        </div>

        {/* Receipt card */}
        <div
          className="rounded-2xl px-4 py-1"
          style={{ background: 'rgba(124,58,237,0.06)', border: '1px solid rgba(139,92,246,0.18)' }}
        >
          <ReceiptRow icon={CreditCard} label="Amount paid"   value={amount} />
          <ReceiptRow icon={Zap}        label="Plan / top-up" value={planLabel} />
          <ReceiptRow icon={Clock}      label="Date"          value={formatDate(payment.date || payment.created_at)} />
          <ReceiptRow icon={Receipt}    label="Order ID"      value={orderId} />
          <ReceiptRow icon={Receipt}    label="Payment ID"    value={paymentId} />
        </div>

        {/* Email note */}
        <div
          className="flex items-start gap-3 rounded-xl px-4 py-3"
          style={{ background: 'rgba(16,185,129,0.07)', border: '1px solid rgba(16,185,129,0.18)' }}
        >
          <Mail size={15} className="text-emerald-400 mt-0.5 shrink-0" />
          <p className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground">Check your email</span> — a receipt was sent to the address on your account.
          </p>
        </div>

        <button
          onClick={onClose}
          className="w-full h-10 rounded-xl text-sm font-semibold text-white transition-all hover:opacity-90"
          style={{ background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)' }}
        >
          Close
        </button>
      </div>
    </div>
  );
}

export default function PaymentHistory({ refreshKey = 0 }) {
  const [payments, setPayments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(true);
  const [refundingId, setRefundingId] = useState(null);
  const [refundReason, setRefundReason] = useState('');
  const [showRefundDialog, setShowRefundDialog] = useState(null);
  const [receiptPayment, setReceiptPayment] = useState(null);

  const fetchPayments = async () => {
    setLoading(true);
    try {
      const res = await getPaymentHistory();
      // Backend returns { payments: [...] }, not a bare array
      setPayments(Array.isArray(res.data) ? res.data : (res.data?.payments || []));
    } catch {
      toast.error('Failed to load payment history');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchPayments(); }, [refreshKey]);

  const handleRefundRequest = async (paymentId) => {
    setRefundingId(paymentId);
    try {
      const res = await requestRefund(paymentId, refundReason);
      toast.success(res.data?.message || 'Refund request submitted');
      setShowRefundDialog(null);
      setRefundReason('');
      await fetchPayments();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to submit refund request');
    } finally {
      setRefundingId(null);
    }
  };

  return (
    <>
      <div className="glass-card rounded-2xl overflow-hidden">
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full px-4 py-3 border-b border-border flex items-center justify-between hover:bg-accent/20 transition-colors"
        >
          <div className="flex items-center gap-2">
            <Receipt size={14} className="text-muted-foreground" />
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Payment History</p>
          </div>
          <div className="flex items-center gap-2">
            {payments.length > 0 && (
              <span className="text-xs text-muted-foreground">{payments.length} transaction{payments.length !== 1 ? 's' : ''}</span>
            )}
            {expanded ? <ChevronUp size={14} className="text-muted-foreground" /> : <ChevronDown size={14} className="text-muted-foreground" />}
          </div>
        </button>

        {expanded && (
          <div className="p-4">
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 size={20} className="animate-spin text-muted-foreground" />
              </div>
            ) : payments.length === 0 ? (
              <div className="text-center py-8">
                <Receipt size={28} className="mx-auto text-muted-foreground/70 mb-2" />
                <p className="text-sm text-muted-foreground">No transactions yet</p>
                <p className="text-xs text-muted-foreground/60 mt-1">Your payment history will appear here</p>
              </div>
            ) : (
              <div className="space-y-2">
                {payments.map((p) => {
                  const statusCfg = STATUS_CONFIG[p.status] || STATUS_CONFIG.unknown;
                  const StatusIcon = statusCfg.icon;
                  const canRefund = p.status === 'completed' && !p.refund_status;
                  const isRefundRequested = p.refund_status === 'requested';
                  const isRefundProcessed = p.refund_status === 'processed';
                  const hasReceiptData = p.razorpay_order_id || p.razorpay_payment_id || p.order_id || p.payment_id;

                  return (
                    <div key={p.id || p._id || p.date} className="rounded-xl p-3 transition-colors"
                      style={{ background: 'hsl(var(--muted) / 0.15)', border: '1px solid hsl(var(--border) / 0.25)' }}>
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <p className="text-sm font-medium text-foreground truncate">{p.description}</p>
                            <span className={`inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-full ${statusCfg.color} ${statusCfg.bg}`}>
                              <StatusIcon size={10} />
                              {statusCfg.label}
                            </span>
                          </div>
                          <div className="flex items-center gap-3 text-xs text-muted-foreground">
                            <span>{formatDate(p.date || p.created_at)}</span>
                            {p.provider && <span className="capitalize">{p.provider}</span>}
                            {p.credits_added > 0 && <span>+{p.credits_added} credits</span>}
                          </div>
                        </div>
                        <p className="text-sm font-bold text-foreground whitespace-nowrap">{p.amount_display || p.amount_formatted || p.amount}</p>
                      </div>

                      {/* View receipt + refund row */}
                      <div className="mt-2 flex items-center gap-3">
                        {hasReceiptData && p.status === 'completed' && (
                          <button
                            onClick={() => setReceiptPayment(p)}
                            className="text-xs text-violet-500 hover:text-violet-400 transition-colors font-medium flex items-center gap-1"
                          >
                            <Receipt size={11} />
                            View receipt
                          </button>
                        )}

                        {isRefundRequested && (
                          <div className="flex items-center gap-1.5 text-xs text-amber-700">
                            <Clock size={12} />
                            Refund requested {p.refund_requested_at ? `on ${formatDate(p.refund_requested_at)}` : ''}
                          </div>
                        )}
                        {isRefundProcessed && (
                          <div className="flex items-center gap-1.5 text-xs text-emerald-600">
                            <CheckCircle size={12} />
                            Refund processed
                          </div>
                        )}

                        {canRefund && showRefundDialog !== p.id && !isRefundRequested && !isRefundProcessed && (
                          <button
                            onClick={() => setShowRefundDialog(p.id || p._id)}
                            className="text-xs text-muted-foreground hover:text-foreground transition-colors underline underline-offset-2"
                          >
                            Request refund
                          </button>
                        )}
                      </div>

                      {showRefundDialog === (p.id || p._id) && (
                        <div className="mt-3 space-y-2 rounded-lg p-3"
                          style={{ background: 'rgba(139,92,246,0.04)', border: '1px solid rgba(139,92,246,0.15)' }}>
                          <p className="text-xs text-muted-foreground">Why would you like a refund? (optional)</p>
                          <textarea
                            value={refundReason}
                            onChange={(e) => setRefundReason(e.target.value)}
                            placeholder="Tell us the reason..."
                            className="w-full h-16 rounded-lg px-3 py-2 text-xs bg-background border border-border text-foreground placeholder:text-muted-foreground/70 resize-none focus:outline-none focus:ring-1 focus:ring-violet-500"
                          />
                          <div className="flex gap-2">
                            <button
                              onClick={() => { setShowRefundDialog(null); setRefundReason(''); }}
                              className="flex-1 h-8 rounded-lg text-xs font-medium text-muted-foreground border border-border hover:bg-accent/40 transition-colors"
                            >
                              Cancel
                            </button>
                            <button
                              onClick={() => handleRefundRequest(p.id || p._id)}
                              disabled={refundingId === (p.id || p._id)}
                              className="flex-1 h-8 rounded-lg text-xs font-semibold text-white flex items-center justify-center gap-1 transition-all hover:opacity-90 disabled:opacity-50"
                              style={{ background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)' }}
                            >
                              {refundingId === (p.id || p._id) ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                              Submit Request
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {receiptPayment && (
        <ReceiptModal payment={receiptPayment} onClose={() => setReceiptPayment(null)} />
      )}
    </>
  );
}
