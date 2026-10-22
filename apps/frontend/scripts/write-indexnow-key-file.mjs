import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_OUTPUT_DIR = path.resolve(__dirname, "..", "dist");
const SAFE_INDEXNOW_KEY = /^[A-Za-z0-9_-]{8,128}$/;

export function validateIndexNowKey(key = process.env.INDEXNOW_API_KEY) {
  if (!key) {
    throw new Error("INDEXNOW_API_KEY is required");
  }
  if (!SAFE_INDEXNOW_KEY.test(key)) {
    throw new Error("INDEXNOW_API_KEY must contain 8-128 letters, numbers, hyphens, or underscores");
  }
  return key;
}

export function writeIndexNowKeyFile(
  outputDir = DEFAULT_OUTPUT_DIR,
  key = process.env.INDEXNOW_API_KEY,
) {
  const safeKey = validateIndexNowKey(key);
  const resolvedOutputDir = path.resolve(outputDir);
  fs.mkdirSync(resolvedOutputDir, { recursive: true });
  const outputPath = path.join(resolvedOutputDir, `${safeKey}.txt`);
  fs.writeFileSync(outputPath, safeKey, { encoding: "utf8", mode: 0o644 });
  return outputPath;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv[2] === "--validate") {
      validateIndexNowKey();
      console.log("[indexnow-key-file] Release key format is valid");
    } else {
      writeIndexNowKeyFile(process.argv[2] || DEFAULT_OUTPUT_DIR);
      console.log("[indexnow-key-file] Generated public ownership file in the release artifact");
    }
  } catch (error) {
    console.error(`[indexnow-key-file] ${error.message}`);
    process.exitCode = 1;
  }
}
