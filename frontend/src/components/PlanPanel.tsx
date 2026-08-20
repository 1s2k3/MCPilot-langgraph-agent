import type { PlanStepView } from "../api/types";

const STATUS_MARK: Record<PlanStepView["status"], { icon: string; cls: string }> = {
  pending: { icon: "○", cls: "text-slate-300" },
  in_progress: { icon: "◐", cls: "animate-pulse text-violet-600" },
  done: { icon: "✓", cls: "text-emerald-600" },
  failed: { icon: "✗", cls: "text-red-600" },
  skipped: { icon: "–", cls: "text-slate-400" },
};

export default function PlanPanel({ plan }: { plan: PlanStepView[] | null }) {
  if (!plan || plan.length === 0) return null;
  return (
    <div className="my-2 rounded-lg border border-violet-200 bg-violet-50/50 p-3">
      <div className="mb-1.5 text-xs font-semibold text-violet-800">执行计划</div>
      <ol className="space-y-1">
        {plan.map((step, i) => {
          const mark = STATUS_MARK[step.status] ?? STATUS_MARK.pending;
          return (
            <li key={step.id} className="flex items-start gap-2 text-sm">
              <span className={`mt-0.5 ${mark.cls}`}>{mark.icon}</span>
              <div className="min-w-0">
                <span className="text-slate-700">
                  <span className="text-[10px] text-slate-400">{i + 1}.</span> {step.goal}
                </span>
                {step.attempts > 0 && (
                  <span className="ml-2 text-[10px] text-amber-600">
                    重试 {step.attempts} 次
                  </span>
                )}
                {step.feedback.length > 0 && (
                  <div className="text-[11px] text-amber-700">
                    反馈: {step.feedback[step.feedback.length - 1]}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
