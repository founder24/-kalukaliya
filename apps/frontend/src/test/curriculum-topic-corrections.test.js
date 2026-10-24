import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

const migrationPaths = [
  resolve(
    process.cwd(),
    "../api/drizzle/migrations/0045_repair_reviewed_topic_content.sql",
  ),
  resolve(
    process.cwd(),
    "../api/drizzle/migrations/0046_add_verified_ahsec_federalism_topic.sql",
  ),
];
const migrations = migrationPaths.map((migrationPath) =>
  readFileSync(migrationPath, "utf8"),
);

function applyMigrationToFixture() {
  const python = String.raw`
import json, sqlite3, sys
db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE subjects (id TEXT PRIMARY KEY)")
db.execute("INSERT INTO subjects VALUES ('6a19e0eb4d8e6ddb2deb7d4f')")
db.execute("CREATE TABLE chapters (id TEXT PRIMARY KEY, subject_id TEXT, title TEXT, slug TEXT, chapter_number INTEGER, status TEXT, content_type TEXT, published_topics TEXT, updated_at INTEGER)")
rows = [
  ("6a19e0d84d8e6ddb2deb7d07", "Motion in a Plane", "motion-in-a-plane", 3, [{"id":"topic-6a19e0d84d8e6ddb2deb7d07-en-250","language":"English","topic_slug":"vector-and-scalar-foundations-invariance-and-algebraic-rules","definition":"PHYSICAL QUANTITIES +---+ SCALARS VECTORS"}]),
  ("6a19e0f44d8e6ddb2deb7d6f", "Forms of Business Organisation", "forms-of-business-organisation", 2, [{"id":"topic-6a19e0f44d8e6ddb2deb7d6f-as-22","language":"Assamese","topic_slug":"ব্যৱসায়-সংগঠনৰ-প্ৰকাৰসমূহ","definition":"শিকাৰ উদ্দেশ্য: কেৱল লক্ষ্য আৰু উদাহৰণ"}]),
  ("6a1f19fbcee3667f3ecc3be9", "Nationalism", "nationalism", 7, [{"id":"topic-6a1f19fbcee3667f3ecc3be9-en-297","language":"English","topic_slug":"challenges-to-federalism","definition":"Nigeria federalism"}]),
]
for chapter_id, title, slug, number, topics in rows:
  db.execute("INSERT INTO chapters VALUES (?, '6a19e0eb4d8e6ddb2deb7d4f', ?, ?, ?, 'published', 'standard', ?, 0)", (chapter_id, title, slug, number, json.dumps(topics, ensure_ascii=False)))
for migration_path in sys.argv[1:]:
  db.executescript(open(migration_path, encoding="utf-8").read())
print(json.dumps({row[0]: {"title":row[1],"slug":row[2],"chapter_number":row[3],"status":row[4],"topics":json.loads(row[5])} for row in db.execute("SELECT id,title,slug,chapter_number,status,published_topics FROM chapters")}, ensure_ascii=False))
`;
  const output = execFileSync("python3", ["-c", python, ...migrationPaths, ...migrationPaths], {
    encoding: "utf8",
  });
  return JSON.parse(output);
}

describe("reviewed curriculum topic corrections", () => {
  it("applies the forward migration to exact reviewed records", () => {
    const result = applyMigrationToFixture();
    const physics = result["6a19e0d84d8e6ddb2deb7d07"].topics[0].definition;
    const business = result["6a19e0f44d8e6ddb2deb7d6f"].topics[0].definition;
    const nationalism = result["6a1f19fbcee3667f3ecc3be9"].topics;
    const federalismChapter = result["bb6e8973ef5d3ba462932b3c"];

    expect(physics).toContain("A scalar quantity has magnitude only");
    expect(physics).toContain("triangle law");
    expect(physics).toContain("𝐯");
    expect(physics).not.toMatch(/PHYSICAL QUANTITIES \+|I \{\\text\{net\}\}/);

    for (const form of [
      "একক মালিকীস্বত্ব",
      "অংশীদাৰী",
      "যৌথ হিন্দু পৰিয়াল",
      "সমবায় সমিতি",
      "যৌথ মূলধনী কোম্পানী",
    ]) {
      expect(business).toContain(form);
    }
    expect(business).not.toContain("শিকাৰ উদ্দেশ্য");
    expect(nationalism.some((topic) => topic.topic_slug === "challenges-to-federalism")).toBe(false);

    expect(federalismChapter.title).toBe("Federalism");
    expect(federalismChapter.slug).toBe("federalism");
    expect(federalismChapter.chapter_number).toBe(7);
    expect(federalismChapter.status).toBe("published");
    expect(federalismChapter.topics).toHaveLength(1);
    expect(federalismChapter.topics[0].topic_slug).toBe("challenges-to-federalism");
    expect(federalismChapter.topics[0].definition).toContain("Yoruba, Ibo, and Hausa-Fulani");
    expect(federalismChapter.topics[0].definition).toContain("oil revenues");
  });

  it("renders the corrected explanations as readable plain HTML text", () => {
    const result = applyMigrationToFixture();
    const definitions = [
      result["6a19e0d84d8e6ddb2deb7d07"].topics[0].definition,
      result["6a19e0f44d8e6ddb2deb7d6f"].topics[0].definition,
      result["bb6e8973ef5d3ba462932b3c"].topics[0].definition,
    ];
    const html = definitions
      .map((definition) => renderToStaticMarkup(React.createElement("p", null, definition)))
      .join("");

    expect(html).toContain("A scalar quantity has magnitude only");
    expect(html).toContain("ব্যৱসায়ৰ আকাৰ");
    expect(html).not.toContain("<code>");
    expect(html).not.toContain("<table>");
  });

  it("records the official source citations and keeps the change forward-only", () => {
    const allMigrations = migrations.join("\n");
    expect(allMigrations).toContain("https://ncert.nic.in/textbook/pdf/keph103.pdf");
    expect(allMigrations).toContain("https://ncert.nic.in/textbook/pdf/kebs102.pdf");
    expect(allMigrations).toContain("https://ncert.nic.in/textbook/pdf/keps207.pdf");
    expect(allMigrations).toContain("https://ahsec.assam.gov.in/Class_XI_textbook");
    expect(allMigrations).toContain("https://ahsec.assam.gov.in/wp-content/uploads/2025/06/Indian-constitution-at-work_XI-Freebooks.pdf");
    expect(allMigrations).toContain("does not belong under Nationalism");
    expect(allMigrations).not.toMatch(/UPDATE\s+.*004[0-4]/i);
  });
});
