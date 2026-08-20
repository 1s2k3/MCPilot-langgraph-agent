import { useState } from "react";
import type { ToolCallView } from "../api/types";

const STATUS_ICON: Record<ToolCallView["status"], string> = {
  running: "⏳",
  succeeded: "✅",
  failed: "❌",
};

function JsonBlock({ value, label }: { value: unknown; label: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="text-xs">
      <button
        className="text-slate-500 hover:text-slate-800"
        onClick={() => setOpen(!open)}
      >
        {open ? "▾" : "▸"} {label}
      </button>
      {open && (
        <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-100 p-2 text-[11px] leading-snug">
          {typeof value === "string" ? value : JSON.stringify(value ?? null, null, 2)}
        </pre>
      )}
    </div>
  );
}

export default function ToolCallCard({
  call,
  result,
}: {
  call: ToolCallView;
  result?: unknown;
}) {
  return (
    <div className="my-2 rounded-lg border border-emerald-200 bg-emerald-50/60 p-2.5 text-sm">
      <div className="flex items-center gap-2">
        <span className={call.status === "running" ? "animate-pulse" : ""}>
          {STATUS_ICON[call.status]}
        </span>
        <span className="font-mono text-xs font-medium text-emerald-800">{call.name}</span>
        {call.server !== "local" && (
          <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-700">
            {call.server}
          </span>
        )}
        {call.duration_ms != null && (
          <span className="text-[10px] text-slate-400">{call.duration_ms}ms</span>
        )}
        {call.truncated && (
          <span className="text-[10px] text-amber-600">结果已截断</span>
        )}
      </div>
      {call.error && <div className="mt-1 text-xs text-red-600">{call.error}</div>}
      {call.args != null && <JsonBlock value={call.args} label="参数" />}
      {result != null && <JsonBlock value={result} label="结果" />}
    </div>
  );
}
