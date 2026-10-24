import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import {
  assertGovernmentSpendingMultiplierDefinition,
} from "../../scripts/_government-spending-multiplier.mjs";

const migrationPath = path.resolve(
  process.cwd(),
  "../api/drizzle/migrations/0044_correct_government_spending_multiplier.sql",
);

describe("government-spending multiplier lesson", () => {
  it("uses the NCERT derivation and source in the forward migration", () => {
    const migration = fs.readFileSync(migrationPath, "utf8");
    const match = migration.match(/\]\.definition',\s*\n\s*'([\s\S]*?)'\s*\)/);

    expect(match).not.toBeNull();
    expect(assertGovernmentSpendingMultiplierDefinition(match[1])).toBe(true);
  });

  it.each(["AY = 7/5G", "AY = 1/2.6T%G"])(
    "rejects malformed equation %s",
    (malformed) => {
      const content = `The correct result is ΔY = (1 ÷ (1 − c)) × ΔG. The multiplier is ΔY ÷ ΔG = 1 ÷ (1 − c). For c = 0.8 the multiplier is 5, and a 100-unit increase raises income by 500 units. Source: NCERT, Introductory Macroeconomics, Chapter 5, equations 5.4–5.6, page 73. ${malformed}`;
      expect(() => assertGovernmentSpendingMultiplierDefinition(content)).toThrow(malformed);
    },
  );

  it("fails closed when a prerendered source omits the corrected derivation", () => {
    expect(() => assertGovernmentSpendingMultiplierDefinition(
      "Government spending changes aggregate demand.",
    )).toThrow(/missing the NCERT-grounded derivation or source/);
  });
});
