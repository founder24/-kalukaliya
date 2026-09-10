import { Info } from 'lucide-react';

const DETAILS = {
  seomanager: {
    title: 'SEO Manager',
    description: 'Detailed SEO administration is not available on the current Cloudflare-native backend.',
    available: 'Public sitemap and SEO health routes continue to serve the student site.',
  },
  users: {
    title: 'Users',
    description: 'Account-level user browsing and editing are intentionally unavailable in this staff portal.',
    available: 'Privacy-safe user totals remain available in Analytics.',
  },
  conversations: {
    title: 'Conversations',
    description: 'Staff access to individual conversation records is not supported by the current backend.',
    available: 'Aggregate chat activity remains available in Analytics.',
  },
  notifications: {
    title: 'Notifications',
    description: 'Broadcast, automation, and push-delivery administration are not supported by the current backend.',
    available: 'No notification request is sent from this module.',
  },
  ai: {
    title: 'AI & Automation',
    description: 'Provider controls and automation-job administration are not exposed by the current backend.',
    available: 'Aggregate RAG and chat health remains available in Analytics.',
  },
  security: {
    title: 'Access & Security',
    description: 'Legacy alert, rate-policy, and access diagnostics are not exposed by the current backend.',
    available: 'Authentication and staff authorization continue to be enforced by the Worker.',
  },
  logs: {
    title: 'Logs',
    description: 'The retired unified-log and admin-action feeds are not available in this portal.',
    available: 'No log request is sent from this module.',
  },
  health: {
    title: 'Health / Uptime',
    description: 'Legacy provider-specific health dashboards are not available on the current backend.',
    available: 'The portal header uses the live, read-only Worker health endpoint.',
  },
  ops: {
    title: 'Ops Console',
    description: 'The legacy SLA ledger, outage map, and runtime-toggle snapshot are not available.',
    available: 'Reliability aggregates remain available in Analytics.',
  },
  settings: {
    title: 'Site Settings',
    description: 'Legacy mutable site settings are not exposed by the current backend.',
    available: 'No settings read or write is attempted from this module.',
  },
};

export default function AdminModuleUnavailable({ moduleId }) {
  const detail = DETAILS[moduleId] || {
    title: 'Staff module',
    description: 'This module is not available on the current backend.',
    available: 'No retired request is sent.',
  };

  return (
    <section
      className="mx-auto mt-8 max-w-2xl rounded-2xl border border-amber-200 bg-amber-50 p-6"
      data-testid={`admin-module-unavailable-${moduleId}`}
    >
      <div className="flex items-start gap-3">
        <Info className="mt-0.5 h-5 w-5 flex-none text-amber-700" aria-hidden="true" />
        <div>
          <h2 className="text-base font-semibold text-gray-900">{detail.title}</h2>
          <p className="mt-2 text-sm leading-6 text-gray-700">{detail.description}</p>
          <p className="mt-3 text-xs leading-5 text-gray-600">{detail.available}</p>
        </div>
      </div>
    </section>
  );
}