// Task #842 — shared caption helpers for AdminHealth cron pills.
//
// The cf-waf-drift and Trustpilot refresh-cron wrappers both build
// their `renderSubText` output the same way: a primary line that
// reports a heartbeat age (with a fallback when no heartbeat is
// known), optionally joined to extra context with " · ". Tasks #836
// and #838 left two copies of the prose ("Last heartbeat … ago" /
// "No heartbeat recorded yet" and "Last successful heartbeat … ago"
// / "No successful heartbeat recorded") and the join character
// inline in each wrapper. Hoisting them here means a third or
// fourth cron pill can compose its caption from the same primitives
// without copy-pasting the join logic or the empty-state fallbacks.

// Render one heartbeat-age line. When `ageLabel` is a non-empty
// string (e.g. "1h", "5m", "2d"), returns "<prefix> <ageLabel> ago";
// otherwise returns the `fallback` string verbatim. The wrappers'
// previous inline ternaries used the exact same shape — keeping it
// identical here so visual output is unchanged.
export function captionLine(prefix, ageLabel, fallback) {
  return ageLabel ? `${prefix} ${ageLabel} ago` : fallback;
}

// Join caption parts with " · ", dropping any empty / nullish parts
// so an absent suffix never renders as an orphan separator (the
// trustpilot test explicitly guards against `recorded · ` slipping
// through). Empty input yields an empty string, matching the
// previous behaviour where a missing suffix collapsed to "".
export function joinCaptionParts(parts) {
  return (parts || []).filter(Boolean).join(' · ');
}

// Compact "Ns / Nm / Nh / Nd" age formatter, kept identical to the
// helper exported from CronHealthPill so captions composed here look
// the same as captions composed via the renderSubText `ageLabel: fmt`
// context arg. Local copy avoids a circular dependency between this
// helpers file and the pill component.
function _ageLabel(secs) {
  if (secs == null) return null;
  const s = Math.max(0, Math.floor(Number(secs)));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

// Task #902 — compose the alerter-state caption that renders below
// the per-pill subText (cf. CronHealthPill). The shape comes from
// `/admin/health/<pill>/cron/alert-state` (see
// routes/admin_health.py::_build_alert_state_response):
//
//   * present (bool) — does the alerter's lock doc exist?
//   * lastAlertAt (ISO|null) — when the alerter last paged.
//   * lastAlertAgeSeconds (int|null) — derived from lastAlertAt.
//   * inDebounce (bool) — last_state is broken/silent AND the page
//                          is inside the realert window.
//   * debounceRemainingSeconds (int|null) — realert_interval -
//                                           lastAlertAgeSeconds.
//
// We render nothing when there's no recorded page (a brand-new
// deployment with a healthy pill should not show a misleading
// "last paged" line). When there IS a recorded page we always show
// "last paged Xh ago"; if we're still inside the debounce window we
// append "in debounce ~Yh remaining" so admins can tell apart "I
// can re-page now" from "the next page is auto-suppressed for
// another Yh". Returns null when nothing should render so callers
// can short-circuit the surrounding <p> wrapper.
export function formatAlertStateCaption(alertState) {
  if (!alertState || !alertState.present || !alertState.lastAlertAt) {
    return null;
  }
  const lastLbl = _ageLabel(alertState.lastAlertAgeSeconds);
  const lastPaged = lastLbl ? `last paged ${lastLbl} ago` : 'last paged: just now';
  if (
    alertState.inDebounce
    && alertState.debounceRemainingSeconds != null
  ) {
    const remLbl = _ageLabel(alertState.debounceRemainingSeconds);
    if (remLbl) {
      return `${lastPaged} · in debounce ~${remLbl} remaining`;
    }
  }
  return lastPaged;
}
