import { useCallback, useEffect, useState } from 'react';
import { canStaffCapability } from '@/utils/staffAccess';
import { useAuth } from '@/context/AuthContext';
import {
  adminExpireReferralApplication,
  adminGetReferralApplicationAudits,
  adminGetReferralApplications,
  adminReviewReferralApplication,
} from '@/utils/api';
import { toast } from 'sonner';
import {
  CheckCircle2,
  ChevronDown,
  Clock3,
  FileClock,
  LockKeyhole,
  RefreshCw,
  Search,
  ShieldCheck,
  XCircle,
} from 'lucide-react';

const unwrap = (response) => response?.data?.data || response?.data || {};
const date = (value) => (
  value
    ? new Date(Number(value) * 1000).toLocaleString('en-IN', {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
    : '—'
);

function Badge({ children, tone = 'slate' }) {
  const colors = {
    slate: 'bg-slate-100 text-slate-600',
    amber: 'bg-amber-50 text-amber-700',
    green: 'bg-emerald-50 text-emerald-700',
    red: 'bg-rose-50 text-rose-700',
    blue: 'bg-sky-50 text-sky-700',
  };

  return (
    <span className={`rounded-full px-2 py-1 text-[10px] font-bold uppercase tracking-wide ${colors[tone]}`}>
      {children}
    </span>
  );
}

export default function ReferralAdmissions({ adminToken }) {
  const { user } = useAuth();
  const allowed = canStaffCapability(user, 'referral:review');
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState('submitted');
  const [selected, setSelected] = useState(null);
  const [reason, setReason] = useState('');
  const [identity, setIdentity] = useState(false);
  const [kyc, setKyc] = useState(false);
  const [audits, setAudits] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = unwrap(await adminGetReferralApplications(adminToken, status));
      setItems(result.applications || result.items || (Array.isArray(result) ? result : []));
    } catch {
      setError('The admissions queue could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [adminToken, status]);

  useEffect(() => {
    if (allowed) load();
  }, [allowed, load]);

  const select = (item) => {
    setSelected(item);
    setReason('');
    setIdentity(item.identity_status === 'verified');
    setKyc(item.kyc_status === 'verified');
    setAudits([]);
  };

  const review = async (decision) => {
    if (!selected) return;
    if (['approve', 'approved'].includes(decision) && (!identity || !kyc)) {
      toast.error('Approval requires explicit identity and KYC evidence.');
      return;
    }
    if (!reason.trim() && ['reject', 'waitlist', 'suspend', 'close'].includes(decision)) {
      toast.error('Add an auditable reason first.');
      return;
    }

    try {
      await adminReviewReferralApplication(adminToken, selected.id, {
        decision,
        reason: reason.trim(),
        identity_verified: identity,
        kyc_verified: kyc,
      });
      toast.success(`Application ${decision}d.`);
      setSelected(null);
      load();
    } catch (requestError) {
      toast.error(requestError?.response?.data?.detail || 'Decision could not be saved.');
    }
  };

  const expire = async () => {
    if (!selected || !reason.trim()) {
      toast.error('Add an expiry reason first.');
      return;
    }

    try {
      await adminExpireReferralApplication(adminToken, selected.id, reason.trim());
      toast.success('Activation expired.');
      setSelected(null);
      load();
    } catch {
      toast.error('Activation expiry could not be saved.');
    }
  };

  const showAudits = async () => {
    try {
      const result = unwrap(await adminGetReferralApplicationAudits(adminToken, selected.id));
      setAudits(result.audits || result.items || (Array.isArray(result) ? result : []));
    } catch {
      toast.error('Audit history unavailable.');
    }
  };

  if (!allowed) {
    return (
      <div className="flex min-h-[55vh] items-center justify-center">
        <div className="max-w-sm rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-sm">
          <LockKeyhole className="mx-auto mb-4 text-slate-400" size={28} />
          <h1 className="text-lg font-bold text-slate-900">Referral admissions is restricted</h1>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            Your staff account does not have the referral review capability.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-full bg-[#f7f8fc] p-1" data-testid="referral-admissions">
      <div className="mx-auto max-w-[1400px]">
        <header className="mb-5 flex flex-col gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="flex items-center gap-2 text-violet-600">
              <ShieldCheck size={18} />
              <span className="text-[11px] font-bold uppercase tracking-[.2em]">Capability protected</span>
            </div>
            <h1 className="mt-1 text-2xl font-bold tracking-tight text-slate-900">Referral admissions</h1>
            <p className="mt-1 text-sm text-slate-500">
              Review evidence, record a reason, and leave an auditable trail.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              data-testid="select-referral-status"
              className="h-10 rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 outline-none"
            >
              <option value="submitted">Submitted</option>
              <option value="under_review">Under review</option>
              <option value="activation_required">Activation required</option>
              <option value="active">Active</option>
              <option value="waitlisted">Waitlisted</option>
              <option value="rejected">Rejected</option>
              <option value="suspended">Suspended</option>
              <option value="closed">Closed</option>
            </select>
            <button
              onClick={load}
              data-testid="button-refresh-admissions"
              className="inline-flex h-10 items-center gap-2 rounded-xl border border-slate-200 px-3 text-sm font-bold text-slate-600 hover:bg-slate-50"
            >
              <RefreshCw size={15} /> Refresh
            </button>
          </div>
        </header>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_440px]">
          <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-100 px-5 py-4">
              <div className="flex items-center justify-between">
                <h2 className="font-bold text-slate-900">
                  Queue <span className="ml-1 text-slate-400">({items.length})</span>
                </h2>
                <Search size={17} className="text-slate-400" />
              </div>
            </div>
            {loading ? (
              <div className="space-y-3 p-5">
                {[1, 2, 3, 4].map((number) => (
                  <div key={number} className="h-20 animate-pulse rounded-xl bg-slate-100" />
                ))}
              </div>
            ) : error ? (
              <div className="p-8 text-center text-sm text-rose-600">{error}</div>
            ) : items.length === 0 ? (
              <div className="p-12 text-center">
                <CheckCircle2 className="mx-auto mb-3 text-emerald-500" size={25} />
                <p className="font-bold text-slate-800">No applications in this view</p>
                <p className="mt-1 text-sm text-slate-500">A clear queue is good operational hygiene.</p>
              </div>
            ) : (
              <div className="divide-y divide-slate-100">
                {items.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => select(item)}
                    data-testid={`button-open-application-${item.id}`}
                    className={`w-full px-5 py-4 text-left transition hover:bg-violet-50/50 ${selected?.id === item.id ? 'bg-violet-50' : ''}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-bold text-slate-900">
                          {item.institution || 'Institution not provided'}
                          <span className="font-normal text-slate-400">
                            {' · '}{item.class_name || 'Class —'}
                          </span>
                        </p>
                        <p className="mt-1 truncate text-xs text-slate-500">
                          {item.user_id || 'User ID unavailable'} · submitted {date(item.submitted_at)}
                        </p>
                      </div>
                      <Badge tone={item.status === 'submitted' ? 'amber' : item.status === 'active' ? 'green' : 'slate'}>
                        {item.status}
                      </Badge>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Badge tone={item.identity_status === 'verified' ? 'green' : 'amber'}>
                        Identity {item.identity_status || 'pending'}
                      </Badge>
                      <Badge tone={item.kyc_status === 'verified' ? 'green' : 'amber'}>
                        KYC {item.kyc_status || 'pending'}
                      </Badge>
                      {item.influencer_slot && <Badge tone="blue">Influencer slot</Badge>}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>

          <aside className="rounded-2xl border border-slate-200 bg-white shadow-sm">
            {!selected ? (
              <div className="flex min-h-[480px] flex-col items-center justify-center p-8 text-center">
                <FileClock className="mb-3 text-slate-300" size={30} />
                <h2 className="font-bold text-slate-800">Select an application</h2>
                <p className="mt-1 max-w-xs text-sm leading-6 text-slate-500">
                  Evidence and decision controls will appear here. Every non-approval needs a reason.
                </p>
              </div>
            ) : (
              <div className="p-5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-[10px] font-bold uppercase tracking-widest text-violet-600">Application detail</p>
                    <h2 className="mt-1 text-lg font-bold text-slate-900">
                      {selected.institution || 'Unknown institution'}
                    </h2>
                    <p className="text-xs text-slate-500">
                      {selected.class_name} · {selected.stream_name} · {selected.user_id}
                    </p>
                  </div>
                  <button
                    onClick={() => setSelected(null)}
                    data-testid="button-close-review"
                    className="text-slate-400 hover:text-slate-700"
                  >
                    <XCircle size={19} />
                  </button>
                </div>

                <div className="mt-5 grid grid-cols-2 gap-3 text-xs">
                  <div className="rounded-xl bg-slate-50 p-3">
                    <p className="text-slate-500">Academic snapshot</p>
                    <b className="mt-1 block text-slate-800">
                      {selected.academic_snapshot ? JSON.stringify(selected.academic_snapshot) : 'Not attached'}
                    </b>
                  </div>
                  <div className="rounded-xl bg-slate-50 p-3">
                    <p className="text-slate-500">Contact snapshot</p>
                    <b className="mt-1 block break-words text-slate-800">
                      {selected.contact_snapshot ? JSON.stringify(selected.contact_snapshot) : 'Not attached'}
                    </b>
                  </div>
                </div>

                <div className="mt-5 space-y-3 rounded-xl border border-amber-200 bg-amber-50/60 p-4">
                  <p className="text-xs font-bold text-amber-900">Evidence gate for approval</p>
                  <label className="flex gap-3 text-sm text-amber-950">
                    <input
                      type="checkbox"
                      checked={identity}
                      onChange={(event) => setIdentity(event.target.checked)}
                      data-testid="checkbox-identity-evidence"
                      className="mt-0.5 accent-violet-600"
                    />
                    Explicit identity evidence checked
                  </label>
                  <label className="flex gap-3 text-sm text-amber-950">
                    <input
                      type="checkbox"
                      checked={kyc}
                      onChange={(event) => setKyc(event.target.checked)}
                      data-testid="checkbox-kyc-evidence"
                      className="mt-0.5 accent-violet-600"
                    />
                    Explicit KYC evidence checked
                  </label>
                </div>

                <label className="mt-5 block text-xs font-bold text-slate-700">
                  Decision reason
                  <textarea
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    data-testid="textarea-review-reason"
                    rows="3"
                    placeholder="Write what was checked or why this decision was made…"
                    className="mt-2 w-full resize-none rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm font-normal outline-none focus:border-violet-500 focus:ring-4 focus:ring-violet-100"
                  />
                </label>

                <div className="mt-4 grid grid-cols-2 gap-2">
                  <button onClick={() => review('approve')} data-testid="button-approve-referral" className="rounded-xl bg-emerald-600 px-3 py-2.5 text-xs font-bold text-white">Approve</button>
                  <button onClick={() => review('waitlist')} data-testid="button-waitlist-referral" className="rounded-xl bg-sky-600 px-3 py-2.5 text-xs font-bold text-white">Waitlist</button>
                  <button onClick={() => review('reject')} data-testid="button-reject-referral" className="rounded-xl bg-rose-600 px-3 py-2.5 text-xs font-bold text-white">Reject</button>
                  <button onClick={() => review('suspend')} data-testid="button-suspend-referral" className="rounded-xl border border-slate-200 px-3 py-2.5 text-xs font-bold text-slate-700">Suspend</button>
                  <button onClick={() => review('close')} data-testid="button-close-referral" className="rounded-xl border border-slate-200 px-3 py-2.5 text-xs font-bold text-slate-700">Close</button>
                  <button onClick={expire} data-testid="button-expire-referral" className="inline-flex items-center justify-center gap-1 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs font-bold text-amber-800"><Clock3 size={13} />Expire activation</button>
                </div>

                <button onClick={showAudits} data-testid="button-view-referral-audits" className="mt-4 inline-flex items-center gap-2 text-xs font-bold text-violet-700">
                  View audit history <ChevronDown size={14} />
                </button>
                {audits.length > 0 && (
                  <div className="mt-3 max-h-40 overflow-auto rounded-xl bg-slate-50 p-3 text-xs">
                    {audits.map((audit, index) => (
                      <div key={audit.id || index} className="border-b border-slate-200 py-2 last:border-0">
                        <b>{audit.action || audit.event || 'Decision'}</b>
                        <span className="ml-2 text-slate-500">{date(audit.occurred_at)}</span>
                        <p className="mt-1 text-slate-500">{audit.reason || 'No reason recorded'}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}