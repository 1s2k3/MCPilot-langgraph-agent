import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { PlatformEvent, RunView } from "../api/types";
import StateInspector from "../components/StateInspector";
import Timeline from "../components/Timeline";
import ToolCallCard from "../components/ToolCallCard";
import type { ToolCallView } from "../api/types";

const TABS = ["Timeline", "State", "Tools"] as const;

/** 单次运行详情页（§8.4）：大尺寸 Timeline + State/Tools Inspector。 */
export default function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<RunView | null>(null);
  const [events, setEvents] = useState<PlatformEvent[]>([]);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Timeline");

  const load = useCallback(async () => {
    if (!runId) return;
    const [r, e] = await Promise.all([
      api<RunView>(`/api/runs/${runId}`),
      api<{ events: PlatformEvent[] }>(`/api/runs/${runId}/events`).then((b) => b.events),
    ]);
    setRun(r);
    setEvents(e);
  }, [runId]);

  useEffect(() => {
    void load();
  }, [load]);

  const toolCalls = useMemo(() => {
    const map: Record<string, ToolCallView> = {};
    for (const e of events) {
      if (e.type === "tool_call_start") {
        map[e.payload.id as string] = {
          id: e.payload.id as string,
          name: e.payload.name as string,
          server: e.payload.server as string,
          status: "running",
          args: e.payload.args,
        };
      } else if (e.type === "tool_call_end" && map[e.payload.id as string]) {
        map[e.payload.id as string] = {
          ...map[e.payload.id as string],
          status: e.payload.status === "succeeded" ? "succeeded" : "failed",
          duration_ms: e.payload.duration_ms as number,
          truncated: e.payload.truncated as boolean,
          error: e.payload.error as string,
        };
      }
    }
    return map;
  }, [events]);

  if (!run) {
    return <div className="p-8 text-sm text-slate-400">加载中…</div>;
  }

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-4">
        <Link to="/" className="text-xs text-blue-500 hover:underline">
          ← 返回会话
        </Link>
        <div className="mt-1 flex items-center gap-3">
          <h1 className="text-lg font-semibold">运行详情</h1>
          <span className="font-mono text-xs text-slate-400">{run.id}</span>
          <span
            className={`rounded px-2 py-0.5 text-xs ${
              run.status === "completed"
                ? "bg-emerald-100 text-emerald-700"
                : run.status === "failed"
                  ? "bg-red-100 text-red-700"
                  : run.status === "interrupted"
                    ? "bg-amber-100 text-amber-700"
                    : "bg-slate-100 text-slate-600"
            }`}
          >
            {run.status}
          </span>
          {run.latency_ms != null && (
            <span className="text-xs text-slate-400">{run.latency_ms}ms</span>
          )}
          {run.usage && (
            <span className="text-xs text-slate-400">
              tokens: in={run.usage.input_tokens ?? 0} out={run.usage.output_tokens ?? 0}
            </span>
          )}
        </div>
        <div className="mt-2 rounded-lg bg-white p-3 text-sm ring-1 ring-slate-100">
          <div className="text-xs text-slate-400">输入</div>
          <div className="whitespace-pre-wrap">{run.input}</div>
        </div>
        {run.final_answer && (
          <div className="mt-2 rounded-lg bg-white p-3 text-sm ring-1 ring-slate-100">
            <div className="text-xs text-slate-400">最终回答</div>
            <div className="whitespace-pre-wrap">{run.final_answer}</div>
          </div>
        )}
        {run.error && (
          <div className="mt-2 rounded-lg bg-red-50 p-3 text-sm text-red-600">
            {String(run.error.message ?? run.error.code ?? "")}
          </div>
        )}
      </div>

      <div className="flex gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-t px-3 py-1.5 text-sm ${
              tab === t
                ? "border border-b-0 border-slate-200 bg-white font-medium"
                : "text-slate-500 hover:bg-slate-50"
            }`}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="rounded-b-xl border border-t-0 border-slate-200 bg-white p-3">
        {tab === "Timeline" && <Timeline events={events} />}
        {tab === "State" && <StateInspector events={events} />}
        {tab === "Tools" && (
          <div className="space-y-2">
            {Object.values(toolCalls).length === 0 && (
              <div className="text-xs text-slate-400">本次运行无工具调用</div>
            )}
            {Object.values(toolCalls).map((c) => (
              <ToolCallCard key={c.id} call={c} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
