import { useState, useEffect, useCallback } from 'react';
import { ChevronDown, ChevronUp, Plus, Trash2, X } from 'lucide-react';
import AdminQuickLinks from './AdminQuickLinks';
import { toast } from 'sonner';
import { adminGetRoadmap, adminCreateRoadmapItem, adminDeleteRoadmapItem, adminUpdateRoadmapItem } from '@/utils/api';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
const STATUS_OPTIONS = ['done', 'in-progress', 'next', 'upcoming', 'future'];
const EFFORT_OPTIONS = ['low', 'medium', 'high'];
const IMPACT_OPTIONS = ['low', 'medium', 'high', 'critical'];

const STATUS_CONFIG = {
  done:          { label: 'Done',        color: 'text-emerald-600', dot: 'bg-emerald-500', border: 'border-emerald-200', bg: 'bg-emerald-50' },
  'in-progress': { label: 'In Progress', color: 'text-violet-600',  dot: 'bg-violet-500 animate-pulse', border: 'border-violet-200', bg: 'bg-violet-50' },
  next:          { label: 'Up Next',     color: 'text-amber-600', dot: 'bg-amber-500', border: 'border-amber-200', bg: 'bg-amber-50' },
  upcoming:      { label: 'Upcoming',    color: 'text-blue-600',   dot: 'bg-blue-400', border: 'border-blue-200', bg: 'bg-blue-50' },
  future:        { label: 'Future',      color: 'text-gray-500',   dot: 'bg-gray-300', border: 'border-gray-200', bg: 'bg-gray-50' },
};

const PHASE_STYLES = [
  { color: 'text-violet-600', bg: 'bg-violet-100' },
  { color: 'text-blue-600', bg: 'bg-blue-100' },
  { color: 'text-emerald-600', bg: 'bg-emerald-100' },
  { color: 'text-orange-600', bg: 'bg-orange-100' },
  { color: 'text-pink-600', bg: 'bg-pink-100' },
  { color: 'text-cyan-600', bg: 'bg-cyan-100' },
];

function StepCard({ step, adminToken, onRefresh }) {
  const [expanded, setExpanded] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [updatingStatus, setUpdatingStatus] = useState(false);
  const sc = STATUS_CONFIG[step.status] || STATUS_CONFIG.future;

  const handleDelete = async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete "${step.title}"?`)) return;
    setDeleting(true);
    try {
      await adminDeleteRoadmapItem(adminToken, step.id || step._id);
      toast.success('Item deleted');
      onRefresh();
    } catch (err) {
      toast.error('Failed to delete item');
    } finally {
      setDeleting(false);
    }
  };

  const handleStatusChange = async (e) => {
    const newStatus = e.target.value;
    setUpdatingStatus(true);
    try {
      await adminUpdateRoadmapItem(adminToken, step.id || step._id, { status: newStatus });
      toast.success('Status updated');
      onRefresh();
    } catch (err) {
      toast.error('Failed to update status');
    } finally {
      setUpdatingStatus(false);
    }
  };

  return (
    <div className={`rounded-xl border ${sc.border} ${sc.bg} overflow-hidden`}>
      <button className="w-full flex items-center gap-3 p-3 text-left" onClick={() => setExpanded(!expanded)}>
        <div className={`w-2 h-2 rounded-full flex-shrink-0 ${sc.dot}`} />
        <span className="flex-1 text-sm text-gray-700 font-medium">{step.title}</span>
        <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${sc.color} bg-white/60`}>{sc.label}</span>
        {expanded ? <ChevronUp size={14} className="text-gray-400" /> : <ChevronDown size={14} className="text-gray-400" />}
      </button>
      {expanded && (
        <div className="px-4 pb-3 space-y-2">
          {(step.description || step.desc) && <p className="text-sm text-gray-500">{step.description || step.desc}</p>}
          <div className="flex items-center gap-2 flex-wrap">
            {step.effort && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">Effort: {step.effort}</span>
            )}
            {step.impact && (
              <span className={`text-[10px] px-2 py-0.5 rounded-full bg-gray-100 ${step.impact === 'critical' ? 'text-red-600' : step.impact === 'high' ? 'text-amber-600' : 'text-gray-500'}`}>Impact: {step.impact}</span>
            )}
          </div>
          <div className="flex items-center gap-2 pt-1">
            <select
              value={step.status}
              onChange={handleStatusChange}
              disabled={updatingStatus}
              className="text-xs bg-white border border-gray-200 rounded-lg px-2 py-1 text-gray-700 outline-none focus:border-violet-400"
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>{STATUS_CONFIG[s]?.label || s}</option>
              ))}
            </select>
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="ml-auto text-red-400 hover:text-red-600 transition-colors p-1 rounded-lg hover:bg-red-50"
            >
              <Trash2 size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function AddItemForm({ adminToken, onRefresh, onClose }) {
  const [title, setTitle] = useState('');
  const [desc, setDesc] = useState('');
  const [phase, setPhase] = useState('');
  const [status, setStatus] = useState('future');
  const [effort, setEffort] = useState('medium');
  const [impact, setImpact] = useState('medium');
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!title.trim() || !phase.trim()) {
      toast.error('Title and phase are required');
      return;
    }
    setSaving(true);
    try {
      await adminCreateRoadmapItem(adminToken, {
        title: title.trim(),
        description: desc.trim(),
        phase: phase.trim(),
        status,
        effort,
        impact,
      });
      toast.success('Roadmap item created');
      onRefresh();
      onClose();
    } catch (err) {
      toast.error('Failed to create item');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="rounded-2xl border border-gray-200 p-4 space-y-3 bg-white shadow-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">Add Roadmap Item</h3>
        <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600">
          <X size={16} />
        </button>
      </div>
      <input
        type="text"
        placeholder="Title *"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        className="w-full text-sm bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-gray-900 placeholder-gray-400 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20"
      />
      <input
        type="text"
        placeholder="Phase name (e.g. MVP Hardening) *"
        value={phase}
        onChange={(e) => setPhase(e.target.value)}
        className="w-full text-sm bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-gray-900 placeholder-gray-400 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20"
      />
      <textarea
        placeholder="Description"
        value={desc}
        onChange={(e) => setDesc(e.target.value)}
        rows={2}
        className="w-full text-sm bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-gray-900 placeholder-gray-400 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20 resize-none"
      />
      <div className="flex gap-2">
        <select value={status} onChange={(e) => setStatus(e.target.value)} className="text-xs bg-white border border-gray-200 rounded-lg px-2 py-1.5 text-gray-700 outline-none flex-1">
          {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{STATUS_CONFIG[s]?.label || s}</option>)}
        </select>
        <select value={effort} onChange={(e) => setEffort(e.target.value)} className="text-xs bg-white border border-gray-200 rounded-lg px-2 py-1.5 text-gray-700 outline-none flex-1">
          {EFFORT_OPTIONS.map((e) => <option key={e} value={e}>Effort: {e}</option>)}
        </select>
        <select value={impact} onChange={(e) => setImpact(e.target.value)} className="text-xs bg-white border border-gray-200 rounded-lg px-2 py-1.5 text-gray-700 outline-none flex-1">
          {IMPACT_OPTIONS.map((i) => <option key={i} value={i}>Impact: {i}</option>)}
        </select>
      </div>
      <button
        type="submit"
        disabled={saving}
        className="w-full text-sm font-medium py-2 rounded-lg bg-violet-600 hover:bg-violet-700 text-white transition-colors disabled:opacity-50"
      >
        {saving ? 'Creating…' : 'Create Item'}
      </button>
    </form>
  );
}

export default function AdminRoadmap({ adminToken, onNavigate }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);

  const fetchRoadmap = useCallback(async () => {
    try {
      const res = await adminGetRoadmap(adminToken);
      setItems(res.data || []);
    } catch (err) {
      toast.error('Failed to load roadmap');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => {
    fetchRoadmap();
  }, [fetchRoadmap]);

  const phases = items.reduce((acc, item) => {
    const phaseName = item.phase || 'Uncategorized';
    if (!acc[phaseName]) acc[phaseName] = [];
    acc[phaseName].push(item);
    return acc;
  }, {});

  const phaseEntries = Object.entries(phases);

  return (
    <SectionErrorBoundary name="Roadmap">
      <div className="space-y-6 max-w-4xl">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-gray-900">Product Roadmap</h2>
            <p className="text-sm text-gray-400 mt-0.5">Development phases and feature status</p>
          </div>
          <button
            onClick={() => setShowAdd(!showAdd)}
            className="flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-700 text-white transition-colors"
          >
            <Plus size={14} />
            Add Item
          </button>
        </div>

        {showAdd && (
          <AddItemForm
            adminToken={adminToken}
            onRefresh={fetchRoadmap}
            onClose={() => setShowAdd(false)}
          />
        )}

        {loading ? (
          <div className="text-center py-12 text-gray-400 text-sm">Loading roadmap…</div>
        ) : phaseEntries.length === 0 ? (
          <div className="text-center py-12 text-gray-400 text-sm">No roadmap items yet. Click "Add Item" to get started.</div>
        ) : (
          phaseEntries.map(([phaseName, steps], idx) => {
            const style = PHASE_STYLES[idx % PHASE_STYLES.length];
            const done = steps.filter((s) => s.status === 'done').length;
            const pct = steps.length > 0 ? Math.round((done / steps.length) * 100) : 0;
            return (
              <div key={phaseName} className="rounded-2xl border border-gray-200 overflow-hidden bg-white shadow-sm">
                <div className="p-4 border-b border-gray-100">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <div className={`w-6 h-6 rounded-lg flex items-center justify-center text-xs font-bold text-white ${style.bg} ${style.color}`}>{idx}</div>
                      <h3 className={`font-semibold ${style.color}`}>{phaseName}</h3>
                    </div>
                    <span className="text-xs text-gray-400">{done}/{steps.length} done · {pct}%</span>
                  </div>
                  <div className="h-1 rounded-full bg-gray-100 overflow-hidden">
                    <div className="h-full rounded-full bg-violet-500 transition-all" style={{ width: `${pct}%` }} />
                  </div>
                </div>
                <div className="p-4 space-y-2">
                  {steps.map((step) => (
                    <StepCard
                      key={step.id || step._id}
                      step={step}
                      adminToken={adminToken}
                      onRefresh={fetchRoadmap}
                    />
                  ))}
                </div>
              </div>
            );
          })
        )}
        <AdminQuickLinks links={['dashboard','content','analytics']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
