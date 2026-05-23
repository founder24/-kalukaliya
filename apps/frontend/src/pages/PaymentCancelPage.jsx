import { Link } from 'react-router-dom';
import { XCircle, ArrowLeft, CreditCard } from 'lucide-react';
import { PublicLayout } from '@/components/layout/PublicLayout';
import { PageTitle } from '@/components/PageTitle';

export default function PaymentCancelPage() {
  return (
    <PublicLayout>
      <PageTitle title="Payment Cancelled | Syrabit.ai" />
      <div className="min-h-[60vh] flex items-center justify-center px-4">
        <div className="max-w-md w-full text-center space-y-6">
          <div className="w-20 h-20 rounded-full mx-auto flex items-center justify-center"
            style={{ background: 'rgba(239,68,68,0.10)', border: '2px solid rgba(239,68,68,0.25)' }}>
            <XCircle size={40} className="text-red-400" />
          </div>

          <div className="space-y-2">
            <h1 className="text-2xl font-bold text-foreground">Payment Cancelled</h1>
            <p className="text-muted-foreground">
              No worries — you haven't been charged. You can try again anytime.
            </p>
          </div>

          <div className="flex flex-col sm:flex-row gap-3 justify-center">
            <Link
              to="/pricing"
              className="inline-flex items-center justify-center gap-2 px-6 py-3 rounded-xl text-sm font-semibold text-white transition-all hover:opacity-90"
              style={{ background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)' }}
            >
              <CreditCard size={16} /> View Plans
            </Link>
            <Link
              to="/chat"
              className="inline-flex items-center justify-center gap-2 px-6 py-3 rounded-xl text-sm font-medium text-muted-foreground border border-border hover:bg-accent/40 transition-colors"
            >
              <ArrowLeft size={16} /> Back to Chat
            </Link>
          </div>
        </div>
      </div>
    </PublicLayout>
  );
}
