const base = "https://containers.back4app.com";
const html = await (await fetch(`${base}/apps`)).text();
const srcs = [...html.matchAll(/<script[^>]+src=["']([^"']+)["']/gi)].map(m => m[1]);
const scripts = [...new Set(srcs)].map(src => src.startsWith("http") ? src : new URL(src, base).href);
console.log(`dashboard_scripts=${scripts.length}`);

const terms = [
  "https://api.containers.back4app.com",
  "api.containers.back4app.com",
  "connect.sid",
  "sessionId",
  "sessionID",
  "accountKey",
  "accountkey",
  "login",
  "me=function",
  "query Me",
  "query me",
  "findRepositories",
  "repositories(",
  "repositoryBranches",
  "createAppFromRepository",
  "updateAppSettings",
];

function snippet(text, pos, term) {
  return text.slice(Math.max(0, pos - 1800), Math.min(text.length, pos + term.length + 3200)).replace(/\s+/g, " ").slice(0, 5200);
}

for (const url of scripts) {
  const response = await fetch(url);
  if (!response.ok) continue;
  const text = await response.text();
  console.log(`=== SCRIPT ${url} bytes=${text.length} ===`);
  let printed = 0;
  for (const term of terms) {
    let pos = 0;
    let perTerm = 0;
    while ((pos = text.indexOf(term, pos)) >= 0 && perTerm < 6 && printed < 90) {
      console.log(`--- TERM ${term} @ ${pos} ---`);
      console.log(snippet(text, pos, term));
      pos += term.length;
      perTerm++;
      printed++;
    }
  }
  console.log(`targeted_windows=${printed}`);
}
