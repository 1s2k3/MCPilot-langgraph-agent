import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { McpServerView } from "../api/types";

const EMPTY_FORM = {
  name: "",
  transport: "streamable_http" as "stdio" | "streamable_http",
  command: "",
  args: "",
  url: "",
  headers: "",
  tool_allowlist: "",
  enabled: true,
};

/** MCP Server 管理页：CRUD + 连接测试 + 工具预览（§5.2）。 */
export default function McpPage() {
  const [servers, setServers] = useState<McpServerView[]>([]);
  const [editing, setEditing] = useState<McpServerView | "new" | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);

  const load = useCallback(async () => {
    setServers(await api<McpServerView[]>("/api/mcp-servers"));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function openForm(server: McpServerView | "new") {
    if (server === "new") {
      setForm(EMPTY_FORM);
    } else {
      setForm({
        name: server.name,
        transport: server.transport === "stdio" ? "stdio" : "streamable_http",
        command: server.command ?? "",
        args: (server.args ?? []).join(" "),
        url: server.url ?? "",
        headers: "",
        tool_allowlist: (server.tool_allowlist ?? []).join(", "),
        enabled: server.enabled,
      });
    }
    setTestResult(null);
    setEditing(server);
  }

  async function save() {
    const payload = {
      name: form.name,
      transport: form.transport,
      command: form.command || null,
      args: form.args ? form.args.split(/\s+/) : [],
      url: form.url || null,
      headers: form.headers
        ? Object.fromEntries(
            form.headers.split("\n").map((line) => {
              const [k, ...rest] = line.split(":");
              return [k.trim(), rest.join(":").trim()];
            }),
          )
        : null,
      tool_allowlist: form.tool_allowlist
        ? form.tool_allowlist.split(",").map((s) => s.trim()).filter(Boolean)
        : null,
      enabled: form.enabled,
    };
    const url = editing === "new" ? "/api/mcp-servers" : `/api/mcp-servers/${(editing as McpServerView).id}`;
    await api(url, {
      method: editing === "new" ? "POST" : "PATCH",
      body: JSON.stringify(payload),
    });
    setEditing(null);
    await load();
  }

  async function test(id: string) {
    const res = await fetch(`/api/mcp-servers/${id}/test`, { method: "POST" });
    setTestResult((await res.json()) as Record<string, unknown>);
  }

  async function remove(id: string) {
    if (!confirm("确认删除该 MCP server？")) return;
    await api(`/api/mcp-servers/${id}`, { method: "DELETE" });
    await load();
  }

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">MCP Servers</h1>
        <button
          className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
          onClick={() => openForm("new")}
        >
          + 接入 Server
        </button>
      </div>

      <div className="space-y-2">
        {servers.map((s) => (
          <div key={s.id} className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="font-medium">{s.name}</span>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                  {s.transport}
                </span>
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] ${
                    s.health === "ok"
                      ? "bg-emerald-100 text-emerald-700"
                      : s.health === "error"
                        ? "bg-red-100 text-red-700"
                        : "bg-slate-100 text-slate-500"
                  }`}
                >
                  {s.health === "ok" ? "● 在线" : s.health === "error" ? "● 失联" : "○ 未知"}
                </span>
                {!s.enabled && (
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                    已禁用
                  </span>
                )}
              </div>
              <div className="flex gap-2">
                <button
                  className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                  onClick={() => void test(s.id)}
                >
                  连接测试
                </button>
                <button
                  className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
                  onClick={() => openForm(s)}
                >
                  编辑
                </button>
                <button
                  className="rounded-lg border border-red-200 px-2.5 py-1 text-xs text-red-500 hover:bg-red-50"
                  onClick={() => void remove(s.id)}
                >
                  删除
                </button>
              </div>
            </div>
            <div className="mt-1 truncate text-xs text-slate-400">
              {s.transport === "stdio"
                ? `${s.command ?? ""} ${(s.args ?? []).join(" ")}`
                : s.url ?? ""}
            </div>
            {testResult && testResult.server === s.name && (
              <div className="mt-2 rounded-lg bg-slate-50 p-2 text-xs">
                {testResult.ok === true ? (
                  <>
                    <span className="text-emerald-600">✓ 连接成功，导出工具:</span>
                    <ul className="mt-1 grid grid-cols-2 gap-1">
                      {(testResult.tools as { name: string; description: string }[]).map((t) => (
                        <li key={t.name} className="truncate font-mono text-[11px]">
                          {t.name}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <span className="text-red-600">
                    ✗ 连接失败: {String(testResult.error ?? "未知错误")}
                  </span>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40">
          <div className="max-h-[90vh] w-full max-w-xl overflow-auto rounded-xl bg-white p-5 shadow-xl">
            <h2 className="mb-3 text-base font-semibold">
              {editing === "new" ? "接入 MCP Server" : `编辑 ${editing.name}`}
            </h2>
            <div className="space-y-3 text-sm">
              <input
                className="w-full rounded-lg border border-slate-200 p-2"
                placeholder="名称（工具前缀，如 mcp-demo）"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                disabled={editing !== "new"}
              />
              <select
                className="w-full rounded-lg border border-slate-200 p-2"
                value={form.transport}
                onChange={(e) =>
                  setForm({
                    ...form,
                    transport: e.target.value as "stdio" | "streamable_http",
                  })
                }
              >
                <option value="streamable_http">streamable_http（远程 HTTP）</option>
                <option value="stdio">stdio（本地子进程）</option>
              </select>
              {form.transport === "stdio" ? (
                <>
                  <input
                    className="w-full rounded-lg border border-slate-200 p-2 font-mono"
                    placeholder="command，如 node"
                    value={form.command}
                    onChange={(e) => setForm({ ...form, command: e.target.value })}
                  />
                  <input
                    className="w-full rounded-lg border border-slate-200 p-2 font-mono"
                    placeholder="args（空格分隔），如 index.js"
                    value={form.args}
                    onChange={(e) => setForm({ ...form, args: e.target.value })}
                  />
                </>
              ) : (
                <>
                  <input
                    className="w-full rounded-lg border border-slate-200 p-2 font-mono"
                    placeholder="url，如 http://localhost:8001/mcp"
                    value={form.url}
                    onChange={(e) => setForm({ ...form, url: e.target.value })}
                  />
                  <textarea
                    className="w-full rounded-lg border border-slate-200 p-2 font-mono text-xs"
                    rows={2}
                    placeholder={"headers（每行 Key: Value，加密存储）"}
                    value={form.headers}
                    onChange={(e) => setForm({ ...form, headers: e.target.value })}
                  />
                </>
              )}
              <input
                className="w-full rounded-lg border border-slate-200 p-2 font-mono text-xs"
                placeholder="工具白名单（逗号分隔，留空=全部）"
                value={form.tool_allowlist}
                onChange={(e) => setForm({ ...form, tool_allowlist: e.target.value })}
              />
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
                onClick={() => setEditing(null)}
              >
                取消
              </button>
              <button
                className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
                onClick={() => void save()}
              >
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
