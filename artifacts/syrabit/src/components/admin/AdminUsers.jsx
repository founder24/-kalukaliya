import { useState, useEffect, useCallback } from 'react';
import { Loader2, Search, Ban, CheckCircle, Crown, ChevronDown, AlertTriangle, RefreshCw, TrendingDown, Activity, CreditCard, Plus, Minus, X, GraduationCap, Trophy } from 'lucide-react';
import AdminQuickLinks from './AdminQuickLinks';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { adminGetUsers, adminUpdateUserStatus, adminUpdateUserPlan, adminUpdateUserRole, churnRisk, adminUpdateUserCredits, adminGetQuizQuota, adminResetQuizQuota } from '@/utils/api';
import { toast } from 'sonner';
import { useSyraSelection, useSyraFilters } from '@/components/admin/syra/SyraContext';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
const PLAN_COLORS = {
  free: 'bg-gray-100 text-gray-500',
  starter: 'bg-violet-50 text-violet-600',
  pro: 'bg-amber-50 text-amber-600',
};

const STATUS_COLORS = {
  active: 'bg-emerald-50 text-emerald-600',
  suspended: 'bg-orange-50 text-orange-600',
  banned: 'bg-red-50 text-red-600',
};

const ROLE_COLORS = {
  student: 'bg-gray-100 text-gray-500',
  educator: 'bg-sky-50 text-sky-600',
  admin: 'bg-violet-50 text-violet-600',
};

const RISK_COLORS = {
  high:   { text: '#ef4444', bg: '#fef2f2', border: '#fecaca', label: 'High' },
  medium: { text: '#f59e0b', bg: '#fffbeb', border: '#fde68a', label: 'Medium' },
  low:    { text: '#10b981', bg: '#ecfdf5', border: '#a7f3d0', label: 'Low' },
};

function RiskBadge({ risk, score }) {
  const c = RISK_COLORS[risk] || RISK_COLORS.low;
  return (
    <span title={`Risk score: ${score}`} style={{ background: c.bg, border: `1px solid ${c.border}`, color: c.text, borderRadius: 20, padding: '2px 8px', fontSize: 11, fontWeight: 700 }}>
      {c.label}
    </span>
  );
}

function CreditsModal({ user, adminToken, onClose, onUpdated }) {
  const [mode, setMode] = useState('add');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (mode !== 'reset') {
      const n = parseInt(amount, 10);
      if (!n || n <= 0) { toast.error('Enter a valid positive number'); return; }
    }
    setSaving(true);
    try {
      const n = mode !== 'reset' ? (parseInt(amount, 10) || 0) : 0;
      const data = { action: mode, ...(mode !== 'reset' && { amount: n }), reason: reason.trim() || undefined };
      await adminUpdateUserCredits(adminToken, user.id, data);
      toast.success(`Credits ${mode === 'add' ? 'added' : mode === 'reset' ? 'reset' : 'deducted'} for ${user.name || user.email}`);
      onUpdated();
      onClose();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to update credits');
    } finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="w-full max-w-sm mx-4 rounded-2xl p-6 shadow-xl bg-white border border-gray-200">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-gray-900 font-semibold text-sm">Credits Management</h3>
            <p className="text-gray-400 text-xs mt-0.5">{user.name || user.email}</p>
            <p className="text-gray-500 text-xs">Today: {user.credits_used || 0} used (daily reset)</p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <X size={16} />
          </button>
        </div>

        <div className="flex gap-1 mb-4 p-1 rounded-xl bg-gray-100">
          {[
            { id: 'add', label: 'Add Credits', icon: Plus },
            { id: 'deduct', label: 'Deduct', icon: Minus },
            { id: 'reset', label: 'Reset to 0', icon: RefreshCw },
          ].map(({ id, label, icon: Icon }) => (
            <button key={id} onClick={() => setMode(id)}
              className={`flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg text-xs font-medium transition-all ${
                mode === id ? 'text-white bg-violet-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'
              }`}>
              <Icon size={10} /> {label}
            </button>
          ))}
        </div>

        {mode === 'add' && <p className="text-xs text-gray-400 mb-3">Restores daily credits — reduces today's usage count.</p>}
        {mode === 'deduct' && <p className="text-xs text-gray-400 mb-3">Marks credits as consumed — increases today's usage count.</p>}
        {mode === 'reset' && <p className="text-xs text-gray-400 mb-3">Resets today's usage to 0 — restores full daily allowance.</p>}

        {mode !== 'reset' && (
          <div className="mb-3">
            <label className="text-xs text-gray-500 mb-1 block">Amount</label>
            <input
              type="number"
              min="1"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="e.g. 100"
              className="w-full rounded-xl px-3 py-2 text-sm text-gray-900 border border-gray-200 bg-gray-50 focus:bg-white focus:ring-2 focus:ring-violet-500/20 focus:border-violet-400 outline-none"
            />
          </div>
        )}

        <div className="mb-4">
          <label className="text-xs text-gray-500 mb-1 block">Reason (optional)</label>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Compensation, promo..."
            className="w-full rounded-xl px-3 py-2 text-sm text-gray-900 border border-gray-200 bg-gray-50 focus:bg-white focus:ring-2 focus:ring-violet-500/20 focus:border-violet-400 outline-none"
          />
        </div>

        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 py-2 rounded-xl text-sm text-gray-500 transition-colors hover:text-gray-700 bg-gray-100 border border-gray-200">
            Cancel
          </button>
          <button onClick={handleSave} disabled={saving || (mode !== 'reset' && (!amount || parseInt(amount, 10) <= 0))}
            className="flex-1 py-2 rounded-xl text-sm text-white disabled:opacity-50 flex items-center justify-center gap-1.5 transition-all hover:opacity-90 bg-violet-600 shadow-sm">
            {saving && <Loader2 size={12} className="animate-spin" />}
            {mode === 'add' ? 'Add Credits' : mode === 'reset' ? 'Reset Credits' : 'Deduct Credits'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function AdminUsers({ adminToken, navContext, onNavigate }) {
  const [users, setUsers]             = useState([]);
  const [loading, setLoading]         = useState(true);
  const [search, setSearch]           = useState('');
  const [searchInput, setSearchInput] = useState(navContext?.search || '');
  const [page, setPage]               = useState(1);
  const [hasMore, setHasMore]         = useState(false);
  const [tab, setTab]                 = useState('all');
  const [riskData, setRiskData]       = useState(null);
  const [riskLoading, setRiskLoading] = useState(false);
  const [riskMap, setRiskMap]         = useState({});
  const [creditsUser, setCreditsUser] = useState(null);

  const PAGE_SIZE = 50;

  const loadUsers = useCallback(async (q = '', p = 1) => {
    setLoading(true);
    try {
      const params = { limit: PAGE_SIZE, offset: (p - 1) * PAGE_SIZE };
      if (q.trim()) params.search = q.trim();
      const res = await adminGetUsers(adminToken, params);
      const data = res.data;
      const list = Array.isArray(data) ? data : data.users || [];
      const total = data.total ?? list.length;
      setUsers(p === 1 ? list : prev => [...prev, ...list]);
      setHasMore((p - 1) * PAGE_SIZE + list.length < total);
      setPage(p);
    } catch {
      toast.error('Failed to load users');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { loadUsers(); }, [loadUsers]);

  // Task #298 — publish current filter set + selected user (the one
  // whose credits modal is open) onto SyraContext so the orb can
  // resolve "ban this user" / "show me only the suspended ones".
  useSyraFilters(useCallback(() => ({
    search: search || undefined,
    tab: tab !== 'all' ? tab : undefined,
  }), [search, tab])());
  useSyraSelection(creditsUser ? {
    type: 'user',
    id: creditsUser.id,
    label: creditsUser.name || creditsUser.email,
  } : null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput);
      loadUsers(searchInput, 1);
    }, 400);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const loadChurnRisk = async () => {
    setRiskLoading(true);
    try {
      const r = await churnRisk(adminToken);
      setRiskData(r.data);
      const map = {};
      (r.data.users || []).forEach(u => { map[u.id] = u; });
      setRiskMap(map);
      toast.success('Churn risk scores loaded');
    } catch {
      toast.error('Failed to load churn risk');
    } finally { setRiskLoading(false); }
  };

  const handleStatusChange = async (userId, newStatus) => {
    try {
      await adminUpdateUserStatus(adminToken, userId, newStatus);
      setUsers((prev) => prev.map((u) => u.id === userId ? { ...u, status: newStatus } : u));
      toast.success('Status updated');
    } catch { toast.error('Failed to update status'); }
  };

  const handleRoleChange = async (userId, newRole) => {
    const user = users.find((u) => u.id === userId);
    const label = user?.name || user?.email || userId;
    if (newRole === 'educator') {
      const ok = window.confirm(`Promote ${label} to the educator role?\n\nEducators can self-serve add new educational sites to the allowlist after a safety probe. Only do this for trusted teachers.`);
      if (!ok) return;
    }
    const reason = newRole === 'educator'
      ? (window.prompt('Optional: short reason / context for the audit log') || '').trim()
      : '';
    try {
      await adminUpdateUserRole(adminToken, userId, newRole, reason || undefined);
      setUsers((prev) => prev.map((u) => u.id === userId ? { ...u, role: newRole } : u));
      toast.success(newRole === 'educator' ? `${label} is now an educator` : `${label} reverted to student`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to update role');
    }
  };

  const handleResetQuizQuota = async (user) => {
    // Task #615: clear today's quiz-quota counter for this user.
    const label = user.name || user.email || user.id;
    const ok = window.confirm(`Reset today's quiz quota for ${label}?\n\nThis lets the student generate quizzes again right away. The action is audit-logged.`);
    if (!ok) return;
    try {
      const r = await adminResetQuizQuota(adminToken, user.id);
      const cleared = r?.data?.cleared ?? 0;
      const limit   = r?.data?.limit;
      toast.success(
        cleared > 0
          ? `Cleared ${cleared}/${limit} quiz calls for ${label}`
          : `Quota was already at 0/${limit ?? '—'}`
      );
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Failed to reset quiz quota');
    }
  };

  const handleViewQuizQuota = async (user) => {
    try {
      const r = await adminGetQuizQuota(adminToken, user.id);
      const { used, limit, remaining } = r?.data || {};
      toast.info(
        `Quiz quota for ${user.name || user.email}: ${used}/${limit} used today (${remaining} remaining; resets at midnight UTC)`,
        { duration: 6000 },
      );
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Failed to read quiz quota');
    }
  };

  const handlePlanChange = async (userId, newPlan) => {
    try {
      await adminUpdateUserPlan(adminToken, userId, newPlan);
      setUsers((prev) => prev.map((u) => u.id === userId ? { ...u, plan: newPlan } : u));
      toast.success('Plan updated');
    } catch { toast.error('Failed to update plan'); }
  };

  const atRiskUsers = riskData?.users?.filter(u => u.risk === 'high') || [];

  if (loading && users.length === 0) return <div className="flex justify-center p-10"><Loader2 size={24} className="animate-spin text-violet-500" /></div>;

  return (
    <SectionErrorBoundary name="Users">
      <div className="p-6 space-y-4">
        {creditsUser && (
          <CreditsModal
            user={creditsUser}
            adminToken={adminToken}
            onClose={() => setCreditsUser(null)}
            onUpdated={() => loadUsers(search, 1)}
          />
        )}

        <div className="flex items-center justify-between flex-wrap gap-3">
          <h2 className="text-gray-900 font-semibold text-lg" data-syra="users-header">Users ({users.length}{hasMore ? '+' : ''})</h2>
          <div className="flex items-center gap-3">
            <button onClick={loadChurnRisk} disabled={riskLoading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold transition-all bg-red-50 border border-red-200 text-red-600 hover:bg-red-100">
              {riskLoading ? <Loader2 size={12} className="animate-spin" /> : <TrendingDown size={12} />}
              Churn Risk
            </button>
            <div className="relative w-60">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input placeholder="Search users (server-side)..." value={searchInput} onChange={(e) => setSearchInput(e.target.value)}
                className="w-full pl-8 h-8 rounded-xl text-sm text-gray-900 placeholder-gray-400 outline-none border border-gray-200 bg-gray-50 focus:bg-white focus:ring-2 focus:ring-violet-500/20 focus:border-violet-400" />
            </div>
          </div>
        </div>

        {riskData && (
          <div className="rounded-2xl p-4 bg-red-50 border border-red-200" data-syra="churn-risk">
            <div className="flex items-center gap-2 mb-3">
              <TrendingDown size={15} color="#ef4444" />
              <span className="font-bold text-gray-900 text-sm">Churn Risk Summary</span>
            </div>
            <div className="grid grid-cols-3 gap-3 mb-3">
              {[
                { label: 'High Risk', count: riskData.summary.high_risk, color: '#ef4444' },
                { label: 'Medium Risk', count: riskData.summary.medium_risk, color: '#f59e0b' },
                { label: 'Low Risk', count: riskData.summary.low_risk, color: '#10b981' },
              ].map(s => (
                <div key={s.label} className="rounded-xl p-3 text-center bg-white border border-gray-200">
                  <div className="text-xl font-black" style={{ color: s.color }}>{s.count}</div>
                  <div className="text-[11px] text-gray-400">{s.label}</div>
                </div>
              ))}
            </div>
            {atRiskUsers.length > 0 && (
              <div>
                <p className="text-[11px] font-bold text-red-600 mb-2 uppercase tracking-wide">High Risk Users (take action)</p>
                {atRiskUsers.slice(0, 5).map(u => (
                  <div key={u.id} className="flex items-center gap-3 py-2 border-b border-gray-200">
                    <div className="flex-1">
                      <span className="text-sm text-gray-700 font-semibold">{u.name || u.email}</span>
                      <span className="text-xs text-gray-400 ml-2">{u.email}</span>
                    </div>
                    <div className="text-xs text-gray-400">{u.factors?.join(' · ')}</div>
                    <RiskBadge risk={u.risk} score={u.risk_score} />
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="rounded-2xl overflow-hidden bg-white border border-gray-200 shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                <th className="text-left text-gray-500 font-medium px-4 py-3 text-xs">User</th>
                <th className="text-left text-gray-500 font-medium px-4 py-3 text-xs">Plan</th>
                <th className="text-left text-gray-500 font-medium px-4 py-3 text-xs">Role</th>
                <th className="text-left text-gray-500 font-medium px-4 py-3 text-xs">Status</th>
                <th className="text-left text-gray-500 font-medium px-4 py-3 text-xs">Credits</th>
                {Object.keys(riskMap).length > 0 && <th className="text-left text-gray-500 font-medium px-4 py-3 text-xs">Churn Risk</th>}
                <th className="text-left text-gray-500 font-medium px-4 py-3 text-xs">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => {
                const risk = riskMap[user.id];
                return (
                  <tr key={user.id} className="hover:bg-gray-50 transition-colors border-b border-gray-100">
                    <td className="px-4 py-3">
                      <div>
                        <p className="text-gray-700 font-medium">{user.name}</p>
                        <p className="text-gray-400 text-xs">{user.email}</p>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button className={`text-xs px-2 py-1 rounded-full flex items-center gap-1 ${PLAN_COLORS[user.plan] || PLAN_COLORS.free}`}>
                            <Crown size={10} /> {user.plan}
                            <ChevronDown size={10} />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent className="bg-white border border-gray-200 shadow-lg">
                          {['free', 'starter', 'pro'].map((p) => (
                            <DropdownMenuItem key={p} className="text-gray-600 focus:bg-gray-50" onClick={() => handlePlanChange(user.id, p)}>
                              {p}
                            </DropdownMenuItem>
                          ))}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                    <td className="px-4 py-3">
                      {(() => {
                        const effectiveRole = user.role || (user.is_admin ? 'admin' : 'student');
                        const isAdminUser = effectiveRole === 'admin' || user.is_admin;
                        if (isAdminUser) {
                          return (
                            <span className={`text-xs px-2 py-1 rounded-full inline-flex items-center gap-1 ${ROLE_COLORS.admin}`}>
                              <Crown size={10} /> admin
                            </span>
                          );
                        }
                        return (
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <button
                                title="Change role"
                                className={`text-xs px-2 py-1 rounded-full inline-flex items-center gap-1 ${ROLE_COLORS[effectiveRole] || ROLE_COLORS.student}`}
                              >
                                {effectiveRole === 'educator' ? <GraduationCap size={10} /> : null}
                                {effectiveRole}
                                <ChevronDown size={10} />
                              </button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent className="bg-white border border-gray-200 shadow-lg">
                              <DropdownMenuItem
                                className="text-gray-600 focus:bg-gray-50"
                                onClick={() => handleRoleChange(user.id, 'student')}
                                disabled={effectiveRole === 'student'}
                              >
                                student
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                className="text-sky-600 focus:bg-gray-50"
                                onClick={() => handleRoleChange(user.id, 'educator')}
                                disabled={effectiveRole === 'educator'}
                              >
                                <GraduationCap size={14} className="mr-2" /> Promote to educator
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        );
                      })()}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-xs px-2 py-1 rounded-full ${STATUS_COLORS[user.status] || STATUS_COLORS.active}`}>
                        {user.status || 'active'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => setCreditsUser(user)}
                        className="flex items-center gap-1.5 text-gray-500 hover:text-violet-600 text-xs transition-colors"
                        title="Manage credits"
                      >
                        <CreditCard size={12} />
                        {user.credits_used || 0} / {user.credits_limit || 0}
                      </button>
                    </td>
                    {Object.keys(riskMap).length > 0 && (
                      <td className="px-4 py-3">
                        {risk ? <RiskBadge risk={risk.risk} score={risk.risk_score} /> : <span className="text-gray-300 text-xs">—</span>}
                      </td>
                    )}
                    <td className="px-4 py-3">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button className="h-7 px-2 text-xs text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition-colors flex items-center gap-1">
                            Actions <ChevronDown size={10} />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent className="bg-white border border-gray-200 shadow-lg">
                          <DropdownMenuItem className="text-gray-600 focus:bg-gray-50" onClick={() => setCreditsUser(user)}>
                            <CreditCard size={14} className="mr-2 text-violet-500" /> Manage Credits
                          </DropdownMenuItem>
                          <DropdownMenuItem className="text-gray-600 focus:bg-gray-50" onClick={() => handleViewQuizQuota(user)}>
                            <Trophy size={14} className="mr-2 text-amber-500" /> View Quiz Quota
                          </DropdownMenuItem>
                          <DropdownMenuItem className="text-gray-600 focus:bg-gray-50" onClick={() => handleResetQuizQuota(user)}>
                            <RefreshCw size={14} className="mr-2 text-amber-500" /> Reset Quiz Quota
                          </DropdownMenuItem>
                          <DropdownMenuItem className="text-gray-600 focus:bg-gray-50" onClick={() => handleStatusChange(user.id, 'active')}>
                            <CheckCircle size={14} className="mr-2 text-emerald-500" /> Set Active
                          </DropdownMenuItem>
                          <DropdownMenuItem className="text-gray-600 focus:bg-gray-50" onClick={() => handleStatusChange(user.id, 'suspended')}>
                            Set Suspended
                          </DropdownMenuItem>
                          <DropdownMenuItem className="text-red-500 focus:bg-gray-50" onClick={() => handleStatusChange(user.id, 'banned')}>
                            <Ban size={14} className="mr-2" /> Ban User
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {loading && (
            <div className="flex justify-center p-4">
              <Loader2 size={16} className="animate-spin text-violet-500" />
            </div>
          )}
          {hasMore && !loading && (
            <div className="flex justify-center p-4">
              <button onClick={() => loadUsers(search, page + 1)}
                className="text-xs text-gray-500 hover:text-violet-600 px-4 py-2 rounded-xl transition-colors border border-gray-200 hover:border-violet-200">
                Load more users
              </button>
            </div>
          )}
        </div>
        <AdminQuickLinks links={['conversations','analytics','notifications','monetization']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
