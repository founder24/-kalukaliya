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
    /Y\*\s*=\s*\(\s*1\s*\/\s*\(\s*1\s*-\s*c\s*\)\s*\)\s*\*\s*\(C̄\s*-\s*cT\s*\+\s*cTR̄\s*\+\s*I\s*\+\s*G\)/,
    /\(Y\*\s*\+\s*ΔY\)\s*=\s*\(\s*1\s*\/\s*\(\s*1\s*-\s*c\s*\)\s*\)\s*\*\s*\(C̄\s*-\s*cT\s*\+\s*cTR̄\s*\+\s*I\s*\+\s*G\s*\+\s*ΔG\)/,
    /ΔY\s*=\s*\(1\s*\/\s*\(1\s*-\s*c\)\)\s*\*\s*ΔG/,
    /ΔY\s*\/\s*ΔG\s*=\s*1\s*\/\s*\(1\s*-\s*c\)/,
    /Source:\s*NCERT.*Introductory Macroeconomics.*Class XII.*Chapter 5.*Government Budget and the Economy.*Changes in Government Expenditure.*equations \(5\.4\), \(5\.4a\), \(5\.5\), and \(5\.6\), p\. 73.*Reprint 2026-27.*https:\/\/ncert\.nic\.in\/textbook\/pdf\/leec105\.pdf/i,
  ];
  if (!requiredPatterns.every((pattern) => pattern.test(normalized))) {
    throw new Error("Government-spending multiplier topic is missing the NCERT-grounded derivation or source.");
  }
  return true;
}
