import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { cancelAutopilotRun, getAutopilot, startAutopilotRun, type AutopilotItem, type AutopilotPlan, type AutopilotRun } from "@/api/video";
import { TIER_CLASS } from "@/components/CredibilityBadge";
import { TimeStamp } from "@/components/TimeStamp";
import { useAuthStore } from "@/lib/auth-store";
import { cn } from "@/lib/utils";

const STATE: Record<AutopilotItem["state"], { label: string; cls: string }> = {
  PENDING: { label: "排队", cls: "border-border text-text-muted" },
  WORKING: { label: "进行中", cls: "border-accent text-accent" },
  HELD: { label: "出了故障", cls: "border-warning text-warning" },
  DONE: { label: "成片完成", cls: "border-success text-success" },
  FAILED: { label: "失败", cls: "border-danger text-danger" },
  CANCELLED: { label: "已取消", cls: "border-border text-text-muted" },
};

function when(epoch: number | null): string {
  return epoch ? new Date(epoch * 1000).toLocaleString("zh-CN", { hour12: false }) : "—";
}

/** 自动流水线：每周计划、最近一批每条视频走到哪一步。只写到成片，不发布。 */
export function AutopilotCard() {
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";
  const [plans, setPlans] = useState<AutopilotPlan[]>([]);
  const [run, setRun] = useState<AutopilotRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = () =>
    getAutopilot()
      .then((d) => {
        setPlans(Array.isArray(d?.plans) ? d.plans : []);
        setRun(Array.isArray(d?.runs) && d.runs.length ? d.runs[0] : null);
      })
      .catch(() => undefined);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 20000);
    return () => window.clearInterval(timer);
  }, []);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  if (!plans.length && !run && !canEdit) return null;
  return (
    <section className="card space-y-2" aria-label="自动流水线">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-medium text-sm flex-1">自动流水线</h2>
        {canEdit && run?.status === "RUNNING" ? (
          <button className="btn text-xs" disabled={busy} onClick={() => act(() => cancelAutopilotRun(run.id))}>取消这一批</button>
        ) : null}
        {canEdit && run?.status !== "RUNNING" ? (
          <button className="btn text-xs" disabled={busy} onClick={() => act(startAutopilotRun)}>
            现在开始一批（3 条快讯 + 2 个知识点）
          </button>
        ) : null}
      </div>
      {error ? <p className="text-danger text-xs">{error}</p> : null}
      {plans.map((p) => (
        <p key={p.id} className="text-xs text-text-muted">
          计划「{p.name}」{p.expression}（{p.enabled ? "启用" : "停用"}），下次 {when(p.next_fire)}
        </p>
      ))}
      {run ? (
        <>
          <p className="text-xs text-text-muted">
            最近一批：<TimeStamp value={run.created_at} />，{run.status === "RUNNING" ? "进行中" : "已结束"}
            {run.note ? `（${run.note}）` : ""}
          </p>
          {run.items.length === 0 ? <p className="text-xs text-text-muted">正在挑选选题…</p> : null}
          <ul className="divide-y divide-border">
            {run.items.map((item) => (
              <li key={item.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2">
                <span className={cn("badge border", STATE[item.state].cls)}>{STATE[item.state].label}</span>
                {item.script_id ? (
                  <Link to={`/video/${item.script_id}`} className="text-sm font-medium text-accent hover:underline">{item.topic.title}</Link>
                ) : (
                  <span className="text-sm font-medium">{item.topic.title}</span>
                )}
                <span className="text-xs text-text-muted">{item.note}</span>
                {item.gate?.label && item.gate.text ? (
                  <span className={cn("badge border", TIER_CLASS[item.gate.label])} title={(item.gate.reasons ?? []).join("；")}>{item.gate.text}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="text-xs text-text-muted">还没有跑过。</p>
      )}
    </section>
  );
}
