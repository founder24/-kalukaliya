import { BookOpen, Users, TrendingUp } from 'lucide-react';
import AnimatedStat from './AnimatedStat';
import AnimatedChatDemo from './AnimatedChatDemo';
import { usePublicStats } from '@/hooks/usePublicStats';

const _statLabels = {
  en: { divisions: 'AssamBoard Divisions', students: 'Students', plans: 'Plans' },
  as: { divisions: 'অসম বোৰ্ড বিভাগ', students: 'ছাত্ৰ-ছাত্ৰী', plans: 'পৰিকল্পনা' },
};

export default function HeroBelowFold({ contentLang = 'en', browserPath, onUrlChange }) {
  const publicStats = usePublicStats();
  const userCount = publicStats?.total_users || 100;
  const labels = _statLabels[contentLang] || _statLabels.en;

  const stats = [
    { value: '3',             label: labels.divisions, icon: BookOpen   },
    { value: `${userCount}+`, label: labels.students,  icon: Users      },
    { value: '3',             label: labels.plans,     icon: TrendingUp },
  ];

  return (
    <>
      <div
        className="mt-16 relative max-w-3xl mx-auto"
        style={{ animation: 'heroFrameIn 1s cubic-bezier(0.16, 1, 0.3, 1) 0.2s both' }}
      >
        <div
          className="absolute -inset-4 rounded-3xl pointer-events-none"
          style={{ background: 'rgba(124,58,237,0.08)', filter: 'blur(60px)' }}
        />
        <div
          className="relative rounded-3xl overflow-hidden"
          style={{
            border: '1px solid rgba(139,92,246,0.15)',
            background: 'linear-gradient(135deg, rgba(15,10,30,0.95) 0%, rgba(20,15,40,0.98) 100%)',
            boxShadow: '0 32px 80px rgba(0,0,0,0.25), 0 0 0 1px rgba(139,92,246,0.08)',
          }}
        >
          <div
            className="flex items-center gap-2 px-4 py-3 border-b"
            style={{ borderColor: 'rgba(255,255,255,0.08)', background: 'rgba(255,255,255,0.02)' }}
          >
            <span className="w-3 h-3 rounded-full" style={{ background: 'rgba(239,68,68,0.6)' }} />
            <span className="w-3 h-3 rounded-full" style={{ background: 'rgba(234,179,8,0.6)' }} />
            <span className="w-3 h-3 rounded-full" style={{ background: 'rgba(34,197,94,0.6)' }} />
            <div
              className="flex-1 mx-4 h-6 rounded-lg flex items-center px-3"
              style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)' }}
            >
              <span
                key={browserPath}
                className="text-xs"
                style={{ color: 'rgba(255,255,255,0.60)', animation: 'fadeIn 0.3s ease-out both' }}
              >
                syrabit.ai/{browserPath}
              </span>
            </div>
          </div>

          <AnimatedChatDemo onUrlChange={onUrlChange} contentLang={contentLang} />
        </div>
      </div>

      <section
        className="py-16 mt-16 -mx-5"
        style={{
          background: 'hsl(var(--muted) / 0.3)',
          borderTop: '1px solid hsl(var(--border) / 0.3)',
          borderBottom: '1px solid hsl(var(--border) / 0.3)',
        }}
      >
        <div className="max-w-4xl mx-auto px-5 grid grid-cols-1 sm:grid-cols-3 gap-8">
          {stats.map((s, i) => (
            <div
              key={s.label}
              style={{ animation: `revealUp 0.7s cubic-bezier(0.16, 1, 0.3, 1) ${i * 0.07}s both` }}
            >
              <AnimatedStat value={s.value} label={s.label} icon={s.icon} />
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
