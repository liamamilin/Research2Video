import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  cancelAutopilotRun, getAutopilot, listCellCoverage, setPlanSpec, startAutopilotRun,
  type AutopilotItem, type AutopilotPlan, type AutopilotRun, type AutopilotSpec, type BoardItem, type CellCoverage,
} from "@/api/video";
import { CredibilityBadge } from "@/components/CredibilityBadge";
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
export function AutopilotCard({ currentItems = [] }: {
  currentItems?: Pick<BoardItem, "script_id" | "title" | "version" | "note" | "credibility">[];
}) {
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";
  const [plans, setPlans] = useState<AutopilotPlan[]>([]);
  const [run, setRun] = useState<AutopilotRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [cells, setCells] = useState<CellCoverage[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [labels, setLabels] = useState<Record<string, string>>({});

  useEffect(() => {
    listCellCoverage()
      .then((rows) => setLabels(Object.fromEntries(rows.map((r) => [r.cell, r.label.split(" · ")[0]]))))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!editing || cells.length) return;
    listCellCoverage()
      .then((rows) => {
        const usable = rows.filter((r) => r.autopilot && r.sources.length);
        setCells(usable);
        // start from what the weekly plan writes now, else the default batch
        const picks = plans[0]?.spec?.picks ?? [{ cell: "flash", count: 3 }, { cell: "point_short", count: 2 }];
        const init: Record<string, number> = {};
        for (const pick of picks) init[pick.cell ?? pick.column ?? ""] = pick.count;
        setCounts(init);
      })
      .catch(() => undefined);
  }, [editing, cells.length, plans]);

  const specNow = (): AutopilotSpec => ({
    picks: Object.entries(counts).filter(([cell, n]) => n > 0 && cells.some((c) => c.cell === cell)).map(([cell, count]) => ({ cell, count })),
    refresh: true,
  });
  const total = specNow().picks.reduce((a, p) => a + p.count, 0);

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
    <section className="card p-4 space-y-2" aria-label="自动流水线">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-medium text-sm flex-1">自动流水线</h2>
        {canEdit && run?.status === "RUNNING" ? (
          <button className="btn text-xs" disabled={busy} onClick={() => act(() => cancelAutopilotRun(run.id))}>取消这一批</button>
        ) : null}
        {canEdit && run?.status !== "RUNNING" ? (
          <button className="btn text-xs" disabled={busy} onClick={() => (editing ? setEditing(false) : setEditing(true))}>
            {editing ? "收起" : "设置这一批"}
          </button>
        ) : null}
        {canEdit && run?.status !== "RUNNING" && !editing ? (
          <button className="btn text-xs" disabled={busy} onClick={() => act(() => startAutopilotRun())}>
            现在开始一批（3 条快讯 + 2 个知识点）
          </button>
        ) : null}
      </div>
      {editing ? (
        <div className="space-y-2 rounded border border-border p-3" aria-label="批次设置">
          <p className="text-xs text-text-muted">每个格子写几条（只列有选题来源、能按条数挑的格子）。每条要研究约几次到二十次搜索。</p>
          <div className="flex flex-wrap gap-3">
            {cells.map((c) => (
              <label key={c.cell} className="flex items-center gap-2 text-xs">
                {c.label}
                <input
                  type="number"
                  min={0}
                  max={5}
                  className="input w-16"
                  aria-label={c.label}
                  value={counts[c.cell] ?? 0}
                  onChange={(e) => setCounts({ ...counts, [c.cell]: Math.max(0, Math.min(5, Number(e.target.value) || 0)) })}
                />
              </label>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-primary text-xs" disabled={busy || total === 0} onClick={() => act(async () => { await startAutopilotRun(specNow()); setEditing(false); })}>
              按这个现在开始一批（共 {total} 条）
            </button>
            {plans[0] ? (
              <button className="btn text-xs" disabled={busy || total === 0} onClick={() => act(() => setPlanSpec(plans[0].id, specNow()))}>
                存为「{plans[0].name}」的内容
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
      {error ? <p className="text-danger text-xs">{error}</p> : null}
      {plans.map((p) => (
        <p key={p.id} className="text-xs text-text-muted">
          计划「{p.name}」{p.expression}{p.enabled ? `（启用），下次 ${when(p.next_fire)}` : "（已停用，不会自动运行）"}
          {p.spec?.picks.length ? `；写 ${p.spec.picks.map((k) => `${labels[k.cell ?? k.column ?? ""] ?? k.cell ?? k.column} ${k.count} 条`).join("、")}` : ""}
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
            {run.items.map((item) => {
              const current = currentItems.find((c) => c.script_id === item.script_id);
              return (
              <li key={item.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2">
                <span className={cn("badge border", STATE[item.state].cls)}>{run.status === "RUNNING" ? "" : "批次结果："}{STATE[item.state].label}</span>
                {item.script_id ? (
                  <Link to={`/video/${item.script_id}`} className="text-sm font-medium text-accent hover:underline">{current?.title ?? item.topic.title}</Link>
                ) : (
                  <span className="text-sm font-medium">{item.topic.title}</span>
                )}
                <span className="text-xs text-text-muted">{current ? `当前 v${current.version}：${current.note}` : item.note}</span>
                <CredibilityBadge value={current?.credibility ?? item.credibility ?? (item.gate?.label && item.gate.text ? { tier: item.gate.label, text: item.gate.text, reasons: item.gate.reasons ?? [], cited_share: null } : null)} />
              </li>
            );})}
          </ul>
        </>
      ) : (
        <p className="text-xs text-text-muted">还没有跑过。</p>
      )}
    </section>
  );
}
