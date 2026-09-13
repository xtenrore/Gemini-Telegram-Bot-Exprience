import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const accountKey = (process.env.BACK4APP_ACCOUNT_KEY || "").trim();
if (!accountKey) {
  console.error("BACK4APP_ACCOUNT_KEY is missing.");
  process.exit(1);
}

const transport = new StdioClientTransport({
  command: "npx",
  args: ["-y", "@back4app/mcp-server-back4app@latest"],
  env: { ...process.env, BACK4APP_ACCOUNT_KEY: accountKey },
});

const client = new Client(
  { name: "aircraft-alert-back4app-deployer", version: "3.2.0" },
  { capabilities: {} },
);

try {
  await client.connect(transport);
  const response = await client.listTools();
  const tools = (response.tools || []).map((tool) => ({
    name: tool.name,
    description: tool.description || "",
    inputSchema: tool.inputSchema || {},
  }));
  console.log(`Back4app MCP authenticated. Tools discovered: ${tools.length}`);
  console.log("=== BACK4APP MCP TOOL CATALOG START ===");
  console.log(JSON.stringify(tools, null, 2));
  console.log("=== BACK4APP MCP TOOL CATALOG END ===");
} finally {
  await client.close().catch(() => {});
}
