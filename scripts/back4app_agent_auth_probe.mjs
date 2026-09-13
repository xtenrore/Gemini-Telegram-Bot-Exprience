const key = (process.env.BACK4APP_ACCOUNT_KEY || "").trim();
if (!key) {
  console.error("BACK4APP_ACCOUNT_KEY is missing.");
  process.exit(1);
}

const endpoint = "https://chatgpt.back4app.com/web-apps";
const candidates = [
  ["authorization_bearer", { Authorization: `Bearer ${key}` }],
  ["x_back4app_account_key", { "X-Back4App-Account-Key": key }],
  ["x_parse_account_key", { "X-Parse-Account-Key": key }],
  ["x_account_key", { "X-Account-Key": key }],
  ["account_key", { "Account-Key": key }],
];

let success = null;
for (const [name, headers] of candidates) {
  try {
    const response = await fetch(endpoint, {
      method: "GET",
      headers: { Accept: "application/json", ...headers },
      redirect: "manual",
    });
    console.log(`Back4app Agent auth probe ${name}: HTTP ${response.status}`);
    if (response.status === 200 && !success) {
      success = name;
    }
    // Consume the body without printing it; it may contain user/account metadata.
    await response.arrayBuffer();
  } catch (error) {
    console.log(`Back4app Agent auth probe ${name}: ${error?.name || "request_error"}`);
  }
}

if (!success) {
  console.error("No tested Account Key header authenticated the Back4app Agent Containers API.");
  process.exit(2);
}
console.log(`Back4app Agent Containers API authentication method verified: ${success}`);
