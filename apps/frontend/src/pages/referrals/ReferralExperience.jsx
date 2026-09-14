import { useCallback, useEffect, useState } from 'react';
import { AppLayout } from '@/components/layout/AppLayout';
import { PageTitle } from '@/components/PageTitle';
import { useAuth } from '@/context/AuthContext';
import {
  activateReferralApplication,
  getReferralExperience,
  getReferralStatements,
  submitReferralApplication,
} from '@/utils/api';
import { QRCodeSVG } from 'qrcode.react';
import { toast } from 'sonner';
import {
  ArrowUpRight, Check, Clipboard, Clock3, ExternalLink, Info, Link2,
  LockKeyhole, RefreshCw, ShieldCheck, UsersRound, WalletCards,
} from 'lucide-react';

const emptyForm = {
  institution: '', class_name: '', stream_name: '', age_eligible: false,
  guardian_consent_required: false, guardian_consent_confirmed: false,
  eligibility_acknowledged: false, conduct_acknowledged: false,
  privacy_consent: false, terms_version: '', privacy_version: '',
};

const unwrap = (response) => response?.data?.data || response?.data || {};
const money = (value) => `₹${Number(value || 0).toLocaleString('en-IN')}`;
const date = (value) => value ? new Date(Number(value) * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';

function Card({ children, className = '' }) {
  return <section className={`rounded-[1.35rem] border border-violet-100/80 bg-white/85 p-5 shadow-[0_12px_40px_rgba(72,53,130,0.07)] backdrop-blur ${className}`}>{children}</section>;
}

function StatusPill({ status }) {
  const styles = {
    approved: 'bg-emerald-50 text-emerald-700 border-emerald-200', active: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    pending: 'bg-amber-50 text-amber-700 border-amber-200', waitlisted: 'bg-sky-50 text-sky-700 border-sky-200',
    rejected: 'bg-rose-50 text-rose-700 border-rose-200', expired: 'bg-slate-100 text-slate-600 border-slate-200',
  };
  return <span className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-bold capitalize ${styles[status] || styles.pending}`}>{String(status || 'not started').replace('_', ' ')}</span>;
}

function ProgressBar({ value, label }) {
  return <div aria-label={label} className="h-2 overflow-hidden rounded-full bg-violet-100"><div className="h-full rounded-full bg-violet-600 transition-all duration-500" style={{ width: `${Math.min(100, Math.max(0, value || 0))}%` }} /></div>;
}

export default function ReferralExperience() {
  const { user } = useAuth();
  const [experience, setExperience] = useState(null);
  const [statements, setStatements] = useState([]);
  const [form, setForm] = useState(emptyForm);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const result = unwrap(await getReferralExperience());
      setExperience(result);
      try {
        const statementResult = unwrap(await getReferralStatements());
        setStatements(statementResult.statements || []);
      } catch {
        setStatements([]);
      }
      const policy = result.policy || {};
      setForm((current) => ({ ...current, terms_version: policy.version || '', privacy_version: policy.version || '' }));
    } catch {
      setError('We could not load your referral eligibility right now.');
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const policy = experience?.policy || {};
  const program = experience?.program || {};
  const application = experience?.application;
  const dashboard = experience?.dashboard;
  const isPaused = program.state === 'paused';
  const canApply = !application && !isPaused;
  const qualification = dashboard?.qualification;
  const currentWeek = dashboard?.current_week;
  const rewardCap = currentWeek?.reward_cap_inr;
  const qualificationPercent = qualification?.target ? (Number(qualification.mature_verified || 0) / Number(qualification.target)) * 100 : 0;

  const change = (name, value) => setForm((current) => ({ ...current, [name]: value }));
  const submit = async (event) => {
    event.preventDefault();
    if (!Object.entries(form).filter(([key]) => ['institution', 'class_name', 'stream_name'].includes(key)).every(([, value]) => String(value).trim())) {
      toast.error('Add your institution, class, and stream first.'); return;
    }
    setSaving(true);
    try {
      await submitReferralApplication({ ...form, institution: form.institution.trim(), class_name: form.class_name.trim(), stream_name: form.stream_name.trim(), idempotency_key: crypto.randomUUID() });
      await load(); toast.success('Application received. We will review it carefully.');
    } catch (requestError) { toast.error(requestError?.response?.data?.detail || 'Application could not be submitted.'); }
    finally { setSaving(false); }
  };
  const activate = async () => {
    setSaving(true);
    try { await activateReferralApplication(); await load(); toast.success('Referral access is now active.'); }
    catch (requestError) { toast.error(requestError?.response?.data?.detail || 'Activation could not be completed.'); }
    finally { setSaving(false); }
  };
  const copy = async () => {
    if (!dashboard?.referral?.link) return;
    await navigator.clipboard.writeText(dashboard.referral.link); setCopied(true); setTimeout(() => setCopied(false), 1800); toast.success('Referral link copied.');
  };
  const share = async () => {
    if (!dashboard?.referral?.link) return;
    if (navigator.share) await navigator.share({ title: 'Study with Syrabit', text: 'A focused AI study space for students.', url: dashboard.referral.link });
    else await copy();
  };

  if (loading) return <AppLayout pageTitle="Referrals"><div className="mx-auto max-w-5xl space-y-5 p-6 animate-pulse"><div className="h-40 rounded-[1.5rem] bg-violet-100/70" /><div className="grid gap-5 md:grid-cols-2"><div className="h-72 rounded-[1.5rem] bg-white/70" /><div className="h-72 rounded-[1.5rem] bg-white/70" /></div></div></AppLayout>;
  if (error) return <AppLayout pageTitle="Referrals"><div className="mx-auto max-w-lg p-10 text-center"><div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-rose-50 text-rose-600"><Info /></div><h1 className="text-xl font-bold text-slate-900">Referral details are unavailable</h1><p className="my-2 text-sm text-slate-500">{error}</p><button onClick={load} data-testid="button-retry-referrals" className="mt-4 inline-flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-bold text-white"><RefreshCw size={15} /> Try again</button></div></AppLayout>;

  return <AppLayout pageTitle="Referrals">
    <PageTitle title="Referrals | Syrabit.ai" />
    <main className="min-h-[100dvh] bg-[radial-gradient(circle_at_top_right,rgba(124,58,237,.12),transparent_34%),#f7f6fb] px-4 py-6 sm:px-6 lg:px-10" data-testid="referral-experience">
      <div className="mx-auto max-w-5xl space-y-5">
        <header className="relative overflow-hidden rounded-[1.6rem] bg-[#241b4b] p-6 text-white shadow-[0_20px_60px_rgba(36,27,75,.22)] sm:p-8">
          <div className="absolute -right-16 -top-20 h-56 w-56 rounded-full border border-violet-300/20" /><div className="absolute right-8 top-8 h-28 w-28 rounded-full border border-violet-300/20" />
          <p className="mb-3 text-[11px] font-bold uppercase tracking-[.22em] text-violet-200">Syrabit referral program</p>
          <div className="relative max-w-2xl"><h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Help a classmate study. Earn when their visit is verified.</h1><p className="mt-3 max-w-xl text-sm leading-6 text-violet-100/80">A transparent program for eligible students. No pressure to share, no public identity, and no promise until the server verifies a visit.</p></div>
          <div className="relative mt-6 flex flex-wrap gap-2 text-xs font-semibold text-violet-100"><span className="rounded-full bg-white/10 px-3 py-2">Policy {policy.version || 'current'}</span><span className="rounded-full bg-white/10 px-3 py-2">Server-verified earnings</span><span className="rounded-full bg-white/10 px-3 py-2">Program pauses are visible</span></div>
        </header>

        {isPaused && <div className="flex gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"><Clock3 className="mt-0.5 shrink-0" size={18} /><div><b>Referrals are paused{program.pause_effective_at ? ` since ${date(program.pause_effective_at)}` : ''}.</b><p className="mt-1 text-amber-800">Your progress is preserved and new earnings stop during the pause. We will show a new activation date here when the program resumes.</p></div></div>}

        {dashboard?.referral && <Card className="border-violet-200 bg-violet-50/70"><div className="flex flex-col gap-5 sm:flex-row sm:items-center"><div className="flex-1"><div className="mb-2 flex items-center gap-2 text-violet-700"><Link2 size={17} /><span className="text-xs font-bold uppercase tracking-widest">Your safe sharing kit</span></div><h2 className="text-lg font-bold text-slate-900">Share only when it feels useful</h2><p className="mt-1 text-sm text-slate-600">Your identity is not shown publicly. Avoid bulk messages or claims about guaranteed money.</p><div className="mt-4 flex flex-col gap-2 sm:flex-row"><code className="min-w-0 flex-1 truncate rounded-xl border border-violet-200 bg-white px-3 py-2.5 text-xs text-slate-600">{dashboard.referral.link}</code><button onClick={copy} data-testid="button-copy-referral-link" className="inline-flex items-center justify-center gap-2 rounded-xl border border-violet-200 bg-white px-4 py-2 text-xs font-bold text-violet-700">{copied ? <Check size={15} /> : <Clipboard size={15} />}{copied ? 'Copied' : 'Copy link'}</button><button onClick={share} data-testid="button-share-referral-link" className="inline-flex items-center justify-center gap-2 rounded-xl bg-violet-600 px-4 py-2 text-xs font-bold text-white"><ExternalLink size={15} /> Share</button></div></div><div className="hidden rounded-xl bg-white p-3 shadow-sm sm:block"><QRCodeSVG value={dashboard.referral.link} size={112} bgColor="#ffffff" fgColor="#241b4b" /></div></div></Card>}

        {statements.length > 0 && <Card data-testid="referral-statements"><div className="flex items-start justify-between gap-4"><div><p className="text-[11px] font-bold uppercase tracking-[.18em] text-violet-600">Settlement statements</p><h2 className="mt-1 text-lg font-bold text-slate-900">Verified activity, shown week by week</h2><p className="mt-1 text-sm text-slate-500">Statements remain held while quality, fraud, beneficiary, and staff review controls complete.</p></div><WalletCards className="text-violet-500" size={22} /></div><div className="mt-4 space-y-2">{statements.map((statement) => <div key={statement.id} className="flex flex-col gap-2 rounded-2xl border border-slate-100 bg-slate-50/80 p-3 sm:flex-row sm:items-center sm:justify-between"><div><div className="flex flex-wrap items-center gap-2"><b className="text-sm text-slate-800">{statement.tier === 'advanced' ? 'Advanced' : 'Basic'} week</b><StatusPill status={statement.status} /></div><p className="mt-1 text-xs text-slate-500">{statement.mature_verified_count || 0} mature verified visitors · {statement.payable_claim_count || 0} payable claims</p></div><strong className="text-sm text-slate-900">{money(statement.gross_amount_inr)}</strong></div>)}</div></Card>}

        {dashboard && <div className="grid gap-5 lg:grid-cols-[1.35fr_.65fr]"><Card><div className="flex items-center justify-between"><div><p className="text-xs font-bold uppercase tracking-widest text-violet-600">This week</p><h2 className="mt-1 text-xl font-bold text-slate-900">Verified earnings</h2></div><WalletCards className="text-violet-500" size={22} /></div><div className="mt-6 flex items-end justify-between"><div><p className="text-4xl font-bold tracking-tight text-[#241b4b]" data-testid="text-authoritative-reward">{money(currentWeek?.authoritative_reward_inr)}</p><p className="mt-1 text-xs text-slate-500">Authoritative reward · settles after verification</p></div>{rewardCap != null && <span className="rounded-xl bg-violet-50 px-3 py-2 text-xs font-bold text-violet-700">Cap {money(rewardCap)}</span>}</div><div className="mt-6 grid grid-cols-3 gap-3 border-t border-slate-100 pt-4 text-xs"><div><p className="text-slate-500">Mature verified</p><b className="text-slate-800">{currentWeek?.mature_verified ?? 0}</b></div><div><p className="text-slate-500">Pending</p><b className="text-amber-700">{currentWeek?.pending ?? 0}</b></div><div><p className="text-slate-500">Rejected</p><b className="text-slate-700">{currentWeek?.rejected ?? 0}</b></div></div></Card><Card><p className="text-xs font-bold uppercase tracking-widest text-violet-600">Advanced qualification</p><h2 className="mt-1 text-xl font-bold text-slate-900">Progress, not a promise</h2><p className="mt-3 text-sm text-slate-600">The server decides qualification and available positions.</p><div className="mt-6 flex items-end justify-between"><b className="text-2xl text-[#241b4b]">{qualification?.mature_verified ?? 0} <span className="text-sm font-medium text-slate-500">/ {qualification?.target ?? policy.advanced_target ?? 500}</span></b><span className="text-xs font-bold text-violet-600">{Math.round(qualificationPercent)}%</span></div><div className="mt-2"><ProgressBar value={qualificationPercent} label="Advanced qualification progress" /></div><p className="mt-4 text-xs leading-5 text-slate-500">{qualification?.remaining_advanced_positions ?? 0} positions remaining · {qualification?.status || 'Qualification is server-controlled'}</p></Card></div>}

        {application ? <Card><div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between"><div><p className="text-xs font-bold uppercase tracking-widest text-violet-600">Application {application.id ? `#${String(application.id).slice(0, 8)}` : ''}</p><h2 className="mt-1 text-xl font-bold text-slate-900">Your review status</h2><p className="mt-2 text-sm text-slate-600">Submitted {date(application.submitted_at)} · Identity {application.identity_status || 'pending'} · KYC {application.kyc_status || 'pending'}</p></div><StatusPill status={application.status} /></div>{application.decision_reason && <div className="mt-4 rounded-xl bg-slate-50 p-3 text-sm text-slate-600"><b className="text-slate-800">Reviewer note:</b> {application.decision_reason}</div>}{application.status === 'activation_required' && <button disabled={saving} onClick={activate} data-testid="button-activate-referral" className="mt-5 inline-flex items-center gap-2 rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-bold text-white disabled:opacity-50"><ArrowUpRight size={16} /> Activate referrals</button>}{application.activation_deadline_at && <p className="mt-3 text-xs text-slate-500">Activation available until {date(application.activation_deadline_at)}.</p>}</Card> : <Card><div className="mb-5"><p className="text-xs font-bold uppercase tracking-widest text-violet-600">Eligibility application</p><h2 className="mt-1 text-xl font-bold text-slate-900">Start with the facts</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">We ask for academic context so the program stays appropriate for students. We do not collect UPI or beneficiary details.</p></div><form onSubmit={submit} className="space-y-5"><div className="grid gap-4 sm:grid-cols-3">{[['institution','Institution'],['class_name','Class'],['stream_name','Stream']].map(([name,label]) => <label key={name} className="text-xs font-bold text-slate-700">{label}<input required value={form[name]} onChange={(e) => change(name, e.target.value)} data-testid={`input-${name}`} className="mt-2 h-11 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 text-sm font-normal outline-none transition focus:border-violet-500 focus:ring-4 focus:ring-violet-100" /></label>)}</div><div className="grid gap-3 rounded-2xl bg-violet-50/70 p-4 text-sm text-slate-700"><p className="font-bold text-violet-900">Please read before consenting</p><p>Basic referrals have a weekly cap of {money(policy.basic_weekly_cap_inr || 100)}. Advanced qualification needs {policy.advanced_target || 500} mature verified visitors; its {money(policy.advanced_weekly_cap_inr || 1000)} cap starts next week after review.</p><p>Only mature, verified visitors count. Settlement is delayed and verification holds can apply. A pause preserves progress and stops new earnings.</p><p>Do not spam, mislead, impersonate, buy traffic, or promise rewards. We do not publish your identity. You can appeal a decision through support.</p><label className="flex gap-3 pt-1"><input type="checkbox" checked={form.age_eligible} onChange={(e) => change('age_eligible', e.target.checked)} data-testid="checkbox-age-eligible" className="mt-0.5 accent-violet-600" /><span>I confirm I am age eligible for this program.</span></label><label className="flex gap-3"><input type="checkbox" checked={form.guardian_consent_required} onChange={(e) => change('guardian_consent_required', e.target.checked)} data-testid="checkbox-guardian-required" className="mt-0.5 accent-violet-600" /><span>Guardian consent is required for me.</span></label>{form.guardian_consent_required && <label className="flex gap-3 pl-6"><input type="checkbox" checked={form.guardian_consent_confirmed} onChange={(e) => change('guardian_consent_confirmed', e.target.checked)} data-testid="checkbox-guardian-confirmed" className="mt-0.5 accent-violet-600" /><span>My guardian has reviewed and confirmed this application.</span></label>}<label className="flex gap-3"><input type="checkbox" checked={form.eligibility_acknowledged} onChange={(e) => change('eligibility_acknowledged', e.target.checked)} data-testid="checkbox-eligibility" className="mt-0.5 accent-violet-600" /><span>I understand eligibility, review, capacity, and server-controlled qualification.</span></label><label className="flex gap-3"><input type="checkbox" checked={form.conduct_acknowledged} onChange={(e) => change('conduct_acknowledged', e.target.checked)} data-testid="checkbox-conduct" className="mt-0.5 accent-violet-600" /><span>I will follow the conduct rules and share responsibly.</span></label><label className="flex gap-3"><input type="checkbox" checked={form.privacy_consent} onChange={(e) => change('privacy_consent', e.target.checked)} data-testid="checkbox-privacy" className="mt-0.5 accent-violet-600" /><span>I consent to the privacy terms and understand no public identity is used.</span></label></div><button disabled={saving || isPaused} data-testid="button-submit-referral-application" className="inline-flex items-center gap-2 rounded-xl bg-violet-600 px-5 py-3 text-sm font-bold text-white shadow-lg shadow-violet-200 transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"><ShieldCheck size={17} />{saving ? 'Sending…' : 'Submit for review'}</button></form></Card>}

        <Card className="border-slate-200 bg-slate-50/80"><div className="flex gap-3"><LockKeyhole size={19} className="mt-0.5 shrink-0 text-slate-500" /><div><h2 className="font-bold text-slate-800">Trust and settlement notes</h2><p className="mt-1 text-sm leading-6 text-slate-600">Reward figures, rank, waitlist, capacity, and qualification come from Syrabit’s server. They can change while verification completes. This page never asks for payment account details.</p></div></div></Card>
      </div>
    </main>
  </AppLayout>;
}