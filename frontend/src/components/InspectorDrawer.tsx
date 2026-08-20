import { useState } from "react";
import type { PlatformEvent } from "../api/types";
import MemoryList from "./MemoryList";
import StateInspector from "./StateInspector";
import Timeline from "./Timeline";
import ToolCallCard from "./ToolCallCard";

const TABS = ["Timeline", "State", "Tools", "Memory"] as const;
type Tab = (typeof TABS)[number];

/** 右侧 Inspector 抽屉：Timeline / State / Tools / Memory 四 Tab（§8.4）。 */
export default function InspectorDrawer({
  events,
  threadId,
  toolCalls,
  open,
  onClose,
}: {
  events: PlatformEvent[];
  threadId: string | null;
  toolCalls: Record<string, unknown>;
  open: boolean;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("Timeline");
  if (!open) return null;
  return (
    <aside className="flex w-96 shrink-0 flex-col border-l border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-100 px-2">
        <div className="flex gap-1 py-1.5">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded px-2.5 py-1 text-xs ${
                tab === t
                  ? "bg-slate-800 text-white"
                  : "text-slate-500 hover:bg-slate-100"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        <button className="px-2 text-slate-400 hover:text-slate-700" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {tab === "Timeline" && <Timeline events={events} />}
        {tab === "State" && <StateInspector events={events} />}
        {tab === "Tools" && (
          <div className="space-y-2 p-2">
            {Object.values(toolCalls).length === 0 && (
              <div className="text-xs text-slate-400">本次运行暂无工具调用</div>
            )}
            {Object.values(toolCalls).map((c) => (
              <ToolCallCard key={(c as { id: string }).id} call={c as never} />
            ))}
          </div>
        )}
        {tab === "Memory" && <MemoryList threadId={threadId} />}
      </div>
    </aside>
  );
}
