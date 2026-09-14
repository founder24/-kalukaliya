import { useCallback, useEffect, useMemo, useState } from 'react';
import { canStaffCapability } from '@/utils/staffAccess';
import { useAuth } from '@/context/AuthContext';
import {
  adminApproveReferralStatement,
  adminGetReferralBeneficiaries,
  adminGetReferralSettlements,
  adminRecordReferralBeneficiary,
  adminRecordReferralPayout,
  adminReviewReferralBeneficiary,
  adminSettleReferralWeek,
  adminTransitionReferralPayout,
  adminUploadReferralReceipt,
} from '@/utils/api';
import { toast } from 'sonner';
import { CheckCircle2, FileUp, LockKeyhole, RefreshCw, ShieldCheck, WalletCards } from 'lucide-react';

const unwrap = (response) => response?.data?.data || response?.data || {};
const money = (value) => `₹${Number(value || 0).toLocaleString('en-IN')}`;
const date = (value) => value
  ? new Date(Number(value) * 1000).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
  : '—';

function Badge({ children, tone = 'slate' }) {
  const colors = {
    slate: 'bg-slate-100 text-slate-600',
    amber: 'bg-amber-50 text-amber-700',
    green: 'bg-emerald-50 text-emerald-700',
    red: 'bg-rose-50 text-rose-700',
    blue: 'bg-sky-50 text-sky-700',
  };
  return <span className={`rounded-full px-2 py-1 text-[10px] font-bold uppercase tracking-wide ${colors[tone]}`}>{children}</span>;
}

function tone(status) {
  if (['paid', 'approved'].includes(status)) return 'green';
  if (['failed', 'reversed', 'clawed_back'].includes(status)) return 'red';
  if (status === 'held') return 'amber';
  return 'blue';
}

export default function ReferralSettlements({ adminToken }) {
  const { user } = useAuth();
  const allowed = canStaffCapability(user, 'referral:settle');
  const [statements, setStatements] = useState([]);
  const [beneficiaries, setBeneficiaries] = useState([]);
  const [selected, setSelected] = useState(null);
  const [weekId, setWeekId] = useState('');
  const [weekReason, setWeekReason] = useState('Weekly mature-claim settlement after quality and fraud review.');
  const [reason, setReason] = useState('');
  const [payoutStatus, setPayoutStatus] = useState('paid');
  const [utr, setUtr] = useState('');
  const [beneficiaryUserId, setBeneficiaryUserId] = useState('');
  const [beneficiaryDetails, setBeneficiaryDetails] = useState('{"name":"","upi_or_bank_reference":""}');
  const [payoutId, setPayoutId] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const result = unwrap(await adminGetReferralSettlements(adminToken));
      setStatements(result.statements || []);
      const beneficiaryResult = unwrap(await adminGetReferralBeneficiaries(adminToken));
      setBeneficiaries(beneficiaryResult.beneficiaries || []);
    } catch {
      setError('Settlement statements could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { if (allowed) load(); }, [allowed, load]);

  const totals = useMemo(() => statements.reduce((sum, row) => sum + Number(row.gross_amount_inr || 0), 0), [statements]);
  const selectedPayoutStatement = selected && selected.status === 'approved';

  if (!allowed) {
    return <div className="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-sm text-amber-900" data-testid="referral-settlement-forbidden"><LockKeyhole className="mb-2 text-amber-700" size={20} /><b>Settlement controls are restricted.</b><p className="mt-1">The referral:settle capability is required. Admissions review access does not grant payment authority.</p></div>;
  }

  const settleWeek = async () => {
    if (!weekId.trim() || weekReason.trim().length < 8) {
      toast.error('Enter a week ID and an auditable settlement reason.');
      return;
    }
    setSaving(true);
    try {
      await adminSettleReferralWeek(adminToken, weekId.trim(), { reason: weekReason.trim() });
      toast.success('Week calculated and placed on hold.');
      await load();
    } catch (requestError) {
      toast.error(requestError?.response?.data?.detail || 'Week settlement could not be calculated.');
    } finally { setSaving(false); }
  };

  const approve = async () => {
    if (!selected || reason.trim().length < 8) {
      toast.error('Add an approval reason first.');
      return;
    }
    setSaving(true);
    try {
      await adminApproveReferralStatement(adminToken, selected.id, { reason: reason.trim() });
      toast.success('Statement approved after beneficiary verification.');
      setSelected(null); setReason(''); await load();
    } catch (requestError) {
      toast.error(requestError?.response?.data?.detail || 'Statement approval failed.');
    } finally { setSaving(false); }
  };

  const recordPayout = async () => {
    if (!selected || reason.trim().length < 8) {
      toast.error('Add a payout reason first.');
      return;
    }
    setSaving(true);
    try {
      const result = await adminRecordReferralPayout(adminToken, selected.id, {
        status: payoutStatus,
        idempotency_key: crypto.randomUUID(),
        utr_reference: utr.trim() || undefined,
        reason: reason.trim(),
      });
      setPayoutId(result?.data?.payoutId || result?.data?.payout_id || '');
      toast.success(`Payout marked ${payoutStatus}.`);
      setSelected(null); setReason(''); await load();
    } catch (requestError) {
      toast.error(requestError?.response?.data?.detail || 'Payout could not be recorded.');
    } finally { setSaving(false); }
  };

  const saveBeneficiary = async () => {
    try {
      const details = JSON.parse(beneficiaryDetails);
      await adminRecordReferralBeneficiary(adminToken, { user_id: beneficiaryUserId.trim(), details });
      toast.success('Beneficiary details saved for independent review.');
      setBeneficiaryUserId('');
    } catch (requestError) {
      toast.error(requestError?.response?.data?.detail || 'Beneficiary details must be valid JSON.');
    }
  };

  const reviewBeneficiary = async (beneficiary, approved) => {
    const reviewReason = window.prompt(`Reason for ${approved ? 'verifying' : 'rejecting'} this beneficiary:`);
    if (!reviewReason || reviewReason.trim().length < 8) return;
    try {
      await adminReviewReferralBeneficiary(adminToken, beneficiary.id, {
        approved,
        reason: reviewReason.trim(),
      });
      toast.success(`Beneficiary ${approved ? 'verified' : 'rejected'}.`);
      await load();
    } catch (requestError) {
      toast.error(requestError?.response?.data?.detail || 'Beneficiary review failed.');
    }
  };

  const transition = async (status) => {
    if (!payoutId || reason.trim().length < 8) {
      toast.error('Enter a payout ID and reason.');
      return;
    }
    try {
      await adminTransitionReferralPayout(adminToken, payoutId, { status, reason: reason.trim() });
      toast.success(`Payout marked ${status}.`);
      setReason(''); await load();
    } catch (requestError) {
      toast.error(requestError?.response?.data?.detail || 'Payout transition failed.');
    }
  };

  const uploadReceipt = async (event) => {
    const file = event.target.files?.[0];
    if (!file || !payoutId) return;
    try {
      await adminUploadReferralReceipt(adminToken, payoutId, file);
      toast.success('Private receipt metadata stored.');
    } catch (requestError) {
      toast.error(requestError?.response?.data?.detail || 'Receipt upload failed.');
    } finally { event.target.value = ''; }
  };

  return <div className="space-y-5" data-testid="referral-settlements">
    <div className="rounded-2xl bg-[#241b4b] p-6 text-white shadow-[0_20px_60px_rgba(36,27,75,.15)]">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div><p className="text-[11px] font-bold uppercase tracking-[.2em] text-violet-200">Referral settlement controls</p><h1 className="mt-2 text-2xl font-bold">Review verified claims. Release nothing automatically.</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-violet-100/80">D1 statements are calculated from mature verified claims only. Staff must verify the beneficiary, approve the statement, record payment evidence, and retain a private receipt.</p></div>
        <WalletCards className="shrink-0 text-violet-200" size={28} />
      </div>
      <div className="mt-5 grid gap-3 sm:grid-cols-3"><div className="rounded-xl bg-white/10 p-3"><p className="text-xs text-violet-200">Statements</p><b className="text-xl">{statements.length}</b></div><div className="rounded-xl bg-white/10 p-3"><p className="text-xs text-violet-200">Calculated exposure</p><b className="text-xl">{money(totals)}</b></div><div className="rounded-xl bg-white/10 p-3"><p className="text-xs text-violet-200">Policy ceiling</p><b className="text-xl">₹37,000</b></div></div>
    </div>

    <section className="rounded-2xl border border-violet-100 bg-white p-5 shadow-sm"><div className="flex items-center gap-2"><ShieldCheck className="text-violet-600" size={19} /><h2 className="font-bold text-slate-900">Calculate a weekly statement</h2></div><div className="mt-4 grid gap-3 md:grid-cols-[1fr_2fr_auto]"><input value={weekId} onChange={(event) => setWeekId(event.target.value)} placeholder="Week ID" data-testid="input-settlement-week-id" className="h-11 rounded-xl border border-slate-200 bg-slate-50 px-3 text-sm outline-none focus:border-violet-500" /><input value={weekReason} onChange={(event) => setWeekReason(event.target.value)} data-testid="input-settlement-week-reason" className="h-11 rounded-xl border border-slate-200 bg-slate-50 px-3 text-sm outline-none focus:border-violet-500" /><button disabled={saving} onClick={settleWeek} className="inline-flex items-center justify-center gap-2 rounded-xl bg-violet-600 px-4 py-2 text-sm font-bold text-white disabled:opacity-50"><CheckCircle2 size={15} />Calculate</button></div></section>

    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><div className="flex items-center justify-between gap-3"><div><h2 className="font-bold text-slate-900">Statement queue</h2><p className="mt-1 text-xs text-slate-500">Tier, claim count, cap, hold, and payment states come from the server.</p></div><button onClick={load} className="inline-flex items-center gap-2 rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold text-slate-700"><RefreshCw size={14} />Refresh</button></div>{error && <p className="mt-4 rounded-xl bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}{loading ? <p className="mt-5 text-sm text-slate-500">Loading settlement statements…</p> : statements.length === 0 ? <p className="mt-5 rounded-xl bg-slate-50 p-4 text-sm text-slate-500">No statements calculated yet.</p> : <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[720px] text-left text-sm"><thead><tr className="border-b border-slate-100 text-[11px] uppercase tracking-wide text-slate-400"><th className="px-3 py-2">Week / slot</th><th className="px-3 py-2">Tier</th><th className="px-3 py-2">Verified</th><th className="px-3 py-2">Amount</th><th className="px-3 py-2">State</th><th className="px-3 py-2" /></tr></thead><tbody>{statements.map((row) => <tr key={row.id} className="border-b border-slate-50"><td className="px-3 py-3"><b>{row.week_id}</b><p className="text-xs text-slate-400">Slot {row.influencer_slot}</p></td><td className="px-3 py-3 capitalize">{row.tier}{row.qualifying_week ? ' · qualifying' : ''}</td><td className="px-3 py-3">{row.mature_verified_count} / {row.payable_claim_count}</td><td className="px-3 py-3 font-bold">{money(row.gross_amount_inr)}</td><td className="px-3 py-3"><Badge tone={tone(row.status)}>{row.status}</Badge><p className="mt-1 text-[11px] text-slate-400">{date(row.approved_at || row.quality_hold_released_at)}</p></td><td className="px-3 py-3"><button onClick={() => { setSelected(row); setReason(''); }} className="rounded-lg border border-violet-200 px-3 py-2 text-xs font-bold text-violet-700">Review</button></td></tr>)}</tbody></table></div>}</section>

    <div className="grid gap-5 lg:grid-cols-2">
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="font-bold text-slate-900">Record beneficiary</h2><p className="mt-1 text-xs text-slate-500">Private details are stored as a snapshot. A different staff member must verify them.</p><div className="mt-4 space-y-3"><input value={beneficiaryUserId} onChange={(event) => setBeneficiaryUserId(event.target.value)} placeholder="Student user ID" className="h-11 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 text-sm outline-none focus:border-violet-500" /><textarea value={beneficiaryDetails} onChange={(event) => setBeneficiaryDetails(event.target.value)} rows="4" className="w-full rounded-xl border border-slate-200 bg-slate-50 p-3 font-mono text-xs outline-none focus:border-violet-500" /><button onClick={saveBeneficiary} className="rounded-xl bg-slate-900 px-4 py-2.5 text-xs font-bold text-white">Save private details</button></div>{beneficiaries.length > 0 && <div className="mt-5 space-y-2 border-t border-slate-100 pt-4"><p className="text-xs font-bold uppercase tracking-wide text-slate-400">Review queue</p>{beneficiaries.map((beneficiary) => <div key={beneficiary.id} className="rounded-xl bg-slate-50 p-3"><div className="flex items-center justify-between gap-2 text-xs"><b>{beneficiary.user_id}</b><Badge tone={beneficiary.status === 'verified' ? 'green' : beneficiary.status === 'rejected' ? 'red' : 'amber'}>{beneficiary.status}</Badge></div>{beneficiary.status === 'pending' && <div className="mt-2 flex gap-2"><button onClick={() => reviewBeneficiary(beneficiary, true)} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-[11px] font-bold text-white">Verify</button><button onClick={() => reviewBeneficiary(beneficiary, false)} className="rounded-lg border border-rose-200 px-3 py-1.5 text-[11px] font-bold text-rose-700">Reject</button></div>}</div>)}</div>}</section>
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="font-bold text-slate-900">Payout evidence and correction</h2><p className="mt-1 text-xs text-slate-500">Receipt objects stay private in R2. No public URL is returned.</p><div className="mt-4 space-y-3"><input value={payoutId} onChange={(event) => setPayoutId(event.target.value)} placeholder="Payout ID" className="h-11 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 text-sm outline-none focus:border-violet-500" /><input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Auditable correction reason" className="h-11 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 text-sm outline-none focus:border-violet-500" /><div className="flex flex-wrap gap-2"><button onClick={() => transition('corrected')} className="rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold">Correct</button><button onClick={() => transition('reversed')} className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700">Reverse</button><button onClick={() => transition('clawed_back')} className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700">Claw back</button><label className="inline-flex cursor-pointer items-center gap-2 rounded-xl border border-violet-200 px-3 py-2 text-xs font-bold text-violet-700"><FileUp size={14} /> Upload private receipt<input type="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg" onChange={uploadReceipt} className="hidden" /></label></div></div></section>
    </div>

    {selected && <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/40 p-4 sm:items-center"><div className="w-full max-w-lg rounded-2xl bg-white p-5 shadow-2xl"><div className="flex items-start justify-between gap-4"><div><p className="text-[11px] font-bold uppercase tracking-wide text-violet-600">Statement review</p><h2 className="mt-1 text-lg font-bold text-slate-900">{selected.week_id} · slot {selected.influencer_slot}</h2><p className="mt-1 text-sm text-slate-500">{selected.tier} · {selected.mature_verified_count} mature verified · {money(selected.gross_amount_inr)}</p></div><Badge tone={tone(selected.status)}>{selected.status}</Badge></div><textarea value={reason} onChange={(event) => setReason(event.target.value)} rows="3" placeholder="Approval or payout reason" className="mt-5 w-full rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm outline-none focus:border-violet-500" />{selected.status === 'held' && <button disabled={saving} onClick={approve} className="mt-3 w-full rounded-xl bg-emerald-600 px-4 py-3 text-sm font-bold text-white disabled:opacity-50">Approve after beneficiary review</button>}{selectedPayoutStatement && <div className="mt-3 space-y-3 rounded-xl bg-violet-50 p-3"><select value={payoutStatus} onChange={(event) => setPayoutStatus(event.target.value)} className="h-10 w-full rounded-lg border border-violet-200 bg-white px-3 text-sm"><option value="paid">Paid</option><option value="failed">Failed / retry later</option></select><input value={utr} onChange={(event) => setUtr(event.target.value)} placeholder="UTR/reference (required when paid)" className="h-10 w-full rounded-lg border border-violet-200 bg-white px-3 text-sm" /><button disabled={saving} onClick={recordPayout} className="w-full rounded-lg bg-violet-600 px-3 py-2.5 text-xs font-bold text-white disabled:opacity-50">Record payout evidence</button></div>}<button onClick={() => setSelected(null)} className="mt-4 w-full rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-bold text-slate-700">Close</button></div></div>}
  </div>;
}