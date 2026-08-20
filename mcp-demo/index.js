/**
 * 演示 MCP server：math / time / echo，只读工具面。
 * stdio 传输，由后端作为子进程拉起（见 app/tools/mcp_client.py）。
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const server = new McpServer({ name: "mcp-demo", version: "0.1.0" });

server.tool(
  "math_add",
  "两个数相加",
  { a: z.number().describe("第一个加数"), b: z.number().describe("第二个加数") },
  async ({ a, b }) => ({ content: [{ type: "text", text: String(a + b) }] }),
);

server.tool(
  "math_multiply",
  "两个数相乘",
  { a: z.number().describe("被乘数"), b: z.number().describe("乘数") },
  async ({ a, b }) => ({ content: [{ type: "text", text: String(a * b) }] }),
);

server.tool("get_time", "返回当前 UTC 时间", {}, async () => ({
  content: [{ type: "text", text: new Date().toISOString() }],
}));

server.tool(
  "echo",
  "原样返回输入文本",
  { text: z.string().describe("要回显的文本") },
  async ({ text }) => ({ content: [{ type: "text", text }] }),
);

const transport = new StdioServerTransport();
await server.connect(transport);
