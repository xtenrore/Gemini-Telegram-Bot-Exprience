const base = "https://containers.back4app.com";
const html = await (await fetch(`${base}/apps`)).text();
const srcs = [...html.matchAll(/<script[^>]+src=["']([^"']+)["']/gi)].map(m => m[1]);
const scripts = [...new Set(srcs)].map(src => src.startsWith("http") ? src : new URL(src, base).href);
console.log(`dashboard_scripts=${scripts.length}`);

const terms = [
  "ApolloClient",
  "createHttpLink",
  "HttpLink",
  "GraphQLWsLink",
  "WebSocketLink",
  "graphql-ws",
  "/graphql",
  "graphql",
  "Authorization",
  "Bearer ",
  "accessToken",
  "sessionToken",
  "localStorage",
  "credentials:",
  "setContext",
  "triggerManualDeployment",
  "prepareDeployment",
  "createApp(",
  "mutation CreateApp",
  "mutation createApp",
  "environmentVars",
  "repositoryOwnerLogin",
  "repositoryName",
  "githubInstallation",
  "sourceKind",
];

function snippet(text, pos, term) {
  const start = Math.max(0, pos - 1000);
  const end = Math.min(text.length, pos + term.length + 1800);
  return text.slice(start, end).replace(/\s+/g, " ").slice(0, 3200);
}

for (const url of scripts) {
  const response = await fetch(url);
  if (!response.ok) continue;
  const text = await response.text();
  console.log(`=== SCRIPT ${url} bytes=${text.length} ===`);

  const urls = [...new Set([...text.matchAll(/https?:\\?\/\\?\/[A-Za-z0-9._~:/?#@!$&'()*+,;=%-]+/g)].map(m => m[0].replaceAll("\\/", "/")))]
    .filter(u => /back4app|graphql|b4a/i.test(u));
  console.log("PUBLIC_URLS_START");
  for (const u of urls.slice(0, 100)) console.log(u);
  console.log("PUBLIC_URLS_END");

  let printed = 0;
  for (const term of terms) {
    let pos = 0;
    let perTerm = 0;
    while ((pos = text.indexOf(term, pos)) >= 0 && perTerm < 4 && printed < 80) {
      console.log(`--- TERM ${term} @ ${pos} ---`);
      console.log(snippet(text, pos, term));
      pos += term.length;
      perTerm++;
      printed++;
    }
  }
  console.log(`targeted_windows=${printed}`);
}
