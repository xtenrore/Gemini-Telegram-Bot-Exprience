import fs from "node:fs";

const base = "https://containers.back4app.com";
const html = await (await fetch(`${base}/apps`)).text();
console.log(`dashboard_html_bytes=${html.length}`);
const srcs = [...html.matchAll(/<script[^>]+src=["']([^"']+)["']/gi)].map(m => m[1]);
const scripts = [...new Set(srcs)].map(src => src.startsWith("http") ? src : new URL(src, base).href);
console.log(`dashboard_scripts=${scripts.length}`);

const terms = [
  "repositoryOwnerLogin",
  "web-apps",
  "deployment",
  "containers.back4app.com",
  "chatgpt.back4app.com",
  "parsecli.back4app.com",
  "X-Parse-Account-Key",
  "X-Account-Key",
  "Authorization",
  "accountKey",
  "github-installation",
  "environmentVariables",
  "healthCheckPath",
  "/apps",
];

function windows(label, text) {
  const output = [];
  for (const term of terms) {
    let pos = 0;
    while ((pos = text.indexOf(term, pos)) >= 0) {
      const start = Math.max(0, pos - 700);
      const end = Math.min(text.length, pos + term.length + 1200);
      let snippet = text.slice(start, end).replace(/\s+/g, " ");
      if (snippet.length > 2200) snippet = snippet.slice(0, 2200);
      output.push({term, pos, snippet});
      pos += term.length;
      if (output.length >= 80) return output;
    }
  }
  return output;
}

let hits = 0;
for (let i = 0; i < scripts.length && i < 80; i++) {
  const url = scripts[i];
  try {
    const response = await fetch(url);
    if (!response.ok) {
      console.log(`script_http_${response.status}: ${url}`);
      continue;
    }
    const text = await response.text();
    const found = windows(url, text);
    if (!found.length) continue;
    console.log(`=== SCRIPT ${url} bytes=${text.length} hits=${found.length} ===`);
    for (const item of found) {
      console.log(`--- ${item.term} @ ${item.pos} ---`);
      console.log(item.snippet);
      hits++;
    }
  } catch (error) {
    console.log(`script_error ${url}: ${error?.name || "error"}`);
  }
}
console.log(`dashboard_interesting_windows=${hits}`);
if (hits === 0) process.exitCode = 2;
