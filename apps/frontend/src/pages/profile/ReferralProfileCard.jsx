import { ArrowRight, Megaphone } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

export default function ReferralProfileCard() {
  const navigate = useNavigate();
  return (
    <section
      className="rounded-2xl border p-5"
      style={{
        background: 'linear-gradient(135deg, rgba(16,185,129,.11), rgba(124,58,237,.08))',
        borderColor: 'rgba(16,185,129,.25)',
      }}
      data-testid="referral-profile-card"
    >
      <div className="flex items-start gap-3">
        <div className="rounded-xl bg-emerald-100 p-2.5 text-emerald-700">
          <Megaphone size={20} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-bold text-foreground">Campus Influencer Program</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            Apply for one of 100 reviewed places and earn from mature, verified weekly referrals.
          </p>
          <button
            type="button"
            onClick={() => navigate('/profile/referrals')}
            className="mt-4 inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-emerald-700"
          >
            View program <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </section>
  );
}