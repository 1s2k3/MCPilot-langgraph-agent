/**
 * 演示 MCP server：math / time / echo，只读工具面。
 *
 * 两种传输模式：
 * - stdio（默认）：由后端作为子进程拉起（生产 Linux 容器 / CI）
 * - HTTP：设置 MCP_DEMO_PORT 环境变量后监听该端口（Windows 本地开发 / docker compose）
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import http from "node:http";
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

if (process.env.MCP_DEMO_PORT) {
  const port = Number(process.env.MCP_DEMO_PORT);
  const httpServer = http.createServer(async (req, res) => {
    try {
      // 官方 node:http 模式（SDK 文档示例）：无状态 transport 每次请求新建，
      // handleRequest 自行完成响应写入（含长连接 POST 流的渐进式写回）
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const rawBody = Buffer.concat(chunks).toString("utf8");
      const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
      res.on("close", () => transport.close());
      await server.connect(transport);
      await transport.handleRequest(req, res, rawBody ? JSON.parse(rawBody) : undefined);
    } catch (err) {
      res.statusCode = 500;
      res.end(String(err));
    }
  });
  httpServer.listen(port, () => console.error(`[mcp-demo] http 模式监听 :${port}/mcp`));
} else {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}
