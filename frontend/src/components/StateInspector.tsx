import { useMemo, useState } from "react";
import type { PlatformEvent } from "../api/types";

interface Snapshot {
  node: string;
  state: Record<string, unknown>;
  seq: number;
}

function keyDiff(prev: Record<string, unknown>, next: Record<string, unknown>): Set<string> {
  const changed = new Set<string>();
  for (const [k, v] of Object.entries(next)) {
    if (!(k in prev) || JSON.stringify(prev[k]) !== JSON.stringify(v)) changed.add(k);
  }
  return changed;
}

/** State Inspector（§8.4）：按节点查看 state_snapshot，与上一节点 diff 高亮。 */
export default function StateInspector({ events }: { events: PlatformEvent[] }) {
  const snapshots = useMemo<Snapshot[]>(
    () =>
      events
        .filter((e) => e.type === "state_snapshot")
        .map((e) => ({
          node: String(e.payload.node ?? "?"),
          state: (e.payload.state ?? {}) as Record<string, unknown>,
          seq: e.seq,
        })),
    [events],
  );
  const [selected, setSelected] = useState<number | null>(null);

  if (snapshots.length === 0) {
    return <div className="p-3 text-xs text-slate-400">暂无状态快照（运行后生成）</div>;
  }
  const idx = selected ?? snapshots.length - 1;
  const snap = snapshots[idx];
  const prev = idx > 0 ? snapshots[idx - 1].state : {};
  const changed = keyDiff(prev, snap.state);

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap gap-1 border-b border-slate-100 p-2">
        {snapshots.map((s, i) => (
          <button
            key={s.seq}
            onClick={() => setSelected(i)}
            className={`rounded px-2 py-0.5 text-[11px] ${
              i === idx ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {s.node}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2">
        <div className="mb-1 text-[11px] text-slate-400">
          节点 <span className="font-medium text-slate-600">{snap.node}</span> 的状态快照
          {changed.size > 0 && (
            <span className="ml-2 text-amber-600">（{changed.size} 个键相对上一节点变化，已高亮）</span>
          )}
        </div>
        <pre className="text-[11px] leading-relaxed">
          {Object.entries(snap.state).map(([k, v]) => (
            <div
              key={k}
              className={`rounded px-1 ${
                changed.has(k) ? "bg-amber-50" : ""
              }`}
            >
              <span className="font-semibold text-blue-700">{k}:</span>{" "}
              <span className="text-slate-700">{JSON.stringify(v, null, 2)}</span>
            </div>
          ))}
        </pre>
      </div>
    </div>
  );
}
