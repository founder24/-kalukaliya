// Shared policy for the release hydration verifier.

export const HYDRATION_PATTERNS = [
  /Hydration failed/i,
  /hydrating but the server rendered/i,
  /did not match/i,
  /Text content does not match/i,
  /Text content did not match/i,
  /Hydration completed but contains mismatches/i,
  /There was an error while hydrating/i,
  /server rendered HTML didn't match the client/i,
  /Minified React error #418/i,
  /Minified React error #421/i,
  /Minified React error #422/i,
  /Minified React error #423/i,
  /Minified React error #425/i,
  /reactjs\.org\/docs\/error-decoder\.html\?invariant=(?:418|421|422|423|425)/i,
  /react\.dev\/errors\/(?:418|421|422|423|425)/i,
];

export function looksLikeHydrationProblem(text) {
  if (!text) return false;
  return HYDRATION_PATTERNS.some((pattern) => pattern.test(text));
}

export function browserIssuesForTarget(target, messages) {
  return messages.filter(
    (message) =>
      looksLikeHydrationProblem(message.text) ||
      (target.kind === "static" && message.type === "pageerror"),
  );
}