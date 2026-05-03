import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Task #306 regression guard — every Workers AI invocation in the edge
 * worker MUST go through `aiGatewayOpts(env, "<tag>")` so the call is
 * routed via AI Gateway when WORKERS_AI_GATEWAY_ID is set, with a
 * metadata.tag the monthly Cloudflare cost review can group on. A
 * raw `env.AI.run(model, payload)` callsite would silently bypass the
 * tagging and leak Workers AI burn into the un-attributed bucket on
 * the invoice — exactly the failure mode this task is meant to prevent.
 */

/** Strip // line comments and /* block comments *\/ from a source string
 *  so the regex search only sees real code, not docs/comments that
 *  legitimately mention `env.AI.run`. */
function stripComments(src: string): string {
  // Block comments first (non-greedy, multi-line).
  let out = src.replace(/\/\*[\s\S]*?\*\//g, "");
  // Then per-line // comments. Naive but safe for this codebase (no
  // string literals contain "//" inside source we control).
  out = out
    .split("\n")
    .map((l) => l.replace(/\/\/.*$/, ""))
    .join("\n");
  return out;
}

describe("Workers AI invocations are tagged for AI Gateway attribution", () => {
  const sourceFile = join(__dirname, "..", "src", "index.ts");

  it("routes every env.AI.run() through aiGatewayOpts(...)", () => {
    const text = stripComments(readFileSync(sourceFile, "utf8"));
    const lines = text.split("\n");
    const offenders: Array<{ line: number; text: string }> = [];
    for (let i = 0; i < lines.length; i++) {
      if (!/\benv\.AI\.run\s*\(/.test(lines[i])) continue;
      // Walk forward until the call's outer paren closes (naive paren
      // counting; the source is hand-written and well behaved).
      let depth = 0;
      let started = false;
      let snippet = "";
      let end = i;
      for (let j = i; j < Math.min(lines.length, i + 30); j++) {
        for (const ch of lines[j]) {
          if (ch === "(") { depth++; started = true; }
          else if (ch === ")") depth--;
        }
        snippet += lines[j] + "\n";
        end = j;
        if (started && depth === 0) break;
      }
      if (!/aiGatewayOpts\s*\(/.test(snippet)) {
        offenders.push({ line: i + 1, text: snippet.trim() });
      }
      i = end;
    }
    expect(
      offenders,
      `Untagged Workers AI callsites would burn the $5k Cloudflare ` +
        `credit pool without an invoice attribution. Wrap each call ` +
        `in aiGatewayOpts(env, "<tag>") (see docs/cloudflare-cost-map.md):\n` +
        offenders.map((o) => `  index.ts:${o.line}\n    ${o.text}`).join("\n"),
    ).toEqual([]);
  });

  it("uses aiGatewayOpts(...) exactly once per env.AI.run callsite", () => {
    // Symmetry check on comment-stripped source: every aiGatewayOpts use
    // pairs with one env.AI.run, so a future PR that drops a tag (or
    // tags an unrelated call) is caught.
    const text = stripComments(readFileSync(sourceFile, "utf8"));
    const aiRun = (text.match(/\benv\.AI\.run\s*\(/g) ?? []).length;
    // Exclude the function declaration `function aiGatewayOpts(` from
    // the use count.
    const opts = (text.match(/\baiGatewayOpts\s*\(/g) ?? []).length;
    const definitions = (text.match(/\bfunction\s+aiGatewayOpts\s*\(/g) ?? []).length;
    expect(opts - definitions).toBe(aiRun);
  });
});
