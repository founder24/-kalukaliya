import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(__dirname, "..", "dist");
const assetsDir = path.join(distDir, "assets");

const html = fs.readFileSync(path.join(distDir, "index.html"), "utf-8");

const critical = new Set();

const scriptMatches = html.matchAll(/src="(\/assets\/[^"]+\.js)"/g);
for (const m of scriptMatches) {
  if (!m[1].includes("emergent") && !m[1].includes("posthog")) {
    critical.add(m[1]);
  }
}

const cssMatches = html.matchAll(/href="(\/assets\/[^"]+\.css)"/g);
for (const m of cssMatches) {
  critical.add(m[1]);
}

const modulepreloadMatches = html.matchAll(/href="(\/assets\/[^"]+\.js)"/g);
for (const m of modulepreloadMatches) {
  critical.add(m[1]);
}

for (const file of fs.readdirSync(assetsDir)) {
  if (/^(react-dom|vendor|router|query|radix|framer)-[A-Za-z0-9_-]+\.js$/.test(file)) {
    critical.add("/assets/" + file);
  }
}

const result = [...critical];
fs.writeFileSync(
  path.join(distDir, "precache-manifest.json"),
  JSON.stringify(result, null, 2),
);

console.log("precache-manifest.json:", result.length, "entries:", result);
