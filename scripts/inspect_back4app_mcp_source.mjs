import fs from "node:fs";
import path from "node:path";

const root = path.resolve("node_modules/@back4app/mcp-server-back4app");
const secret = (process.env.BACK4APP_ACCOUNT_KEY || "").trim();
const redact = (text) => secret ? text.split(secret).join("***") : text;
const interesting = /(https?:\/\/|account.?key|authorization|x-parse|token|validate|dashboard|containers|parsecli)/i;
const urlRegex = /https?:\/\/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+/g;

if (!fs.existsSync(root)) {
  console.error("Installed Back4app MCP package was not found.");
  process.exit(1);
}

const files = [];
function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else files.push(full);
  }
}
walk(root);

console.log("=== PACKAGE FILES ===");
for (const file of files) {
  const stat = fs.statSync(file);
  console.log(`${path.relative(root, file)} ${stat.size}`);
}

const urls = new Set();
const snippets = [];
for (const file of files) {
  const stat = fs.statSync(file);
  if (stat.size > 12_000_000) continue;
  const buf = fs.readFileSync(file);
  if (buf.includes(0)) continue;
  const text = buf.toString("utf8");
  for (const match of text.matchAll(urlRegex)) urls.add(match[0]);
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    if (!interesting.test(lines[i])) continue;
    let raw = lines[i].trim();
    if (raw.length > 1200) {
      const matchIndex = raw.search(interesting);
      const start = Math.max(0, matchIndex - 350);
      raw = raw.slice(start, start + 1000);
    }
    snippets.push(redact(`${path.relative(root, file)}:${i + 1}: ${raw}`));
  }
}

console.log("=== STATIC URL LITERALS ===");
for (const url of [...urls].sort()) console.log(redact(url));
console.log("=== AUTH/MANAGEMENT SOURCE SNIPPETS ===");
for (const line of snippets.slice(0, 400)) console.log(line);
console.log(`Inspected ${files.length} files; emitted ${Math.min(snippets.length, 400)} snippets.`);
