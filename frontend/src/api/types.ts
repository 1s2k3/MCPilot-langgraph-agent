/** 与后端 API 契约对齐的类型（docs/development-framework.md §7）。 */

export interface PlatformEvent {
  seq: number;
  ts: string;
  run_id: string;
  type: string;
  payload: Record<string, unknown>;
}

export type StepStatus = "pending" | "in_progress" | "done" | "failed" | "skipped";

export interface PlanStepView {
  id: string;
  goal: string;
  status: StepStatus;
  attempts: number;
  feedback: string[];
  tools_hint: string[];
}

export interface ToolCallView {
  id: string;
  name: string;
  server: string;
  status: "running" | "succeeded" | "failed";
  args?: unknown;
  duration_ms?: number;
  truncated?: boolean;
  error?: string;
}

export interface InterruptView {
  pending: { name: string; args: unknown; id: string }[];
  resumable?: boolean;
}

export interface Thread {
  id: string;
  agent_id: string | null;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface AgentConfig {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  planner_prompt: string;
  node_models: Record<string, Record<string, unknown>>;
  budgets: Record<string, number>;
  tool_policy: { rules: { tool: string; action: string }[]; default: string };
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface McpServerView {
  id: string;
  name: string;
  transport: string;
  command: string | null;
  args: string[] | null;
  url: string | null;
  env_masked: Record<string, string>; // 加密存储，只返回掩码
  enabled: boolean;
  tool_allowlist: string[] | null;
  headers_masked: Record<string, string>;
  health: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryView {
  id: string;
  type: string;
  content: string;
  importance: number;
  thread_id: string | null;
  source_run_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageView {
  id: string;
  role: string;
  content: string;
  tool_calls: unknown;
  seq: number;
  run_id: string | null;
  created_at: string;
}

export interface RunView {
  id: string;
  thread_id: string;
  agent_id: string | null;
  status: string;
  input: string;
  final_answer: string | null;
  usage: Record<string, number>;
  latency_ms: number | null;
  error: Record<string, unknown> | null;
  created_at: string;
  finished_at: string | null;
}

export interface ApiKeyView {
  id: string;
  provider: string;
  name: string;
  masked: string;
  last_used_at: string | null;
  created_at: string;
}
