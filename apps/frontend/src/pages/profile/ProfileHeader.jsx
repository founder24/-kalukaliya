import {
  BookMarked, MessageSquare, Zap, Crown, Check, Copy,
} from 'lucide-react';

export default function ProfileHeader({
  profile, stats, planInfo, creditsLimit, creditsRemaining,
  copiedId, handleCopyId, getInitials,
}) {
  return (
    <div
      className="relative rounded-3xl overflow-hidden p-6"
      style={{
        background: 'linear-gradient(135deg, rgba(124,58,237,0.20) 0%, rgba(139,92,246,0.12) 50%, rgba(124,58,237,0.08) 100%)',
        border: '1px solid rgba(139,92,246,0.25)',
        boxShadow: '0 8px 40px rgba(124,58,237,0.15)',
      }}
    >
      <div
        className="absolute top-0 right-0 w-48 h-48 rounded-full pointer-events-none"
        style={{
          background: 'radial-gradient(circle, rgba(139,92,246,0.20), transparent 70%)',
          filter: 'blur(20px)',
          animation: 'float 6s ease-in-out infinite',
        }}
      />
      <div
        className="absolute bottom-0 left-0 w-32 h-32 rounded-full pointer-events-none"
        style={{
          background: 'radial-gradient(circle, rgba(167,139,250,0.12), transparent 70%)',
          filter: 'blur(16px)',
          animation: 'float 8s ease-in-out infinite reverse',
        }}
      />
      <div
        className="absolute inset-0 pointer-events-none opacity-[0.06]"
        style={{
          backgroundImage: 'radial-gradient(rgba(167,139,250,1) 1px, transparent 1px)',
          backgroundSize: '20px 20px',
        }}
      />

      <div className="relative z-10 flex items-start gap-4">
        <div className="relative flex-shrink-0">
          {profile?.avatar_url ? (
            <div style={{ width: 72, height: 72 }}>
              <img
                src={profile.avatar_url}
                alt={profile?.name || 'Avatar'}
                width="72"
                height="72"
                className="w-full h-full rounded-2xl object-cover shadow-xl"
                style={{ boxShadow: '0 0 24px rgba(139,92,246,0.4)' }}
              />
            </div>
          ) : (
            <div
              className="rounded-2xl flex items-center justify-center text-2xl font-bold text-white shadow-xl"
              style={{
                width: 72, height: 72,
                background: 'linear-gradient(135deg, #7c3aed, #8b5cf6)',
                boxShadow: '0 0 24px rgba(139,92,246,0.4)',
              }}
            >
              {getInitials(profile?.name)}
            </div>
          )}
          <div
            className="absolute pointer-events-none"
            style={{
              inset: -6,
              borderRadius: '50%',
              border: '1.5px solid rgba(167,139,250,0.4)',
              animation: 'orbit 8s linear infinite',
            }}
          />
        </div>

        <div className="flex-1 min-w-0">
          <h1 className="text-xl font-bold text-foreground truncate">
            {profile?.name || 'User'}
          </h1>
          <p className="text-muted-foreground text-sm mt-0.5 truncate">{profile?.email}</p>

          <div className="flex items-center gap-2 mt-2">
            <span
              className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold border ${planInfo.badgeColor}`}
            >
              <Crown size={10} />
              {planInfo.label}
            </span>
            {profile?.board_name && (
              <span className="text-xs text-muted-foreground/60">{profile.board_name}</span>
            )}
          </div>
        </div>

        <button
          onClick={handleCopyId}
          className="text-muted-foreground/70 hover:text-foreground/60 transition-colors p-1"
          title="Copy User ID"
        >
          {copiedId ? <Check size={14} className="text-emerald-600" /> : <Copy size={14} />}
        </button>
      </div>

      <div className="relative z-10 flex items-center gap-3 mt-5">
        {[
          { icon: BookMarked, label: 'Saved',  value: stats.saved_subjects },
          { icon: MessageSquare, label: 'Chats', value: stats.conversations },
          { icon: Zap, label: 'Credits', value: creditsLimit === 0 ? 'Upgrade' : `${creditsRemaining}/${creditsLimit}` },
        ].map(({ icon: Icon, label, value }) => (
          <div
            key={label}
            className="flex-1 flex flex-col items-center p-2 rounded-xl min-w-0"
            style={{ background: 'hsl(var(--muted) / 0.5)', border: '1px solid hsl(var(--border) / 0.3)' }}
          >
            <Icon size={14} className="text-muted-foreground mb-1" />
            <span className="text-foreground text-sm font-semibold truncate max-w-full min-w-0">{value}</span>
            <span className="text-muted-foreground text-[10px]">{label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
