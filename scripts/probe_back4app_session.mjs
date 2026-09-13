const accountKey = process.env.BACK4APP_ACCOUNT_KEY;
if (!accountKey) throw new Error("BACK4APP_ACCOUNT_KEY is missing");

const auth = await fetch("https://parsecli.back4app.com/accountkey", {
  method: "POST",
  headers: {"content-type": "application/json"},
  body: JSON.stringify({accountKey}),
  redirect: "manual",
});
const authJson = await auth.json().catch(() => ({}));
const setCookie = auth.headers.get("set-cookie") || "";
const cookieName = setCookie ? setCookie.split("=", 1)[0].trim() : "none";
console.log(`accountkey_status=${auth.status}`);
console.log(`accountkey_response_keys=${Object.keys(authJson).sort().join(",") || "none"}`);
console.log(`accountkey_set_cookie=${setCookie ? "yes" : "no"} name=${cookieName}`);

const gqlUrl = "https://api.containers.back4app.com/graphql";
const query = "query Me { me { username } }";

async function probe(label, headers = {}) {
  const r = await fetch(gqlUrl, {
    method: "POST",
    headers: {"content-type": "application/json", ...headers},
    body: JSON.stringify({query}),
    redirect: "manual",
  });
  const j = await r.json().catch(() => ({}));
  const err = Array.isArray(j.errors) && j.errors[0] ? j.errors[0] : null;
  const code = err?.extensions?.code || "none";
  const hasData = !!j.data?.me;
  console.log(`${label}: http=${r.status} gql_code=${code} me_data=${hasData ? "yes" : "no"}`);
  return {r, j};
}

await probe("graphql_no_auth");
await probe("graphql_x_parse_account_key", {"X-Parse-Account-Key": accountKey});
await probe("graphql_x_account_key", {"X-Account-Key": accountKey});
if (setCookie) {
  const cookiePair = setCookie.split(";", 1)[0];
  await probe("graphql_accountkey_cookie", {Cookie: cookiePair});
}
