import { DollarSign, TrendingUp, Target, Zap, AlertTriangle } from 'lucide-react';
import {
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar, LineChart, Line, Cell,
} from 'recharts';
import { Card, Stat, TT, PLAN_COLORS, fmt, fmtInr } from './shared';
import CurrencyProvenanceCaption, { breakdownTooltip } from './CurrencyProvenanceCaption';

// Task #740 — custom Recharts tooltip that prints the daily INR total
// AND the per-currency provenance line (USD slice + FX rate + source)
// so an admin hovering a data point sees exactly which currencies
// rolled into that day's number — not just a renamed series label.
function RevenueChartTooltip({ active, payload, label, breakdown }) {
  if (!active || !payload || !payload.length) return null;
  const v = payload[0].value;
  const usdLine = breakdownTooltip(breakdown);
  return (
    <div style={TT.contentStyle}>
      <div className="font-medium text-gray-900">{label}</div>
      <div className="text-emerald-600 mt-0.5">
        Revenue: ₹{Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}
      </div>
      {usdLine && (
        <div className="text-[10px] text-gray-500 mt-1 leading-snug max-w-[260px]">
          {usdLine}
        </div>
      )}
    </div>
  );
}

export default function RevenueTab({ widgetErrors, load, mrr, predicted, growth, arpu, ltv, paidUsers, dailyRev, cohortData, predict, revenue, rangeDays = 30 }) {
  const rangeLabel = rangeDays === 1 ? 'Today' : `Last ${rangeDays} Days`;
  const breakdown = revenue?.currency_breakdown;
  return (
    <div className="space-y-4">
      {widgetErrors.revenue && (
        <div className="flex items-center gap-3 p-3.5 rounded-xl" style={{
          background: 'rgba(245,158,11,0.06)',
          border: '1px solid rgba(245,158,11,0.15)',
        }}>
          <AlertTriangle size={14} className="text-amber-700 flex-shrink-0" />
          <p className="text-xs text-amber-700/80 flex-1">Revenue data failed to load.</p>
          <button onClick={() => load(true)} className="text-xs text-amber-700 hover:text-gray-900 px-2.5 py-1 rounded-lg transition-colors"
            style={{ background: 'rgba(245,158,11,0.12)' }}>Retry</button>
        </div>
      )}
      {breakdown ? (
        <CurrencyProvenanceCaption breakdown={breakdown} className="px-1" />
      ) : (
        <p className="text-[11px] text-gray-400 px-1">
          Includes Razorpay (INR) + Stripe (USD→INR via daily ECB rate). Per-row provenance on the Monetization page.
        </p>
      )}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat icon={DollarSign} label="MRR (30d)"     value={fmtInr(mrr)}       color="#10b981" trend={growth} />
        <Stat icon={TrendingUp} label="Predicted MRR" value={fmtInr(predicted)} color="#7c3aed"
          sub={growth >= 0 ? `${growth}% MoM growth` : `${Math.abs(growth)}% MoM decline`} />
        <Stat icon={Target}     label="ARPU"          value={fmtInr(arpu)}       color="#f59e0b"
          sub={paidUsers > 0 ? `${paidUsers} paid users` : 'No paid users yet'} />
        <Stat icon={Zap}        label="LTV (12-mo)"   value={fmtInr(ltv)}        color="#06b6d4" sub="Avg lifetime value" />
      </div>

      <Card title={`Daily Revenue — ${rangeLabel}`}
        empty={!dailyRev.length} emptyMsg="No payment data yet">
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={dailyRev} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f9fafb" />
            <XAxis dataKey="date" tick={{ fill: '#4b5563', fontSize: 11 }} tickFormatter={fmt} />
            <YAxis tick={{ fill: '#4b5563', fontSize: 11 }} tickFormatter={v => `₹${v}`} />
            <Tooltip cursor={TT.cursor} content={<RevenueChartTooltip breakdown={breakdown} />} />
            <Line type="monotone" dataKey="revenue_inr" name="Revenue ₹" stroke="#10b981" strokeWidth={2.5}
              dot={{ r: 3, fill: '#10b981' }} activeDot={{ r: 5 }} />
          </LineChart>
        </ResponsiveContainer>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="Users by Plan" empty={!cohortData.length} emptyMsg="No cohort data yet">
          {cohortData.length > 0 && (
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={cohortData} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f9fafb" />
                <XAxis dataKey="plan" tick={{ fill: '#4b5563', fontSize: 11 }} />
                <YAxis tick={{ fill: '#4b5563', fontSize: 11 }} allowDecimals={false} />
                <Tooltip {...TT} />
                <Bar dataKey="count" name="Users" radius={[4, 4, 0, 0]}>
                  {cohortData.map((entry, i) => (
                    <Cell key={i} fill={PLAN_COLORS[entry.plan] || '#8b5cf6'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card title="Revenue Summary">
          <div className="space-y-3">
            {[
              { label: 'Payments (this month)', value: predict?.payments_this_month || 0, color: '#10b981' },
              { label: 'Payments (last month)',  value: predict?.payments_last_month  || 0, color: '#64748b' },
              { label: 'Signups (this month)',   value: predict?.signups_this_month   || 0, color: '#7c3aed' },
              { label: 'Signups (last month)',   value: predict?.signups_last_month   || 0, color: '#64748b' },
              { label: `Total payments (${rangeDays === 1 ? 'today' : `${rangeDays}d`})`, value: revenue?.total_payments || 0, color: '#f59e0b' },
            ].map((item, i) => (
              <div key={i} className="flex items-center justify-between p-2 rounded-lg" style={{
                background: i % 2 === 0 ? '#f9fafb' : 'transparent',
              }}>
                <span className="text-gray-600 text-sm">{item.label}</span>
                <span className="font-semibold text-sm" style={{ color: item.color }}>{item.value.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
