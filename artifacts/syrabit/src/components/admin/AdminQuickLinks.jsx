import {
  LayoutDashboard, BookOpen, Globe, Cpu, Users, MessageSquare,
  TrendingUp, Crown, CreditCard, Bell, Key, Shield, Settings,
  Activity, HeartPulse, GitBranch,
} from 'lucide-react';

const ICON_MAP = {
  dashboard:     LayoutDashboard,
  roadmap:       GitBranch,
  content:       BookOpen,
  seomanager:    Globe,
  vertex:        Cpu,
  users:         Users,
  conversations: MessageSquare,
  analytics:     TrendingUp,
  monetization:  Crown,
  plans:         CreditCard,
  notifications: Bell,
  apiconfig:     Key,
  googleauth:    Shield,
  settings:      Settings,
  ratelimits:    Shield,
  activitylog:   Activity,
  health:        HeartPulse,
};

const LABEL_MAP = {
  dashboard:     'Dashboard',
  roadmap:       'Roadmap',
  content:       'Content',
  seomanager:    'SEO Manager',
  vertex:        'AI Studio',
  users:         'Users',
  conversations: 'Conversations',
  analytics:     'Analytics',
  monetization:  'Monetization',
  plans:         'Plans & Credits',
  notifications: 'Notifications',
  apiconfig:     'API Config',
  googleauth:    'Google Auth',
  settings:      'Site Settings',
  ratelimits:    'Rate Limits',
  activitylog:   'Activity Log',
  health:        'Health / Uptime',
};

export default function AdminQuickLinks({ links = [], onNavigate }) {
  if (!onNavigate || links.length === 0) return null;
  return (
    <div className="mt-8 p-3 px-4 bg-gray-50 border border-gray-200 rounded-xl">
      <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-2.5">
        Related Sections
      </p>
      <div className="flex flex-wrap gap-2">
        {links.map(id => {
          const Icon = ICON_MAP[id] || LayoutDashboard;
          return (
            <button
              key={id}
              onClick={() => onNavigate(id)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-200 rounded-lg text-xs font-medium text-gray-500 hover:text-violet-600 hover:border-violet-200 hover:bg-violet-50 transition-all cursor-pointer"
            >
              <Icon size={12} />
              {LABEL_MAP[id]}
            </button>
          );
        })}
      </div>
    </div>
  );
}
