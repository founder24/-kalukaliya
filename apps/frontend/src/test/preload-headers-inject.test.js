import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import preloadHeadersInjectPlugin from "../../vite-plugins/preload-headers-inject.js";

describe("build preload header isolation", () => {
  it("keeps document hints but detaches them from immutable assets after all additions", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "preload-headers-"));
    try {
      const headersPath = path.join(dir, "_headers");
      fs.writeFileSync(headersPath, '/*\n  X-Test: preserved\n  Link: <https://syrabit.ai/llms.txt>; rel="service-doc"\n\n/assets/*\n  Cache-Control: public, max-age=31536000, immutable\n');
      const plugin = preloadHeadersInjectPlugin();
      const context = { warn() {}, error(message) { throw new Error(message); } };
      for (const hash of ["old123", "new456"]) {
        plugin.writeBundle.call(context, { dir }, { [`assets/index-${hash}.js`]: {} });
      }
      const output = fs.readFileSync(headersPath, "utf8");
      expect(output).toContain("Link: </assets/index-new456.js>; rel=preload");
      expect(output).not.toContain("index-old123.js");
      expect(output).toContain("X-Test: preserved");
      expect(output).toContain('Link: <https://syrabit.ai/llms.txt>; rel="service-doc"');
      expect(output).toContain("Cache-Control: public, max-age=31536000, immutable");
      expect(output.trimEnd()).toMatch(/\/assets\/\*\n  ! Link$/);
      expect(output.lastIndexOf("! Link")).toBeGreaterThan(output.lastIndexOf("  Link:"));
      expect(output.match(/! Link/g)).toHaveLength(1);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
