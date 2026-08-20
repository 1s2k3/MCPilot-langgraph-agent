import { useState } from "react";
import type { PlatformEvent } from "../api/types";

/** 事件类型 → 颜色（与 dataviz 规范一致的品牌中性色板，M7 统一校准）。 */
export const EVENT_COLORS: Record<string, string> = {
  run_start: "bg-slate-500",
  run_end: "bg-slate-700",
  llm_start: "bg-blue-500",
  llm_delta: "bg-blue-400",
  llm_end: "bg-blue-600",
  tool_call_start: "bg-emerald-500",
  tool_call_end: "bg-emerald-600",
  plan_created: "bg-violet-500",
  step_start: "bg-violet-400",
  step_done: "bg-violet-600",
  step_failed: "bg-red-500",
  reflect: "bg-amber-500",
  interrupt: "bg-red-600",
  resumed: "bg-red-400",
  state_snapshot: "bg-slate-400",
  notice: "bg-cyan-500",
  error: "bg-red-700",
};

function eventSummary(evt: PlatformEvent): string {
  const p = evt.payload;
  switch (evt.type) {
    case "llm_delta":
      return String(p.delta ?? "").slice(0, 60);
    case "llm_end": {
      const u = p.usage as Record<string, number> | undefined;
      return u ? `tokens in=${u.input_tokens ?? 0} out=${u.output_tokens ?? 0}` : "";
    }
    case "tool_call_start":
    case "tool_call_end":
      return `${p.name ?? ""} ${evt.type === "tool_call_end" ? `(${p.status ?? ""})` : ""}`;
    case "plan_created":
      return `${((p.steps as unknown[]) ?? []).length} 步计划`;
    case "step_start":
    case "step_done":
    case "step_failed":
      return `步骤 ${Number(p.index) + 1}: ${p.goal ?? ""}`;
    case "reflect":
      return `${p.verdict ?? ""}${p.reason ? ` — ${p.reason}` : ""}`;
    case "interrupt":
      return `待审批: ${((p.pending as { name: string }[]) ?? []).map((x) => x.name).join(", ")}`;
    case "state_snapshot":
      return `节点: ${p.node ?? ""}`;
    case "run_start":
      return String(p.input ?? "").slice(0, 60);
    case "run_end":
      return `${p.status ?? ""}${p.latency_ms != null ? ` · ${p.latency_ms}ms` : ""}`;
    case "error":
      return String(p.message ?? "");
    default:
      return "";
  }
}

/** Agent Timeline：事件轨道（§8.4），点击展开 payload。 */
export default function Timeline({ events }: { events: PlatformEvent[] }) {
  const [expanded, setExpanded] = useState<number | null>(null);
  return (
    <div className="space-y-1 p-1">
      {events.map((evt) => (
        <div key={evt.seq} className="flex items-start gap-2">
          <span
            className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
              EVENT_COLORS[evt.type] ?? "bg-slate-300"
            }`}
          />
          <button
            className="min-w-0 flex-1 text-left text-xs hover:bg-slate-50"
            onClick={() => setExpanded(expanded === evt.seq ? null : evt.seq)}
          >
            <span className="font-mono text-[10px] text-slate-400">#{evt.seq}</span>{" "}
            <span className="font-medium text-slate-700">{evt.type}</span>{" "}
            <span className="truncate text-slate-500">{eventSummary(evt)}</span>
            <span className="ml-2 text-[10px] text-slate-300">
              {evt.ts.slice(11, 19)}
            </span>
            {expanded === evt.seq && (
              <pre className="mt-1 max-h-48 overflow-auto rounded bg-slate-100 p-2 text-[10px]">
                {JSON.stringify(evt.payload, null, 2)}
              </pre>
            )}
          </button>
        </div>
      ))}
    </div>
  );
}
