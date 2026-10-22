import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  validateIndexNowKey,
  writeIndexNowKeyFile,
} from "../../scripts/write-indexnow-key-file.mjs";
import { verifyKeyFile } from "../../scripts/indexnow-submit.mjs";

const tempDirs = [];

function createTempDir() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "syrabit-indexnow-key-"));
  tempDirs.push(directory);
  return directory;
}

afterEach(() => {
  for (const directory of tempDirs.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

describe("IndexNow ownership file generation", () => {
  it("writes the configured key into the release output without requiring a committed source file", () => {
    const outputDir = createTempDir();
    const outputPath = writeIndexNowKeyFile(outputDir, "release-key-2026");

    expect(path.basename(outputPath)).toBe("release-key-2026.txt");
    expect(fs.readFileSync(outputPath, "utf8")).toBe("release-key-2026");
  });

  it("rejects a missing key", () => {
    expect(() => validateIndexNowKey("")).toThrow("INDEXNOW_API_KEY is required");
  });

  it("rejects keys that could escape the output directory", () => {
    expect(() => validateIndexNowKey("../unsafe-key")).toThrow(
      "INDEXNOW_API_KEY must contain 8-128 letters, numbers, hyphens, or underscores",
    );
  });

  it("lets the source preflight defer file verification until the release artifact is built", () => {
    expect(() =>
      verifyKeyFile(createTempDir(), "release-key-2026", { allowMissing: true }),
    ).not.toThrow();
  });

  it("generates and verifies an ownership file when checking the release artifact", () => {
    const outputDir = createTempDir();

    verifyKeyFile(outputDir, "release-key-2026", { generateIfMissing: true });

    expect(fs.readFileSync(path.join(outputDir, "release-key-2026.txt"), "utf8")).toBe(
      "release-key-2026",
    );
  });
});
