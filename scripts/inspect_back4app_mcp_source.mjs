import fs from "node:fs";
import path from "node:path";

const root = path.resolve("node_modules/@back4app/mcp-server-back4app");
const secret = (process.env.BACK4APP_ACCOUNT_KEY || "").trim();
const redact = (text) => secret ? text.split(secret).join("***") : text;
const interesting = /(https?:\/\/|account.?key|authorization|x-parse|token|validate|dashboard|containers)/i;
const urlRegex = /https?:\/\/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+/g;

const files = [];
function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (/\.(?:js|mjs|cjs|ts|json)$/i.test(entry.name)) files.push(full);
  }
}

if (!fs.existsSync(root)) {
  console.error("Installed Back4app MCP package was not found.");
  process.exit(1);
}
walk(root);

const urls = new Set();
const snippets = [];
for (const file of files) {
  const stat = fs.statSync(file);
  if (stat.size > 2_000_000) continue;
  const text = fs.readFileSync(file, "utf8");
  for (const match of text.matchAll(urlRegex)) urls.add(match[0]);
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    if (!interesting.test(lines[i])) continue;
    const rel = path.relative(root, file);
    const snippet = `${rel}:${i + 1}: ${lines[i].trim()}`;
    if (snippet.length <= 700) snippets.push(redact(snippet));
  }
}

console.log("=== STATIC URL LITERALS ===");
for (const url of [...urls].sort()) console.log(redact(url));
console.log("=== AUTH/MANAGEMENT SOURCE SNIPPETS ===");
for (const line of snippets.slice(0, 250)) console.log(line);
console.log(`Inspected ${files.length} package files; emitted ${Math.min(snippets.length, 250)} snippets.`);
