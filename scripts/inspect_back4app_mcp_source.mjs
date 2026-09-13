import fs from "node:fs";
import path from "node:path";

const root = path.resolve("node_modules/@back4app/mcp-server-back4app");
const secret = (process.env.BACK4APP_ACCOUNT_KEY || "").trim();
const redact = (text) => secret ? text.split(secret).join("***") : text;
const terms = [
  "BACK4APP_API_URL",
  "ACCOUNT_KEY",
  "Validating account key",
  "parsecli.back4app.com",
  "Authorization",
  "accountKey",
  "account-key",
  "X-Parse",
  "fetch(",
];

if (!fs.existsSync(root)) {
  console.error("Installed Back4app MCP package was not found.");
  process.exit(1);
}

function emitWindows(label, text) {
  const seen = new Set();
  for (const term of terms) {
    let from = 0;
    while (true) {
      const idx = text.indexOf(term, from);
      if (idx < 0) break;
      const start = Math.max(0, idx - 900);
      const end = Math.min(text.length, idx + term.length + 1400);
      const key = `${start}:${end}`;
      if (!seen.has(key)) {
        seen.add(key);
        const window = text.slice(start, end).replace(/\s+/g, " ");
        console.log(`--- ${label} around ${term} @ ${idx} ---`);
        console.log(redact(window));
      }
      from = idx + term.length;
      if (seen.size >= 80) return;
    }
  }
}

const mapPath = path.join(root, "dist/index.js.map");
if (fs.existsSync(mapPath)) {
  const map = JSON.parse(fs.readFileSync(mapPath, "utf8"));
  console.log(`Source map sources: ${(map.sources || []).length}`);
  for (let i = 0; i < (map.sourcesContent || []).length; i++) {
    const content = map.sourcesContent[i];
    if (typeof content !== "string") continue;
    if (!terms.some((term) => content.includes(term))) continue;
    emitWindows(map.sources?.[i] || `source-${i}`, content);
  }
} else {
  console.log("No source map found; scanning bundle.");
  emitWindows("dist/index.js", fs.readFileSync(path.join(root, "dist/index.js"), "utf8"));
}
