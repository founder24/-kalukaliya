import {
  Database, Zap, MessageSquare, BookMarked,
} from 'lucide-react';

export default function AiCredits({ stats }) {
  return (
    <div className="glass-card rounded-2xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Usage</p>
      </div>
      <div className="p-4">
        <div className="grid grid-cols-2 gap-3">
          {[
            { icon: Database, label: 'Total Tokens', value: stats.total_tokens > 1000 ? `${(stats.total_tokens/1000).toFixed(0)}K` : stats.total_tokens, color: 'text-blue-600', bg: 'rgba(59,130,246,0.10)' },
            { icon: Zap, label: 'Chat Rate Limit', value: '6/min', color: 'text-emerald-600', bg: 'rgba(16,185,129,0.10)' },
            { icon: MessageSquare, label: 'Conversations', value: stats.conversations, color: 'text-violet-600', bg: 'rgba(139,92,246,0.10)' },
            { icon: BookMarked, label: 'Saved Subjects', value: stats.saved_subjects, color: 'text-pink-700', bg: 'rgba(244,63,94,0.10)' },
          ].map(({ icon: Icon, label, value, color, bg }) => (
            <div key={label} className="rounded-xl p-3" style={{ background: bg, border: `1px solid ${bg.replace('0.10', '0.20')}` }}>
              <Icon size={18} className={`${color} mb-2`} />
              <p className={`text-xl font-bold ${color}`}>{value}</p>
              <p className="text-muted-foreground/60 text-xs mt-0.5">{label}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
