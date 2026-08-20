/**
 * SSE 运行流 hook：订阅 /api/threads/{id}/runs/{runId}/stream，
 * reducer 按事件类型维护运行视图（计划 / 工具卡 / 审批 / 状态）。
 * 服务端以 Last-Event-ID(=seq) 幂等补拉，EventSource 原生支持断线重连语义。
 */
import { useEffect, useReducer } from "react";
import { adminQueryParam } from "../api/client";
import type {
  InterruptView,
  PlanStepView,
  PlatformEvent,
  ToolCallView,
} from "../api/types";

export type RunStreamStatus =
  | "idle"
  | "streaming"
  | "interrupted"
  | "done"
  | "failed";

export interface RunStreamState {
  events: PlatformEvent[];
  plan: PlanStepView[] | null;
  toolCalls: Record<string, ToolCallView>;
  interrupt: InterruptView | null;
  status: RunStreamStatus;
  error: string | null;
}

type Action = { type: "@@reset" } | { type: "@@event"; event: PlatformEvent };

const INITIAL: RunStreamState = {
  events: [],
  plan: null,
  toolCalls: {},
  interrupt: null,
  status: "idle",
  error: null,
};

function updateStep(
  plan: PlanStepView[] | null,
  index: number,
  patch: Partial<PlanStepView>,
): PlanStepView[] | null {
  if (!plan || index < 0 || index >= plan.length) return plan;
  return plan.map((s, i) => (i === index ? { ...s, ...patch } : s));
}

function reducer(state: RunStreamState, action: Action): RunStreamState {
  if (action.type === "@@reset") return INITIAL;
  const evt = action.event;
  if (state.events.some((e) => e.seq === evt.seq)) return state; // seq 幂等
  const events = [...state.events, evt];
  const p = evt.payload as Record<string, any>;
  switch (evt.type) {
    case "run_start":
      return { ...INITIAL, events, status: "streaming" };
    case "plan_created":
      return { ...state, events, plan: (p.steps as PlanStepView[]) ?? [], status: "streaming" };
    case "step_start":
      return { ...state, events, plan: updateStep(state.plan, p.index as number, { status: "in_progress" }) };
    case "step_done":
      return { ...state, events, plan: updateStep(state.plan, p.index as number, { status: "done" }) };
    case "step_failed":
      return { ...state, events, plan: updateStep(state.plan, p.index as number, { status: "failed" }) };
    case "tool_call_start":
      return {
        ...state,
        events,
        toolCalls: {
          ...state.toolCalls,
          [p.id as string]: {
            id: p.id as string,
            name: p.name as string,
            server: p.server as string,
            status: "running",
            args: p.args,
          },
        },
      };
    case "tool_call_end": {
      const id = p.id as string;
      const prev = state.toolCalls[id];
      if (!prev) return { ...state, events };
      return {
        ...state,
        events,
        toolCalls: {
          ...state.toolCalls,
          [id]: {
            ...prev,
            status: (p.status === "succeeded" ? "succeeded" : "failed") as ToolCallView["status"],
            duration_ms: p.duration_ms as number,
            truncated: p.truncated as boolean,
            error: p.error as string,
          },
        },
      };
    }
    case "interrupt":
      return {
        ...state,
        events,
        interrupt: { pending: (p.pending as InterruptView["pending"]) ?? [] },
        status: "interrupted",
      };
    case "resumed":
      return { ...state, events, interrupt: null, status: "streaming" };
    case "run_end":
      return { ...state, events, status: p.status === "failed" ? "failed" : "done" };
    case "error":
      return { ...state, events, error: (p.message as string) ?? "未知错误" };
    default:
      return { ...state, events };
  }
}

export function useRunStream(threadId: string | null, runId: string | null) {
  const [state, dispatch] = useReducer(reducer, INITIAL);

  useEffect(() => {
    if (!threadId || !runId) {
      dispatch({ type: "@@reset" });
      return;
    }
    dispatch({ type: "@@reset" });
    const url = adminQueryParam(`/api/threads/${threadId}/runs/${runId}/stream`);
    const es = new EventSource(url);
    es.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data) as PlatformEvent;
        dispatch({ type: "@@event", event: evt });
        if (evt.type === "run_end") es.close(); // 服务端 run_end 后关闭流
      } catch {
        /* 忽略心跳/非 JSON 帧 */
      }
    };
    return () => es.close();
  }, [threadId, runId]);

  return state;
}

/** 从事件流拼装实时回答文本（finalizer 优先，executor 兜底）。 */
export function assembleAnswer(events: PlatformEvent[]): string {
  const parts: string[] = [];
  for (const e of events) {
    if (e.type === "llm_delta" && (e.payload.node === "finalizer" || e.payload.node === "executor")) {
      parts.push(String(e.payload.delta ?? ""));
    }
  }
  return parts.join("");
}
