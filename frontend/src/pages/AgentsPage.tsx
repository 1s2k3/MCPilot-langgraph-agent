import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { AgentConfig } from "../api/types";

const ACTIONS = ["allow", "ask", "deny"];
const EMPTY_FORM = {
  name: "",
  description: "",
  system_prompt: "",
  planner_prompt: "",
  budgets: '{"max_llm_calls": 40, "max_total_tool_calls": 30, "max_plan_steps": 8, "max_attempts_per_step": 3}',
  rules: [{ tool: "", action: "allow" }] as { tool: string; action: string }[],
  default: "allow",
};

/** Agent 配置页：CRUD + 工具权限矩阵（§5.9）。 */
export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [editing, setEditing] = useState<AgentConfig | "new" | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);

  const load = useCallback(async () => {
    setAgents(await api<AgentConfig[]>("/api/agents"));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function openForm(agent: AgentConfig | "new") {
    if (agent === "new") {
      setForm(EMPTY_FORM);
    } else {
      setForm({
        name: agent.name,
        description: agent.description,
        system_prompt: agent.system_prompt,
        planner_prompt: agent.planner_prompt,
        budgets: JSON.stringify(agent.budgets ?? {}, null, 2),
        rules: agent.tool_policy?.rules?.map((r) => ({ ...r })) ?? [],
        default: agent.tool_policy?.default ?? "allow",
      });
    }
    setEditing(agent);
  }

  async function save() {
    let budgets: Record<string, number>;
    try {
      budgets = JSON.parse(form.budgets) as Record<string, number>;
    } catch {
      alert("budgets 不是合法 JSON");
      return;
    }
    const payload = {
      name: form.name,
      description: form.description,
      system_prompt: form.system_prompt,
      planner_prompt: form.planner_prompt,
      budgets,
      tool_policy: {
        rules: form.rules.filter((r) => r.tool.trim()),
        default: form.default,
      },
    };
    const url = editing === "new" ? "/api/agents" : `/api/agents/${(editing as AgentConfig).id}`;
    await api(url, {
      method: editing === "new" ? "POST" : "PATCH",
      body: JSON.stringify(payload),
    });
    setEditing(null);
    await load();
  }

  async function remove(id: string) {
    if (!confirm("确认删除该 Agent？")) return;
    await api(`/api/agents/${id}`, { method: "DELETE" });
    await load();
  }

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Agents</h1>
        <button
          className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
          onClick={() => openForm("new")}
        >
          + 新建 Agent
        </button>
      </div>

      <div className="space-y-2">
        {agents.map((a) => (
          <div
            key={a.id}
            className="flex items-center justify-between rounded-xl border border-slate-200 bg-white p-4"
          >
            <div>
              <div className="font-medium">
                {a.name}
                {!a.enabled && (
                  <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                    已禁用
                  </span>
                )}
              </div>
              <div className="mt-0.5 max-w-xl truncate text-xs text-slate-400">
                {a.system_prompt || a.description || "（无系统提示词）"}
              </div>
            </div>
            <div className="flex gap-2">
              <button
                className="rounded-lg border border-slate-200 px-3 py-1 text-sm text-slate-600 hover:bg-slate-50"
                onClick={() => openForm(a)}
              >
                编辑
              </button>
              <button
                className="rounded-lg border border-red-200 px-3 py-1 text-sm text-red-500 hover:bg-red-50"
                onClick={() => void remove(a.id)}
              >
                删除
              </button>
            </div>
          </div>
        ))}
      </div>

      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40">
          <div className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-xl bg-white p-5 shadow-xl">
            <h2 className="mb-3 text-base font-semibold">
              {editing === "new" ? "新建 Agent" : `编辑 ${editing.name}`}
            </h2>
            <div className="space-y-3 text-sm">
              <input
                className="w-full rounded-lg border border-slate-200 p-2"
                placeholder="名称"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
              <textarea
                className="w-full rounded-lg border border-slate-200 p-2"
                rows={3}
                placeholder="系统提示词"
                value={form.system_prompt}
                onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
              />
              <textarea
                className="w-full rounded-lg border border-slate-200 p-2"
                rows={2}
                placeholder="规划提示词（可选，覆盖默认）"
                value={form.planner_prompt}
                onChange={(e) => setForm({ ...form, planner_prompt: e.target.value })}
              />
              <textarea
                className="w-full rounded-lg border border-slate-200 p-2 font-mono text-xs"
                rows={4}
                placeholder="预算护栏 JSON"
                value={form.budgets}
                onChange={(e) => setForm({ ...form, budgets: e.target.value })}
              />
              <div>
                <div className="mb-1 font-medium text-slate-600">工具权限策略</div>
                {form.rules.map((rule, i) => (
                  <div key={i} className="mb-1 flex gap-2">
                    <input
                      className="min-w-0 flex-1 rounded-lg border border-slate-200 px-2 py-1 font-mono text-xs"
                      placeholder="工具名或通配符，如 calculator / mcp-demo.*"
                      value={rule.tool}
                      onChange={(e) => {
                        const rules = [...form.rules];
                        rules[i] = { ...rule, tool: e.target.value };
                        setForm({ ...form, rules });
                      }}
                    />
                    <select
                      className="rounded-lg border border-slate-200 px-2 py-1 text-xs"
                      value={rule.action}
                      onChange={(e) => {
                        const rules = [...form.rules];
                        rules[i] = { ...rule, action: e.target.value };
                        setForm({ ...form, rules });
                      }}
                    >
                      {ACTIONS.map((a) => (
                        <option key={a} value={a}>{a}</option>
                      ))}
                    </select>
                    <button
                      className="px-2 text-red-400 hover:text-red-600"
                      onClick={() =>
                        setForm({ ...form, rules: form.rules.filter((_, j) => j !== i) })
                      }
                    >
                      ✕
                    </button>
                  </div>
                ))}
                <button
                  className="text-xs text-blue-600 hover:underline"
                  onClick={() =>
                    setForm({ ...form, rules: [...form.rules, { tool: "", action: "allow" }] })
                  }
                >
                  + 添加规则
                </button>
                <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                  未命中规则的默认动作:
                  <select
                    className="rounded-lg border border-slate-200 px-2 py-1"
                    value={form.default}
                    onChange={(e) => setForm({ ...form, default: e.target.value })}
                  >
                    {ACTIONS.map((a) => (
                      <option key={a} value={a}>{a}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
                onClick={() => setEditing(null)}
              >
                取消
              </button>
              <button
                className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
                onClick={() => void save()}
              >
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
