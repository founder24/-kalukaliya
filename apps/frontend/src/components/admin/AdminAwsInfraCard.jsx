import React, { useState, useEffect, useCallback, useMemo } from 'react';
import axios from 'axios';
import { ShieldCheck, AlertTriangle, RefreshCw, Clock, ExternalLink } from 'lucide-react';
import { API_BASE } from '@/utils/api';

/*
  AdminAwsInfraCard
  =================

  Phase 4 — Async worker port (Task #332).

  Renders the live state of the AWS SQS + Lambda worker tier
  defined in `infra/aws/sqs.tf` + `infra/aws/lambda-workers.tf`.

  Backend contract — `GET /admin/aws/workers/health`:
    {
      composite: "ok" | "degraded" | "failed" | "unknown",
      asOf: "2026-05-04T12:34:56Z",
      queues: [
        {
          key:           "seo-indexnow",
          queueName:     "syrabit-seo-indexnow",
          dlqName:       "syrabit-seo-indexnow-dlq",
          backlog:       12,         // ApproximateNumberOfMessagesVisible
          dlqDepth:      0,
          consumerName:  "syrabit-seo-indexnow-consumer",
          consumerErrorRate: 0.0,    // last 5 min
          alarmState:    "OK" | "ALARM" | "INSUFFICIENT_DATA",
          backlogThreshold: 500,
        },
        ...
      ],
      compositeAlarmArn: "arn:aws:cloudwatch:ap-south-1:...:alarm:syrabit-workers-degraded"
    }

  The endpoint is implemented by routes/admin_aws_infra.py on the
  backend — it proxies CloudWatch GetMetricData + the composite
  alarm DescribeAlarms call so the React side never holds an AWS
  credential. The ops_alerts SNS topic (observability.tf) is the
  source of truth for paging; this card is purely *visibility*.
*/

const STATUS_STYLES = {
  ok:       { wrap: 'bg-emerald-50 border border-emerald-200', icon: <ShieldCheck size={20} className="text-emerald-500" />,    text: 'text-emerald-700', label: 'AWS workers — all queues green' },
  degraded: { wrap: 'bg-amber-50 border border-amber-200',     icon: <AlertTriangle size={20} className="text-amber-500" />,    text: 'text-amber-700',   label: 'AWS workers — backlog or DLQ alarm' },
  failed:   { wrap: 'bg-red-50 border border-red-200',         icon: <AlertTriangle size={20} className="text-red-500" />,      text: 'text-red-700',     label: 'AWS workers — composite alarm in ALARM state' },
  unknown:  { wrap: 'bg-gray-50 border border-gray-200',       icon: <Clock size={20} className="text-gray-400" />,             text: 'text-gray-500',    label: 'AWS workers — health probe pending' },
};

const adminHeaders = (token) => (token ? { Authorization: `Bearer ${token}` } : {});

export default function AdminAwsInfraCard({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    axios
      .get(`${API_BASE}/admin/aws/workers/health`, {
        headers: adminHeaders(adminToken),
        withCredentials: true,
      })
      .then((r) => setData(r.data))
      .catch((e) => setError(e?.response?.status ? `HTTP ${e.response.status}` : 'unreachable'))
      .finally(() => setLoading(false));
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  const composite = data?.composite || (error ? 'unknown' : 'unknown');
  const style = STATUS_STYLES[composite] || STATUS_STYLES.unknown;

  // Sort queues with anything non-OK first so degradations are at
  // the top of the table without scrolling.
  const queues = useMemo(() => {
    const list = (data?.queues || []).slice();
    list.sort((a, b) => {
      const rank = (q) => (q.dlqDepth > 0 ? 0 : q.alarmState === 'ALARM' ? 1 : q.backlog > 0 ? 2 : 3);
      return rank(a) - rank(b);
    });
    return list;
  }, [data]);

  return (
    <div className="rounded-2xl bg-white border border-gray-200 p-4 space-y-4" data-testid="admin-aws-infra-card">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          AWS Infra · SQS + Lambda workers
        </h3>
        <button
          onClick={load}
          disabled={loading}
          className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100"
          data-testid="admin-aws-infra-refresh"
          title="Refresh"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className={`rounded-2xl p-3 flex items-center gap-3 ${style.wrap}`} data-testid="admin-aws-infra-banner">
        {style.icon}
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold ${style.text}`} data-testid="admin-aws-infra-status">
            {error ? `AWS workers — health probe error (${error})` : style.label}
          </p>
          {data?.asOf && (
            <p className="text-[11px] text-gray-500 mt-0.5">
              Snapshot {new Date(data.asOf).toLocaleString()} · {queues.length} queues
            </p>
          )}
        </div>
      </div>

      {queues.length > 0 && (
        <div className="overflow-x-auto -mx-1">
          <table className="w-full text-xs">
            <thead className="text-gray-400">
              <tr className="text-left">
                <th className="px-2 py-1 font-medium">Queue</th>
                <th className="px-2 py-1 font-medium text-right">Backlog</th>
                <th className="px-2 py-1 font-medium text-right">DLQ</th>
                <th className="px-2 py-1 font-medium text-right">Err %</th>
                <th className="px-2 py-1 font-medium">Alarm</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {queues.map((q) => {
                const isAlarm = q.alarmState === 'ALARM' || q.dlqDepth > 0;
                const rowCls = isAlarm
                  ? 'bg-red-50/50'
                  : q.backlog > q.backlogThreshold * 0.5
                    ? 'bg-amber-50/40'
                    : '';
                return (
                  <tr key={q.key} className={rowCls} data-testid={`admin-aws-infra-row-${q.key}`}>
                    <td className="px-2 py-1.5 font-mono text-gray-700">{q.queueName}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{q.backlog ?? '—'}</td>
                    <td className={`px-2 py-1.5 text-right tabular-nums ${q.dlqDepth > 0 ? 'text-red-600 font-semibold' : ''}`}>{q.dlqDepth ?? '—'}</td>
                    <td className={`px-2 py-1.5 text-right tabular-nums ${q.consumerErrorRate >= 0.05 ? 'text-red-600 font-semibold' : ''}`}>
                      {typeof q.consumerErrorRate === 'number' ? `${(q.consumerErrorRate * 100).toFixed(1)}%` : '—'}
                    </td>
                    <td className="px-2 py-1.5">
                      <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border ${
                        q.alarmState === 'ALARM'
                          ? 'bg-red-100 text-red-700 border-red-200'
                          : q.alarmState === 'OK'
                            ? 'bg-emerald-100 text-emerald-700 border-emerald-200'
                            : 'bg-gray-100 text-gray-500 border-gray-200'
                      }`}>
                        {q.alarmState || 'UNKNOWN'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[11px] text-gray-400 mt-1">
        Source: CloudWatch (queue depth + Lambda error rate) + composite alarm
        <code className="mx-1 px-1 rounded bg-gray-100">syrabit-workers-degraded</code>.
        On-call paging fans out via the existing
        <a
          href="https://console.aws.amazon.com/sns/v3/home?region=ap-south-1#/topic/ops_alerts"
          target="_blank"
          rel="noopener noreferrer"
          className="text-violet-600 hover:text-violet-700 inline-flex items-center gap-0.5 mx-1"
        >
          ops_alerts SNS topic <ExternalLink size={10} />
        </a>
        — this tile is read-only visibility. Runbook:
        <a
          href="/docs/infra/workers-on-aws"
          className="text-violet-600 hover:text-violet-700 inline-flex items-center gap-0.5 ml-1"
        >
          workers-on-aws.md <ExternalLink size={10} />
        </a>
      </p>
    </div>
  );
}
