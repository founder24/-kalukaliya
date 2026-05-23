const fmtInr = (n) =>
  `₹${Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
const fmtUsd = (n) =>
  `$${Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 2 })}`;

function fmtAsOf(iso) {
  if (!iso) return 'unknown';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return 'unknown';
    return d.toISOString().slice(0, 10);
  } catch {
    return 'unknown';
  }
}

// FX is considered stale when:
//   1. The backend suffixes fx_source with `_stale` (its convention when
//      every live FX provider failed and we fell back to the last cached
//      rate — see admin_advanced.py docstring "...e.g. 'frankfurter',
//      'open_er_api', '..._stale'").
//   2. OR fx_fetched_at is older than the threshold below (defensive — covers
//      the case where the backend didn't get a chance to suffix _stale, e.g.
//      payment row written days ago and never re-quoted).
const FX_STALE_HOURS = 24;

export function isFxStale(breakdown) {
  if (!breakdown) return false;
  const usdNative = Number(breakdown.usd_native) || 0;
  // Razorpay-only breakdowns don't depend on an FX rate, so "stale" is
  // not meaningful.
  if (usdNative <= 0) return false;
  const source = String(breakdown.fx_source || '');
  if (source.endsWith('_stale')) return true;
  if (!breakdown.fx_rate) return true; // missing rate on a USD-bearing slice == unusable
  const fetchedAt = breakdown.fx_fetched_at;
  if (!fetchedAt) return true;
  try {
    const ts = new Date(fetchedAt).getTime();
    if (Number.isNaN(ts)) return true;
    const ageHours = (Date.now() - ts) / 36e5;
    return ageHours > FX_STALE_HOURS;
  } catch {
    return true;
  }
}

export function describeBreakdown(breakdown) {
  if (!breakdown) return null;
  const inr = Number(breakdown.inr_native) || 0;
  const usdNative = Number(breakdown.usd_native) || 0;
  const usdAsInr = Number(breakdown.inr_from_usd) || 0;
  if (usdNative <= 0) {
    return `Includes Razorpay only (${fmtInr(inr)}).`;
  }
  const rate = breakdown.fx_rate ? Number(breakdown.fx_rate).toFixed(4) : '—';
  const source = breakdown.fx_source || 'unknown';
  const asOf = fmtAsOf(breakdown.fx_fetched_at);
  return `Includes: Razorpay (${fmtInr(inr)}) + Stripe (${fmtUsd(usdNative)} → ${fmtInr(usdAsInr)} @ rate ${rate}, source: ${source}, as of ${asOf}).`;
}

export function breakdownTooltip(breakdown) {
  if (!breakdown) return '';
  const usdNative = Number(breakdown.usd_native) || 0;
  if (usdNative <= 0) return '';
  const usdAsInr = Number(breakdown.inr_from_usd) || 0;
  const rate = breakdown.fx_rate ? Number(breakdown.fx_rate).toFixed(4) : '—';
  const source = breakdown.fx_source || 'unknown';
  const stale = isFxStale(breakdown) ? ' — FX RATE STALE, refresh upstream provider' : '';
  return `USD slice: ${fmtUsd(usdNative)} → ${fmtInr(usdAsInr)} @ ${rate} (${source})${stale}`;
}

export default function CurrencyProvenanceCaption({ breakdown, className = '' }) {
  const text = describeBreakdown(breakdown);
  if (!text) return null;
  const stale = isFxStale(breakdown);
  return (
    <p
      className={`text-[11px] leading-snug ${stale ? 'text-amber-700' : 'text-gray-500'} ${className}`}
      title={breakdownTooltip(breakdown)}
    >
      {stale && (
        <span
          className="inline-flex items-center gap-1 mr-1 px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-800 font-medium uppercase tracking-wide text-[9px]"
          aria-label="Foreign exchange rate is stale"
        >
          ⚠ FX stale
        </span>
      )}
      {text}
    </p>
  );
}
