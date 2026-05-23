import { Link } from 'react-router-dom';
import {
  ShieldCheck, ChevronRight, AlertTriangle, Trash2,
  Clock, Loader2,
} from 'lucide-react';

export function DeletionBanner({
  deletionPending, getDeletionHoursLeft,
  cancellingDelete, handleCancelDeletion,
}) {
  if (!deletionPending) return null;
  return (
    <div
      className="rounded-2xl p-4"
      style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)' }}
    >
      <div className="flex items-start gap-3">
        <Clock size={18} className="text-amber-700 flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="text-amber-700 font-semibold text-sm">Account deletion scheduled</p>
          <p className="text-amber-700/70 text-xs mt-0.5">
            Your account will be permanently deleted in ~{getDeletionHoursLeft()} hours.
            All data will be erased.
          </p>
        </div>
        <button
          onClick={handleCancelDeletion}
          disabled={cancellingDelete}
          className="flex-shrink-0 px-3 py-1.5 rounded-xl text-xs font-semibold text-amber-700 border border-amber-400/30 hover:bg-amber-400/10 transition-colors"
        >
          {cancellingDelete ? <Loader2 size={12} className="animate-spin" /> : 'Cancel Deletion'}
        </button>
      </div>
    </div>
  );
}

export default function DangerZone({ profile, deletionPending, setShowDeleteConfirm }) {
  return (
    <>
      {profile?.is_admin && (
        <Link to="/admin">
          <div
            className="rounded-2xl p-4 flex items-center gap-3 transition-all hover:opacity-90 cursor-pointer"
            style={{
              background: 'linear-gradient(135deg, rgba(124,58,237,0.15), rgba(99,102,241,0.10))',
              border: '1px solid rgba(139,92,246,0.30)',
              boxShadow: '0 0 24px rgba(124,58,237,0.08)',
            }}
          >
            <div className="w-10 h-10 rounded-xl flex items-center justify-center"
              style={{ background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.25)' }}>
              <ShieldCheck size={18} style={{ color: 'hsl(var(--primary))' }} />
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-foreground">Admin Portal</span>
                <span className="text-[10px] font-bold px-2 py-0.5 rounded-full"
                  style={{ background: 'rgba(124,58,237,0.15)', color: 'hsl(var(--primary))', border: '1px solid rgba(139,92,246,0.25)' }}>
                  INTERNAL
                </span>
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              </div>
              <p className="text-xs text-muted-foreground/60 mt-0.5">Manage users, content, analytics</p>
            </div>
            <ChevronRight size={14} className="text-muted-foreground/70" />
          </div>
        </Link>
      )}

      {!deletionPending && (
        <div className="glass-card rounded-2xl overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Danger Zone</p>
          </div>
          <div className="p-4">
            <div className="flex items-start gap-3 p-3 rounded-xl"
              style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.15)' }}>
              <AlertTriangle size={16} className="text-red-600 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <p className="text-sm font-semibold text-foreground">Delete Account</p>
                <p className="text-xs text-muted-foreground/70 mt-0.5">
                  Permanently delete your account and all data after a 72-hour grace period.
                </p>
              </div>
              <button
                onClick={() => setShowDeleteConfirm(true)}
                className="flex-shrink-0 px-3 py-1.5 rounded-xl text-xs font-semibold text-red-600 border border-red-500/25 hover:bg-red-500/10 transition-colors"
                data-testid="delete-account-button"
              >
                <Trash2 size={12} className="inline mr-1" />
                Delete
              </button>
            </div>

            <p className="text-xs text-muted-foreground mt-3 text-center">
              Member since {profile?.created_at ? new Date(profile.created_at).toLocaleDateString('en-IN', { year: 'numeric', month: 'long' }) : '—'}
            </p>
          </div>
        </div>
      )}
    </>
  );
}
