import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ApiKeyView } from "../api/types";

/** API Key 管理页（§9）：写后不可读，只展示掩码。 */
export default function KeysPage() {
  const [keys, setKeys] = useState<ApiKeyView[]>([]);
  const [provider, setProvider] = useState("anthropic");
  const [name, setName] = useState("");
  const [key, setKey] = useState("");

  const load = useCallback(async () => {
    setKeys(await api<ApiKeyView[]>("/api/api-keys"));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function create() {
    if (!name.trim() || !key.trim()) return;
    await api("/api/api-keys", {
      method: "POST",
      body: JSON.stringify({ provider, name, key }),
    });
    setName("");
    setKey("");
    await load();
  }

  async function remove(id: string) {
    if (!confirm("确认删除该密钥？")) return;
    await api(`/api/api-keys/${id}`, { method: "DELETE" });
    await load();
  }

  return (
    <div className="mx-auto max-w-3xl p-6">
      <h1 className="mb-4 text-lg font-semibold">API Keys</h1>
      <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4">
        <div className="mb-2 text-sm font-medium text-slate-600">新增密钥（加密存储，写后不可读）</div>
        <div className="flex gap-2">
          <select
            className="rounded-lg border border-slate-200 px-2 py-1.5 text-sm"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="anthropic">anthropic</option>
            <option value="langsmith">langsmith</option>
          </select>
          <input
            className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm"
            placeholder="名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <input
            className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-1.5 font-mono text-sm"
            placeholder="sk-…"
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
          <button
            className="rounded-lg bg-blue-600 px-4 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            disabled={!name.trim() || !key.trim()}
            onClick={() => void create()}
          >
            保存
          </button>
        </div>
      </div>

      <div className="space-y-2">
        {keys.map((k) => (
          <div
            key={k.id}
            className="flex items-center justify-between rounded-xl border border-slate-200 bg-white p-4"
          >
            <div>
              <div className="text-sm font-medium">{k.name}</div>
              <div className="mt-0.5 text-xs text-slate-400">
                <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono">
                  {k.provider}
                </span>
                <span className="ml-2">{k.masked}</span>
                {k.last_used_at && (
                  <span className="ml-2">最近使用: {k.last_used_at.slice(0, 19).replace("T", " ")}</span>
                )}
              </div>
            </div>
            <button
              className="rounded-lg border border-red-200 px-3 py-1 text-sm text-red-500 hover:bg-red-50"
              onClick={() => void remove(k.id)}
            >
              删除
            </button>
          </div>
        ))}
        {keys.length === 0 && (
          <div className="py-8 text-center text-sm text-slate-400">
            暂无密钥（LLM 调用可回退到环境变量 ANTHROPIC_API_KEY）
          </div>
        )}
      </div>
    </div>
  );
}
