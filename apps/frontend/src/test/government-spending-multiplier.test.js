import { execFileSync } from "node:child_process";
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  assertGovernmentSpendingMultiplierDefinition,
} from "../../scripts/_government-spending-multiplier.mjs";

const migrationPath = path.resolve(
  process.cwd(),
  "../api/drizzle/migrations/0047_correct_government_spending_multiplier.sql",
);
const malformedDefinition =
  "The government spending multiplier is a measure. Subtracting equations gives AY = 7/5G. This can be rewritten as AY = 1/2.6T%G.";
const verifiedDefinition =
  "NCERT equation (5.4): Y* = (1 ÷ (1 − c)) × (C̄ − cT + cTR̄ + I + G). " +
  "Equation (5.4a): (Y* + ΔY) = (1 ÷ (1 − c)) × (C̄ − cT + cTR̄ + I + G + ΔG). " +
  "Equation (5.5): ΔY = (1 ÷ (1 − c)) × ΔG. " +
  "Equation (5.6): ΔY ÷ ΔG = 1 ÷ (1 − c). " +
  "Source: NCERT, Introductory Macroeconomics, Class XII, Chapter 5, “Government Budget and the Economy,” " +
  "section “Changes in Government Expenditure,” equations (5.4), (5.4a), (5.5), and (5.6), " +
  "p. 73 (Reprint 2026–27). https://ncert.nic.in/textbook/pdf/leec105.pdf";

function runMigrationFixture() {
  const python = String.raw`
import json, sqlite3, sys
db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE chapters (id TEXT PRIMARY KEY, subject_id TEXT, title TEXT, slug TEXT, chapter_number INTEGER, status TEXT, content_type TEXT, published_topics TEXT, updated_at INTEGER)")
corrupt = "The government spending multiplier is a measure. Subtracting equations gives AY = 7/5G. This can be rewritten as AY = 1/2.6T%G."
def topic(definition, topic_id="topic-6a1f87832c1a19d2142de410-en-430"):
  return {"id": topic_id, "language": "English", "title": "Government Spending Multiplier", "topic_slug": "government-spending-multiplier", "definition": definition, "definition_status": "ok"}
canonical_id = "6a1f87832c1a19d2142de410"
db.execute("INSERT INTO chapters VALUES (?, 'economics', 'Government Budget and the Economy', 'government-budget-and-the-economy', 5, 'published', 'standard', ?, 0)", (canonical_id, json.dumps([topic(corrupt)])))
db.execute("INSERT INTO chapters VALUES ('other-chapter', 'economics', 'Government Budget and the Economy', 'government-budget-and-the-economy', 5, 'published', 'standard', ?, 0)", (json.dumps([topic(corrupt)]),))
db.execute("INSERT INTO chapters VALUES ('unpublished-copy', 'economics', 'Government Budget and the Economy', 'government-budget-and-the-economy', 5, 'draft', 'standard', ?, 0)", (json.dumps([topic(corrupt)]),))
db.execute("INSERT INTO chapters VALUES ('topic-mismatch', 'economics', 'Government Budget and the Economy', 'government-budget-and-the-economy', 5, 'published', 'standard', ?, 0)", (json.dumps([topic(corrupt, "different-topic-id")]),))
sql = open(sys.argv[1], encoding="utf-8").read()
db.executescript(sql)
changes_after_first_run = db.total_changes
db.executescript(sql)
changes_after_second_run = db.total_changes
rows = {row[0]: {"title": row[1], "slug": row[2], "status": row[3], "topics": json.loads(row[4])}
        for row in db.execute("SELECT id, title, slug, status, published_topics FROM chapters")}
print(json.dumps({"rows": rows, "changesAfterFirstRun": changes_after_first_run, "changesAfterSecondRun": changes_after_second_run}, ensure_ascii=False))
`;
  const output = execFileSync("python3", ["-c", python, migrationPath], {
    encoding: "utf8",
  });
  return JSON.parse(output);
}

describe("government-spending multiplier lesson", () => {
  it("uses the NCERT derivation and source in the new forward migration", () => {
    const migration = fs.readFileSync(migrationPath, "utf8");
    const match = migration.match(/\]\.definition',\s*\n\s*'([\s\S]*?)'\s*\)/);

    expect(match).not.toBeNull();
    expect(assertGovernmentSpendingMultiplierDefinition(match[1])).toBe(true);
    expect(migration).toContain("chapter.slug = 'government-budget-and-the-economy'");
    expect(migration).toContain("chapter.id = '6a1f87832c1a19d2142de410'");
    expect(migration).toContain("https://ncert.nic.in/textbook/pdf/leec105.pdf");
  });

  it.each(["AY = 7/5G", "AY = 1/2.6T%G"])(
    "rejects malformed equation %s",
    (malformed) => {
      const content = `${verifiedDefinition} ${malformed}`;
      expect(() => assertGovernmentSpendingMultiplierDefinition(content)).toThrow(malformed);
    },
  );

  it("applies only to the canonical published chapter/topic, and is idempotent", () => {
    const result = runMigrationFixture();
    const corrected = result.rows["6a1f87832c1a19d2142de410"].topics[0].definition;

    expect(assertGovernmentSpendingMultiplierDefinition(corrected)).toBe(true);
    expect(corrected).not.toContain("AY = 7/5G");
    expect(corrected).not.toContain("AY = 1/2.6T%G");
    expect(result.rows["other-chapter"].topics[0].definition).toBe(malformedDefinition);
    expect(result.rows["unpublished-copy"].topics[0].definition).toBe(malformedDefinition);
    expect(result.rows["topic-mismatch"].topics[0].definition).toBe(malformedDefinition);
    expect(result.changesAfterFirstRun).toBe(result.changesAfterSecondRun);
  });

  it("keeps every NCERT equation and its full source citation as readable HTML text", () => {
    const html = renderToStaticMarkup(React.createElement("p", null, verifiedDefinition));

    for (const text of [
      "Y* = (1 ÷ (1 − c)) × (C̄ − cT + cTR̄ + I + G)",
      "(Y* + ΔY) = (1 ÷ (1 − c)) × (C̄ − cT + cTR̄ + I + G + ΔG)",
      "ΔY = (1 ÷ (1 − c)) × ΔG",
      "ΔY ÷ ΔG = 1 ÷ (1 − c)",
      "https://ncert.nic.in/textbook/pdf/leec105.pdf",
    ]) {
      expect(html).toContain(text);
    }
  });

  it("fails closed when a prerendered source omits the corrected derivation", () => {
    expect(() => assertGovernmentSpendingMultiplierDefinition(
      "Government spending changes aggregate demand.",
    )).toThrow(/missing the NCERT-grounded derivation or source/);
  });
});
