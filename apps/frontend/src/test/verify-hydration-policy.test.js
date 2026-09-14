import { describe, expect, it } from "vitest";
import { browserIssuesForTarget } from "../../scripts/verify-hydration-policy.mjs";

describe("release hydration browser policy", () => {
  it("fails a static route when the page raises an uncaught error", () => {
    const issues = browserIssuesForTarget(
      { kind: "static", route: "/terms" },
      [{ type: "pageerror", text: "static route crashed during mount" }],
    );

    expect(issues).toEqual([
      { type: "pageerror", text: "static route crashed during mount" },
    ]);
  });

  it("does not treat a page error on a prerendered content route as a static-route failure", () => {
    const issues = browserIssuesForTarget(
      { kind: "chapter", route: "/ahsec/physics/chapter-1" },
      [{ type: "pageerror", text: "client-only chapter warning" }],
    );

    expect(issues).toHaveLength(0);
  });

  it("keeps hydration mismatch detection enabled for every route kind", () => {
    const issues = browserIssuesForTarget(
      { kind: "static", route: "/login" },
      [{ type: "warning", text: "Hydration failed because the UI did not match" }],
    );

    expect(issues).toHaveLength(1);
  });
});