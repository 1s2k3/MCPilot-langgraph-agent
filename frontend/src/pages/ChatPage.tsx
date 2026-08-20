import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  AgentConfig,
  MessageView,
  RunView,
  Thread,
} from "../api/types";
import InspectorDrawer from "../components/InspectorDrawer";
import PermissionDialog from "../components/PermissionDialog";
import PlanPanel from "../components/PlanPanel";
import ToolCallCard from "../components/ToolCallCard";
import { assembleAnswer, useRunStream } from "../hooks/useRunStream";

const ACTIVE = new Set(["pending", "running", "interrupted"]);

/** 会话主页面：线程列表 + 流式对话 + 计划/工具卡 + HITL 弹窗 + Inspector。 */
export default function ChatPage() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageView[]>([]);
  const [input, setInput] = useState("");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [dismissedInterrupt, setDismissedInterrupt] = useState<string | null>(null);
  const [newThreadAgent, setNewThreadAgent] = useState<string>("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const stream = useRunStream(threadId, activeRunId);
  const liveAnswer = assembleAnswer(stream.events);

  const loadThreads = useCallback(async () => {
    setThreads(await api<Thread[]>("/api/threads"));
  }, []);

  const loadMessages = useCallback(async (tid: string) => {
    setMessages(await api<MessageView[]>(`/api/threads/${tid}/messages`));
  }, []);

  useEffect(() => {
    void loadThreads();
    void api<AgentConfig[]>("/api/agents").then(setAgents).catch(() => {});
  }, [loadThreads]);

  useEffect(() => {
    if (threadId) void loadMessages(threadId);
  }, [threadId, loadMessages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, stream.events.length]);

  // 切换线程：恢复活动 run（页面刷新/重进场景）
  useEffect(() => {
    if (!threadId) return;
    setActiveRunId(null);
    setDismissedInterrupt(null);
    void api<RunView[]>(`/api/threads/${threadId}/runs`).then((runs) => {
      const active = runs.find((r) => ACTIVE.has(r.status));
      if (active) setActiveRunId(active.id);
    }).catch(() => {});
  }, [threadId]);

  // run 结束：刷新消息历史
  useEffect(() => {
    if (stream.status === "done" || stream.status === "failed") {
      if (threadId) void loadMessages(threadId);
    }
  }, [stream.status, threadId, loadMessages]);

  async function send() {
    if (!threadId || !input.trim()) return;
    const text = input.trim();
    setInput("");
    const body = await api<{ run_id: string }>(`/api/threads/${threadId}/runs`, {
      method: "POST",
      body: JSON.stringify({ input: text }),
    });
    setActiveRunId(body.run_id);
  }

  async function createThread() {
    const t = await api<Thread>("/api/threads", {
      method: "POST",
      body: JSON.stringify({ agent_id: newThreadAgent || null }),
    });
    await loadThreads();
    setThreadId(t.id);
  }

  const toolCallValues = Object.values(stream.toolCalls);
  const showPermission =
    stream.interrupt != null && dismissedInterrupt !== activeRunId;

  return (
    <div className="flex h-full min-h-0">
      {/* 线程列表 */}
      <nav className="flex w-56 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="border-b border-slate-100 p-2">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-500">会话</span>
            <button
              className="rounded bg-blue-600 px-2 py-0.5 text-xs text-white hover:bg-blue-700"
              onClick={() => void createThread()}
            >
              + 新建
            </button>
          </div>
          <select
            className="w-full rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-600"
            value={newThreadAgent}
            onChange={(e) => setNewThreadAgent(e.target.value)}
            title="新建会话使用的 Agent"
          >
            <option value="">默认 Agent</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {threads.map((t) => (
            <button
              key={t.id}
              onClick={() => setThreadId(t.id)}
              className={`block w-full truncate px-3 py-2 text-left text-sm ${
                t.id === threadId
                  ? "bg-blue-50 text-blue-700"
                  : "text-slate-600 hover:bg-slate-50"
              }`}
            >
              {t.title || "（新会话）"}
            </button>
          ))}
        </div>
      </nav>

      {/* 对话区 */}
      <main className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {messages.map((m) => (
            <div
              key={m.id}
              className={`mb-3 flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[75%] whitespace-pre-wrap rounded-xl px-3.5 py-2.5 text-sm ${
                  m.role === "user"
                    ? "bg-blue-600 text-white"
                    : "bg-white text-slate-800 shadow-sm ring-1 ring-slate-100"
                }`}
              >
                {m.content}
              </div>
            </div>
          ))}

          {activeRunId && (
            <div className="mb-3">
              <div className="mb-1 text-[11px] text-slate-400">
                运行 <span className="font-mono">{activeRunId.slice(0, 8)}</span>
                {stream.status === "interrupted" && (
                  <span className="ml-2 text-red-500">⏸ 等待审批</span>
                )}
                {stream.status === "streaming" && (
                  <span className="ml-2 animate-pulse text-blue-500">执行中…</span>
                )}
              </div>
              <PlanPanel plan={stream.plan} />
              {toolCallValues.map((c) => (
                <ToolCallCard key={(c as { id: string }).id} call={c as never} />
              ))}
              {(liveAnswer || stream.status === "streaming") && (
                <div className="whitespace-pre-wrap rounded-xl bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm ring-1 ring-slate-100">
                  {liveAnswer}
                  {stream.status === "streaming" && (
                    <span className="animate-pulse text-blue-400">▌</span>
                  )}
                </div>
              )}
              {stream.error && (
                <div className="mt-2 rounded-lg bg-red-50 p-2 text-xs text-red-600">
                  {stream.error}
                </div>
              )}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* 输入区 */}
        <div className="border-t border-slate-200 bg-white p-3">
          <div className="flex gap-2">
            <input
              className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
              placeholder="输入任务，Enter 发送…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <button
              className="rounded-lg bg-blue-600 px-4 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
              disabled={!threadId || !input.trim() || stream.status === "streaming" || stream.status === "interrupted"}
              onClick={() => void send()}
            >
              发送
            </button>
            <button
              className="rounded-lg border border-slate-200 px-3 text-sm text-slate-600 hover:bg-slate-50"
              onClick={() => setInspectorOpen(!inspectorOpen)}
            >
              {inspectorOpen ? "收起" : "检查器"}
            </button>
          </div>
        </div>
      </main>

      {/* Inspector 抽屉 */}
      <InspectorDrawer
        events={stream.events}
        threadId={threadId}
        toolCalls={stream.toolCalls as Record<string, unknown>}
        open={inspectorOpen}
        onClose={() => setInspectorOpen(false)}
      />

      {showPermission && stream.interrupt && (
        <PermissionDialog
          threadId={threadId!}
          runId={activeRunId!}
          interrupt={stream.interrupt}
          onResolved={() => setDismissedInterrupt(activeRunId)}
        />
      )}
    </div>
  );
}
