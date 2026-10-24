const MALFORMED_EQUATIONS = ["AY = 7/5G", "AY = 1/2.6T%G"];

export function assertGovernmentSpendingMultiplierDefinition(definition) {
  if (typeof definition !== "string" || !definition.trim()) {
    throw new Error("Government-spending multiplier topic has no definition.");
  }

  for (const malformed of MALFORMED_EQUATIONS) {
    if (definition.includes(malformed)) {
      throw new Error(`Government-spending multiplier contains malformed equation: ${malformed}`);
    }
  }

  const normalized = definition
    .replace(/[–—]/g, "-")
    .replace(/−/g, "-")
    .replace(/÷/g, "/")
    .replace(/×/g, "*")
    .replace(/\s+/g, " ");
  const requiredPatterns = [
    /ΔY\s*=\s*\(1\s*\/\s*\(1\s*-\s*c\)\)\s*\*\s*ΔG/,
    /ΔY\s*\/\s*ΔG\s*=\s*1\s*\/\s*\(1\s*-\s*c\)/,
    /For c\s*=\s*0\.8[^.]*\b5\b[^.]*\b500\b/i,
    /Source:\s*NCERT.*Introductory Macroeconomics.*Chapter 5.*equations 5\.4-5\.6, page 73/i,
  ];
  if (!requiredPatterns.every((pattern) => pattern.test(normalized))) {
    throw new Error("Government-spending multiplier topic is missing the NCERT-grounded derivation or source.");
  }
  return true;
}
