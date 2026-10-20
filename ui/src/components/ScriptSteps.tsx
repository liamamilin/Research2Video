import { useState } from "react";

import { rerunVideoStep, type ScriptEvent, type ScriptStep, type StepName, type VideoScriptDetail } from "@/api/video";
import { errorMessage, useToast } from "@/lib/toast";
import { cn } from "@/lib/utils";

const STATE: Record<ScriptStep["state"], { label: string; cls: string }> = {
  PENDING: { label: "未开始", cls: "text-text-muted" },
  RUNNING: { label: "进行中", cls: "text-accent" },
  DONE: { label: "完成", cls: "text-success" },
  FAILED: { label: "失败", cls: "text-danger" },
  SKIPPED: { label: "跳过", cls: "text-text-muted" },
  WAITING: { label: "等待", cls: "text-warning" },
  INCOMPLETE: { label: "没跑完", cls: "text-warning" },
};
const EVENT: Record<string, string> = { start: "开始", rerun: "重跑", resume: "从断点继续", approve: "定稿", render: "出片" };

// 能重跑哪一步、按钮叫什么、确认时说清楚花什么。出片在「成片」页，定稿在上方按钮。
function rerunnable(step: ScriptStep, detail: VideoScriptDetail): { label: string; confirm: string } | null {
  if (step.step === "research" && step.state === "FAILED") {
    return { label: "从断点继续", confirm: "继续调研？Job 已跑完的报告会直接用；没跑完就重新调研，会花搜索额度。" };
  }
  if (step.step === "write" && detail.report_path && ["DONE", "FAILED"].includes(step.state)) {
    const approved = detail.status === "APPROVED" ? "重写成功后文稿回到草稿，要重新定稿（旧定稿和成片都还在）；失败则保持定稿。" : "";
    return { label: "重写", confirm: `用同一份研究报告重写一版？不再调研；会调用写稿模型，存成新版本，旧版本都保留。${approved}` };
  }
  // only a draft: the audit resumes as a draft, which would take an approved script's approval away
  if (step.step === "audit" && detail.status === "DRAFT" && ["DONE", "INCOMPLETE"].includes(step.state) && detail.column?.writer !== "adapt") {
    return { label: "重跑审计", confirm: "重跑事实和风格审计？不改稿子，会调用审校模型。" };
  }
  return null;
}

export function ScriptSteps({ detail, canEdit, onChanged }: {
  detail: VideoScriptDetail; canEdit: boolean; onChanged: (d: VideoScriptDetail) => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState<StepName | null>(null);
  const [showLog, setShowLog] = useState(false);
  const steps = detail.steps ?? [];
  const events: ScriptEvent[] = detail.events ?? [];
  if (!steps.length) return null;
  const working = ["RESEARCHING", "WRITING"].includes(detail.status) || Boolean(detail.stage);

  const rerun = async (step: ScriptStep, confirmText: string) => {
    if (!window.confirm(confirmText)) return;
    setBusy(step.step);
    try {
      onChanged(await rerunVideoStep(detail.id, step.step));
      toast.success(`已开始：${step.label}`);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card p-3 space-y-2" aria-label="生产步骤">
      <ol className="flex flex-wrap gap-x-4 gap-y-2 text-xs">
        {steps.map((step, i) => {
          const action = canEdit && !working ? rerunnable(step, detail) : null;
          return (
            <li key={step.step} className="flex items-center gap-1.5" title={step.note || undefined}>
              <span className="text-text-muted">{i + 1}.</span>
              <span>{step.label}</span>
              <span className={cn("font-medium", STATE[step.state].cls)}>{STATE[step.state].label}</span>
              {step.note && step.state !== "DONE" ? (
                <span className="text-text-muted max-w-[16rem] truncate">{step.note}</span>
              ) : null}
              {action ? (
                <button className="text-accent hover:underline" disabled={busy !== null} onClick={() => rerun(step, action.confirm)}>
                  {busy === step.step ? "…" : action.label}
                </button>
              ) : null}
            </li>
          );
        })}
      </ol>
      {events.length ? (
        <div className="text-xs text-text-muted">
          <button className="hover:underline" onClick={() => setShowLog(!showLog)}>
            {showLog ? "收起记录" : `记录（${events.length}）`}
          </button>
          {showLog ? (
            <ul className="mt-1 space-y-0.5">
              {events.map((e, i) => (
                <li key={i}>
                  {e.at.replace("T", " ").slice(0, 16)} · {steps.find((s) => s.step === e.step)?.label ?? e.step}
                  {EVENT[e.event] ?? e.event} · {e.by || "系统"}
                  {e.note ? ` · ${e.note}` : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
