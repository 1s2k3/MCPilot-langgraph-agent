import { useCallback, useEffect, useState } from "react";
import type { MemoryView } from "../api/types";

interface Props {
  threadId?: string | null;
}

/** Memory 面板（§8.4）：列表 + 语义搜索 + 删除（可溯源到 source_run）。 */
export default function MemoryList({ threadId }: Props) {
  const [memories, setMemories] = useState<MemoryView[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const base = threadId ? `/api/threads/${threadId}/memories` : "/api/memories";
      const url = query ? `${base}?q=${encodeURIComponent(query)}` : base;
      const res = await fetch(url);
      if (res.ok) {
        const body = (await res.json()) as { memories: MemoryView[] };
        setMemories(body.memories);
      }
    } finally {
      setLoading(false);
    }
  }, [threadId, query]);

  useEffect(() => {
    const t = setTimeout(load, query ? 300 : 0);
    return () => clearTimeout(t);
  }, [load, query]);

  async function remove(id: string) {
    if (!confirm("确认删除这条记忆？")) return;
    const res = await fetch(`/api/memories/${id}`, { method: "DELETE" });
    if (res.ok) await load();
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-slate-100 p-2">
        <input
          className="w-full rounded-lg border border-slate-200 px-2 py-1 text-xs"
          placeholder="语义搜索记忆…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <div className="min-h-0 flex-1 space-y-2 overflow-auto p-2">
        {loading && <div className="text-xs text-slate-400">加载中…</div>}
        {!loading && memories.length === 0 && (
          <div className="text-xs text-slate-400">暂无长期记忆</div>
        )}
        {memories.map((m) => (
          <div key={m.id} className="rounded-lg border border-slate-200 bg-white p-2.5">
            <div className="flex items-center justify-between">
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] ${
                  m.type === "preference"
                    ? "bg-pink-100 text-pink-700"
                    : "bg-blue-100 text-blue-700"
                }`}
              >
                {m.type === "preference" ? "偏好" : "事实"}
              </span>
              <div className="flex items-center gap-2">
                {query && "similarity" in (m as unknown as { similarity?: number }) && (
                  <span className="text-[10px] text-slate-400">
                    {(m as unknown as { similarity: number }).similarity.toFixed(2)}
                  </span>
                )}
                <span className="text-[10px] text-slate-400">
                  重要性 {(m.importance * 100).toFixed(0)}%
                </span>
                <button
                  className="text-[10px] text-red-400 hover:text-red-600"
                  onClick={() => remove(m.id)}
                >
                  删除
                </button>
              </div>
            </div>
            <div className="mt-1 text-sm text-slate-700">{m.content}</div>
            {m.source_run_id && (
              <a
                href={`/runs/${m.source_run_id}`}
                className="mt-1 inline-block text-[10px] text-blue-500 hover:underline"
              >
                溯源到运行 ↗
              </a>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
