export function shouldCollapseEmptyAdSlot({
  adStatus,
  hasCreative,
  timedOut,
}) {
  if (adStatus === 'filled' || hasCreative) return false;
  if (adStatus === 'unfilled') return true;
  return Boolean(timedOut && !hasCreative);
}
