import { useState } from "react";
import type { InterruptView } from "../api/types";

interface Props {
  threadId: string;
  runId: string;
  interrupt: InterruptView;
  onResolved: () => void;
}

/** HITL 审批弹窗（§5.9）：approve / deny + 反馈 + 会话级授权。 */
export default function PermissionDialog({ threadId, runId, interrupt, onResolved }: Props) {
  const [feedback, setFeedback] = useState("");
  const [sessionWide, setSessionWide] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function submit(action: "approve" | "deny") {
    setSubmitting(true);
    try {
      const res = await fetch(`/api/threads/${threadId}/runs/${runId}/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, feedback, session_wide: sessionWide }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        alert(body?.error?.message ?? `审批提交失败 (${res.status})`);
        return;
      }
      onResolved();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40">
      <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
        <h2 className="text-base font-semibold text-slate-900">工具执行审批</h2>
        <p className="mt-1 text-xs text-slate-500">
          Agent 请求执行以下工具，需要你的批准：
        </p>
        <div className="mt-3 space-y-2">
          {interrupt.pending.map((item) => (
            <div key={item.id} className="rounded-lg border border-red-200 bg-red-50 p-3">
              <div className="font-mono text-sm font-medium text-red-800">{item.name}</div>
              <pre className="mt-1 max-h-32 overflow-auto text-[11px] text-slate-600">
                {JSON.stringify(item.args, null, 2)}
              </pre>
            </div>
          ))}
        </div>
        <textarea
          className="mt-3 w-full rounded-lg border border-slate-200 p-2 text-sm"
          rows={2}
          placeholder="反馈（可选，拒绝时告知 Agent 原因）"
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
        />
        <label className="mt-2 flex items-center gap-2 text-xs text-slate-600">
          <input
            type="checkbox"
            checked={sessionWide}
            onChange={(e) => setSessionWide(e.target.checked)}
          />
          本次会话内不再询问该工具
        </label>
        <div className="mt-4 flex justify-end gap-2">
          <button
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-50"
            disabled={submitting}
            onClick={() => submit("deny")}
          >
            拒绝
          </button>
          <button
            className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            disabled={submitting}
            onClick={() => submit("approve")}
          >
            批准
          </button>
        </div>
      </div>
    </div>
  );
}
