import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type { AgentConfig } from "../api/types";

interface Dataset {
  id: string;
  name: string;
  description: string;
  entry_count: number;
  created_at: string;
}

interface EvalRunView {
  id: string;
  dataset_id: string;
  agent_id: string | null;
  status: string;
  model_snapshot: { agent?: string; scripted?: boolean };
  metrics: Record<string, number | null>;
  created_at: string;
  finished_at: string | null;
}

interface EvalScoreView {
  entry_index: number;
  input: string;
  trajectory: { tool_sequence: string[]; latency_ms?: number };
  tool_seq_match: { exact: boolean | null; prefix: boolean | null } | null;
  answer_score: number | null;
  judge_reason: string | null;
  error: string | null;
}

const METRIC_LABELS: Record<string, string> = {
  success_rate: "成功率",
  avg_answer_score: "平均评分 (1-5)",
  tool_seq_exact_rate: "工具序列完全匹配率",
  tool_seq_prefix_rate: "工具序列前缀匹配率",
  avg_latency_ms: "平均耗时 (ms)",
  avg_iterations: "平均迭代次数",
  avg_input_tokens: "平均输入 tokens",
  avg_output_tokens: "平均输出 tokens",
  reflection_fix_rate: "反思修正成功率",
};

const HEADLINE_METRICS = ["success_rate", "avg_answer_score", "tool_seq_exact_rate"];

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{value}</div>
    </div>
  );
}

function fmt(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v < 1) return `${(v * 100).toFixed(1)}%`;
  return String(v);
}

/** 评估中心（§11.3 / §8.5）：Dataset 管理 + 运行记录 + 指标 Dashboard。 */
export default function EvalPage() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [runs, setRuns] = useState<EvalRunView[]>([]);
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [scores, setScores] = useState<EvalScoreView[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [showRun, setShowRun] = useState<Dataset | null>(null);
  const [form, setForm] = useState({ name: "", description: "", entries: "" });
  const [runAgentId, setRunAgentId] = useState("");

  const load = useCallback(async () => {
    const [ds, rs, ag] = await Promise.all([
      api<{ datasets: Dataset[] }>("/api/eval/datasets").then((b) => b.datasets),
      api<{ runs: EvalRunView[] }>("/api/eval/runs").then((b) => b.runs),
      api<AgentConfig[]>("/api/agents"),
    ]);
    setDatasets(ds);
    setRuns(rs);
    setAgents(ag);
  }, []);

  useEffect(() => {
    void load();
    // 评估运行中 → 轮询刷新
    const t = setInterval(() => void load(), 3000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (!selectedRunId) return;
    void api<{ scores: EvalScoreView[] }>(`/api/eval/runs/${selectedRunId}/scores`).then((b) =>
      setScores(b.scores),
    );
  }, [selectedRunId]);

  const selectedRun = runs.find((r) => r.id === selectedRunId) ?? null;

  // 图表数据：最近 8 次完成的运行（单指标单轴，时间序）
  const chartData = useMemo(
    () =>
      runs
        .filter((r) => r.status === "completed")
        .slice(0, 8)
        .reverse()
        .map((r, i) => ({
          name: `#${i + 1} ${r.created_at.slice(5, 16).replace("T", " ")}`,
          成功率: r.metrics.success_rate != null ? Number((r.metrics.success_rate * 100).toFixed(1)) : 0,
          runId: r.id,
        })),
    [runs],
  );

  async function createDataset() {
    let entries: unknown[];
    try {
      entries = JSON.parse(form.entries) as unknown[];
      if (!Array.isArray(entries) || entries.length === 0) throw new Error("entries 必须是非空数组");
    } catch (e) {
      alert(`entries 不是合法 JSON 数组: ${(e as Error).message}`);
      return;
    }
    await api("/api/eval/datasets", {
      method: "POST",
      body: JSON.stringify({ name: form.name, description: form.description, entries }),
    });
    setShowCreate(false);
    setForm({ name: "", description: "", entries: "" });
    await load();
  }

  async function startRun() {
    if (!showRun) return;
    await api(`/api/eval/datasets/${showRun.id}/runs`, {
      method: "POST",
      body: JSON.stringify({ agent_id: runAgentId || null }),
    });
    setShowRun(null);
    await load();
  }

  async function removeDataset(id: string) {
    if (!confirm("确认删除该数据集（会级联删除其评估记录）？")) return;
    await api(`/api/eval/datasets/${id}`, { method: "DELETE" });
    await load();
  }

  return (
    <div className="mx-auto max-w-6xl p-6">
      <h1 className="mb-4 text-lg font-semibold">评估中心</h1>

      {/* 数据集 */}
      <section className="mb-6">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-600">数据集</h2>
          <button
            className="rounded-lg bg-blue-600 px-3 py-1 text-xs text-white hover:bg-blue-700"
            onClick={() => setShowCreate(true)}
          >
            + 新建数据集
          </button>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {datasets.map((d) => (
            <div key={d.id} className="rounded-xl border border-slate-200 bg-white p-3">
              <div className="flex items-center justify-between">
                <span className="truncate text-sm font-medium">{d.name}</span>
                <span className="text-[10px] text-slate-400">{d.entry_count} 条</span>
              </div>
              <div className="mt-0.5 truncate text-xs text-slate-400">{d.description}</div>
              <div className="mt-2 flex gap-2">
                <button
                  className="rounded border border-slate-200 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
                  onClick={() => setShowRun(d)}
                >
                  运行评估
                </button>
                <button
                  className="rounded border border-red-200 px-2 py-0.5 text-xs text-red-500 hover:bg-red-50"
                  onClick={() => void removeDataset(d.id)}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
          {datasets.length === 0 && (
            <div className="col-span-3 rounded-xl border border-dashed border-slate-200 p-6 text-center text-sm text-slate-400">
              暂无数据集，创建后即可回归评估
            </div>
          )}
        </div>
      </section>

      {/* 运行记录 + Dashboard */}
      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-600">运行记录</h2>
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                <th className="px-3 py-2">时间</th>
                <th className="px-3 py-2">Agent</th>
                <th className="px-3 py-2">模式</th>
                <th className="px-3 py-2">状态</th>
                <th className="px-3 py-2">成功率</th>
                <th className="px-3 py-2">平均评分</th>
                <th className="px-3 py-2">耗时</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr
                  key={r.id}
                  className={`border-t border-slate-100 ${selectedRunId === r.id ? "bg-blue-50/50" : ""}`}
                >
                  <td className="px-3 py-2 font-mono text-xs">{r.created_at.slice(5, 19).replace("T", " ")}</td>
                  <td className="px-3 py-2">{r.model_snapshot.agent ?? "—"}</td>
                  <td className="px-3 py-2 text-xs">
                    {r.model_snapshot.scripted ? "scripted" : "live"}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] ${
                        r.status === "completed"
                          ? "bg-emerald-100 text-emerald-700"
                          : r.status === "failed"
                            ? "bg-red-100 text-red-700"
                            : "bg-amber-100 text-amber-700"
                      }`}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 tabular-nums">{fmt(r.metrics.success_rate)}</td>
                  <td className="px-3 py-2 tabular-nums">{fmt(r.metrics.avg_answer_score)}</td>
                  <td className="px-3 py-2 tabular-nums">{fmt(r.metrics.avg_latency_ms)}</td>
                  <td className="px-3 py-2">
                    <button
                      className="text-xs text-blue-600 hover:underline"
                      onClick={() => setSelectedRunId(selectedRunId === r.id ? null : r.id)}
                    >
                      {selectedRunId === r.id ? "收起" : "详情"}
                    </button>
                  </td>
                </tr>
              ))}
              {runs.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-3 py-6 text-center text-sm text-slate-400">
                    暂无评估运行
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {selectedRun && (
          <div className="mt-4 space-y-4">
            <div className="grid grid-cols-4 gap-2">
              {HEADLINE_METRICS.map((k) => (
                <StatTile
                  key={k}
                  label={METRIC_LABELS[k]}
                  value={fmt(selectedRun.metrics[k])}
                />
              ))}
              <StatTile label="平均耗时" value={fmt(selectedRun.metrics.avg_latency_ms)} />
            </div>

            {/* 成功趋势（单指标单轴，单色序贯柱，直接标注 + 表格视图） */}
            <div className="viz-root rounded-xl border border-slate-200 bg-white p-4">
              <style>{`
                .viz-root {
                  --series-1: #2a78d6;
                  --text-secondary: #52514e;
                }
              `}</style>
              <div className="mb-2 text-sm font-medium text-slate-700">
                最近运行成功率（%）
              </div>
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e7e5e4" vertical={false} />
                    <XAxis
                      dataKey="name"
                      tick={{ fontSize: 10, fill: "#78716c" }}
                      tickLine={false}
                      axisLine={{ stroke: "#e7e5e4" }}
                    />
                    <YAxis
                      domain={[0, 100]}
                      tick={{ fontSize: 10, fill: "#78716c" }}
                      tickLine={false}
                      axisLine={false}
                    />
                    <Tooltip
                      formatter={(v) => [`${v}%`, "成功率"]}
                      labelStyle={{ fontSize: 11 }}
                      contentStyle={{ fontSize: 11, borderRadius: 8 }}
                    />
                    <Bar
                      dataKey="成功率"
                      fill="var(--series-1)"
                      barSize={28}
                      radius={[4, 4, 0, 0]}
                      label={{ position: "top", fontSize: 10, fill: "var(--text-secondary)" }}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* 全部指标（表格视图） */}
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="mb-2 text-sm font-medium text-slate-700">全部指标</div>
              <div className="grid grid-cols-4 gap-x-4 gap-y-2">
                {Object.entries(selectedRun.metrics).map(([k, v]) => (
                  <div key={k} className="flex items-baseline justify-between border-b border-slate-50 text-sm">
                    <span className="text-xs text-slate-500">{METRIC_LABELS[k] ?? k}</span>
                    <span className="tabular-nums">{fmt(v)}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* 逐条评分 */}
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-left text-xs text-slate-500">
                  <tr>
                    <th className="px-3 py-2">#</th>
                    <th className="px-3 py-2">输入</th>
                    <th className="px-3 py-2">工具序列</th>
                    <th className="px-3 py-2">序列匹配</th>
                    <th className="px-3 py-2">评分</th>
                    <th className="px-3 py-2">Judge 理由</th>
                  </tr>
                </thead>
                <tbody>
                  {scores.map((s) => (
                    <tr key={s.entry_index} className="border-t border-slate-100">
                      <td className="px-3 py-2 text-xs text-slate-400">{s.entry_index + 1}</td>
                      <td className="max-w-56 truncate px-3 py-2">{s.input}</td>
                      <td className="px-3 py-2 font-mono text-xs">
                        {s.trajectory.tool_sequence.length
                          ? s.trajectory.tool_sequence.join(" → ")
                          : "—"}
                      </td>
                      <td className="px-3 py-2 text-xs">
                        {s.tool_seq_match?.exact == null ? (
                          "—"
                        ) : s.tool_seq_match.exact ? (
                          <span className="text-emerald-600">完全匹配</span>
                        ) : s.tool_seq_match.prefix ? (
                          <span className="text-amber-600">前缀匹配</span>
                        ) : (
                          <span className="text-red-600">不匹配</span>
                        )}
                      </td>
                      <td className="px-3 py-2 tabular-nums">{s.answer_score ?? "—"}</td>
                      <td className="max-w-72 truncate px-3 py-2 text-xs text-slate-500">
                        {s.judge_reason ?? (s.error ?? "")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      {/* 新建数据集弹窗 */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40">
          <div className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-xl bg-white p-5 shadow-xl">
            <h2 className="mb-3 text-base font-semibold">新建数据集</h2>
            <div className="space-y-3 text-sm">
              <input
                className="w-full rounded-lg border border-slate-200 p-2"
                placeholder="名称"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
              <textarea
                className="w-full rounded-lg border border-slate-200 p-2 font-mono text-xs"
                rows={12}
                placeholder={`[{"input": "...", "expected_tool_calls": ["calculator"], "reference_answer": "...", "rubric": "..."}]`}
                value={form.entries}
                onChange={(e) => setForm({ ...form, entries: e.target.value })}
              />
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
                onClick={() => setShowCreate(false)}
              >
                取消
              </button>
              <button
                className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
                onClick={() => void createDataset()}
              >
                创建
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 运行评估弹窗 */}
      {showRun && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40">
          <div className="w-full max-w-sm rounded-xl bg-white p-5 shadow-xl">
            <h2 className="mb-3 text-base font-semibold">运行评估：{showRun.name}</h2>
            <select
              className="w-full rounded-lg border border-slate-200 p-2 text-sm"
              value={runAgentId}
              onChange={(e) => setRunAgentId(e.target.value)}
            >
              <option value="">默认 Agent</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
            <div className="mt-4 flex justify-end gap-2">
              <button
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
                onClick={() => setShowRun(null)}
              >
                取消
              </button>
              <button
                className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
                onClick={() => void startRun()}
              >
                开始
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
