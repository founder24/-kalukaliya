import {
  LayoutDashboard, BookOpen, Globe, Cpu, Users, MessageSquare,
  TrendingUp, Crown, CreditCard, Bell, Key, Shield, ShieldAlert, Settings,
  Activity, HeartPulse, GitBranch, Zap,
} from 'lucide-react';

const ICON_MAP = {
  dashboard:     LayoutDashboard,
  roadmap:       GitBranch,
  content:       BookOpen,
  contenthub:    BookOpen,
  seomanager:    Globe,
  vertex:        Cpu,
  ai:            Cpu,
  users:         Users,
  conversations: MessageSquare,
  feedback:      MessageSquare,
  analytics:     TrendingUp,
  monetization:  Crown,
  revenue:       Crown,
  plans:         CreditCard,
  ads:           TrendingUp,
  notifications: Bell,
  apiconfig:     Key,
  googleauth:    Shield,
  security:      ShieldAlert,
  settings:      Settings,
  ratelimits:    Shield,
  botsecurity:   ShieldAlert,
  edubrowser:    Shield,
  activitylog:   Activity,
  logs:          Activity,
  logsexplorer:  Activity,
  automation:    Zap,
  health:        HeartPulse,
};

const LABEL_MAP = {
  dashboard:     'Dashboard',
  roadmap:       'Roadmap',
  content:       'Content',
  contenthub:    'Content',
  seomanager:    'SEO Manager',
  vertex:        'AI Studio',
  ai:            'AI & Automation',
  users:         'Users',
  conversations: 'Conversations',
  feedback:      'Chat Feedback',
  analytics:     'Analytics',
  monetization:  'Monetization',
  revenue:       'Revenue',
  plans:         'Plans & Credits',
  ads:           'Ad Revenue',
  notifications: 'Notifications',
  apiconfig:     'API Config',
  googleauth:    'Google Auth',
  security:      'Access & Security',
  settings:      'Site Settings',
  ratelimits:    'Rate Limits',
  botsecurity:   'Bot Security',
  edubrowser:    'Edu Mode',
  activitylog:   'Admin Actions',
  logs:          'Logs',
  logsexplorer:  'Logs',
  automation:    'Automation',
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
