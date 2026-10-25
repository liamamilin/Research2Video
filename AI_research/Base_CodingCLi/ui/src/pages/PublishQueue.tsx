import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { listShipments, type Shipment } from "@/api/video";
import { CredibilityBadge, TIER_LABEL } from "@/components/CredibilityBadge";
import { FilterBar, inRange, matchesText, RANGE_FACET, useFilters, type Facet } from "@/components/FilterBar";
import { TimeStamp } from "@/components/TimeStamp";
import { useAuthStore } from "@/lib/auth-store";
import { cn } from "@/lib/utils";
import { RefreshCw, SlidersHorizontal } from "lucide-react";
import { ErrorState } from "@/components/ErrorState";
import { usePageResource } from "@/lib/use-page-resource";
import { RevealButton } from "./VideoPublish";

const RENDER_NOTE: Record<string, string> = {
  SUBMITTING: "提交中", QUEUED: "排队中", RUNNING: "生成中", DOWNLOADING: "取回中",
  COMPLETED: "成片完成", FAILED: "出片失败", CANCELLED: "已取消",
};

// Reuse this login's last list on return; always fetch fresh statuses in the background.
// A different login gets a different user object, so its data is never reused.
const previousShipments = new WeakMap<object, Shipment[]>();

function state(s: Shipment): { label: string; cls: string } {
  if (s.publications.length) return { label: "已发布", cls: "border-success text-success" };
  if (s.render?.status === "COMPLETED") {
    if (s.credibility && ["serious", "verify", "unaudited"].includes(s.credibility.tier)) return { label: "待核实", cls: "border-warning text-warning" };
    return { label: "待发布", cls: "border-accent text-accent" };
  }
  if (s.render && ["FAILED", "CANCELLED"].includes(s.render.status)) return { label: "出片失败", cls: "border-danger text-danger" };
  if (s.render) return { label: RENDER_NOTE[s.render.status] ?? s.render.status, cls: "border-border text-text-muted" };
  return { label: "待出片", cls: "border-border text-text-muted" };
}

export function PublishQueuePage() {
  const user = useAuthStore((s) => s.user);
  const role = user?.role;
  const canEdit = role === "editor" || role === "admin";
  const filters = useFilters(["stage", "tier", "column", "source", "range", "matrix"]);
  const load = useCallback(async (signal: AbortSignal) => {
    const fresh = await listShipments(signal);
    if (!signal.aborted && user) previousShipments.set(user, fresh);
    return fresh;
  }, [user]);
  const { data, error, loading, reload } = usePageResource(load, 20000);
  const rows = data ?? (user ? previousShipments.get(user) : undefined) ?? null;
  const [advanced, setAdvanced] = useState(false);
  const cellName = (key: string) => rows?.find((r) => r.column_key === key)?.column_label ?? key;

  const { values: f } = filters;
  const scope = (rows ?? []).filter((s) => !f.matrix || (s.matrix || "_independent") === f.matrix);
  const matrixOptions = [...new Map((rows ?? []).map((s) => [s.matrix || "_independent", s.matrix_name || "独立视频"]))];
  const shown = scope.filter(
    (s) =>
      (!f.stage || state(s).label === f.stage) &&
      (!f.tier || s.credibility?.tier === f.tier) &&
      (!f.column || s.column_key === f.column) &&
      (!f.source || (f.source === "auto") === Boolean(s.auto)) &&
      inRange(s.render?.completed_at ?? s.created_at, f.range) &&
      matchesText(f.q, s.title, s.job_name, s.matrix_name),
  );
  const facets: Facet[] = [
    { key: "stage", label: "阶段", options: ["待核实", "待发布", "已发布", "待出片", "生成中", "出片失败"].map((v) => ({ value: v, label: v })) },
    { key: "tier", label: "可信度", options: (Object.keys(TIER_LABEL) as (keyof typeof TIER_LABEL)[]).map((k) => ({ value: k, label: TIER_LABEL[k] })) },
    { key: "column", label: "视频类型", options: Array.from(new Set((rows ?? []).map((r) => r.column_key))).map((v) => ({ value: v, label: cellName(v) })) },
    { key: "source", label: "来源", options: [{ value: "auto", label: "自动流水线" }, { value: "manual", label: "手动创建" }] },
    RANGE_FACET,
  ];

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-xl font-semibold tracking-tight">成片与发布</h1><p className="text-xs text-text-muted mt-1">按矩阵查看当前定稿的成片，看片后手动上传并记录发布链接。</p></div><div className="flex gap-2"><Link className="btn text-xs" to="/video?status=all">全部文稿 ↗</Link><button className="btn text-xs" disabled={loading} aria-label="刷新成片" onClick={reload}><RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />刷新</button></div></header>
      {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
      {rows && loading ? <p role="status" className="text-xs text-text-muted">正在更新成片状态…</p> : null}
      <div className="flex flex-wrap gap-2"><select aria-label="成片矩阵" className="input w-auto text-xs max-w-full" value={f.matrix} onChange={(e) => filters.set("matrix", e.target.value)}><option value="">全部矩阵</option>{matrixOptions.map(([key,name]) => <option key={key} value={key}>{name}</option>)}</select><button className="btn text-xs" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}><SlidersHorizontal className="w-3.5 h-3.5" />筛选</button></div>
      {rows ? <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">{["待核实", "待发布", "待出片", "已发布"].map((label) => <button key={label} aria-pressed={f.stage === label} onClick={() => filters.set("stage", f.stage === label ? "" : label)} className={cn("card p-4 text-left hover:bg-bg-hover", f.stage === label && "border-accent")}><div className="text-xs text-text-muted">{label}</div><div className="text-2xl mt-1 font-semibold">{scope.filter((s) => state(s).label === label).length}</div></button>)}</div> : !error ? <ErrorState empty="正在读取成片…" /> : null}
      {rows ? <FilterBar facets={advanced ? facets : []} filters={filters} shown={shown.length} total={scope.length} placeholder="搜索视频或来源任务" /> : null}
      {rows && !shown.length ? <section className="card p-8 text-center text-sm text-text-muted">{rows.length ? "没有符合筛选的成片" : "还没有已定稿的文稿"}{filters.active ? <button className="btn text-xs ml-3" onClick={filters.clear}>清除筛选</button> : <Link className="text-accent ml-3 hover:underline" to="/video">查看文稿</Link>}</section> : null}
      <div className={cn("grid gap-5 items-start", !f.matrix && "xl:grid-cols-2")}>{[...new Set(shown.map((s) => s.matrix || "_independent"))].map((matrix) => <section key={matrix} className="space-y-3"><h2 className="text-sm font-medium flex items-center gap-2">{shown.find((s) => (s.matrix || "_independent") === matrix)?.matrix_name || "独立视频"}<span className="text-xs font-normal text-text-muted">{shown.filter((s) => (s.matrix || "_independent") === matrix).length} 个视频</span></h2>
      <ul className={cn("grid gap-3", f.matrix && "xl:grid-cols-2")}>
        {shown.filter((s) => (s.matrix || "_independent") === matrix).map((s) => {
          const st = state(s);
          return (
            <li key={s.script_id} className="card p-4 space-y-3 min-w-0">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <span className={cn("badge border", st.cls)}>{st.label}</span>
                <Link to={`/video/${s.script_id}`} className="text-sm font-medium hover:underline w-full order-first leading-6">{s.title || s.script_id.slice(0, 8)}</Link>
                <CredibilityBadge value={s.credibility} />
                {s.auto ? <span className="badge border border-border text-text-muted">自动</span> : null}
                <span className="text-xs text-text-muted">{s.column_label || cellName(s.column_key)} · 定稿 v{s.approved_version}</span>
                <TimeStamp value={s.created_at} label="创建" />
                {s.render?.status === "COMPLETED" ? <TimeStamp value={s.render.completed_at} label="成片完成" /> : null}
                {s.job_name ? <span className="text-xs text-text-muted w-full">来源：{s.job_name}</span> : null}
                {s.video_url ? <a className="text-xs text-accent hover:underline" href={s.video_url} target="_blank" rel="noreferrer">看成片</a> : null}
                {s.render?.status === "COMPLETED" && canEdit ? <RevealButton renderId={s.render.id} className="text-xs text-accent hover:underline" /> : null}
                {s.render?.status === "COMPLETED" && canEdit && !s.publications.length ? <Link to={`/platforms?tab=uploads&render=${s.render.id}`} className="text-xs text-accent hover:underline">投稿到哔哩哔哩 →</Link> : null}
                {st.label === "待核实" ? <Link className="btn text-xs border-warning/40 text-warning" to={`/video/${s.script_id}?tab=review`}>先核实文稿 →</Link> : null}
                <Link className="text-xs text-accent hover:underline" to={`/video/${s.script_id}?tab=${s.render?.status === "COMPLETED" ? "publish" : "video"}`}>
                  {s.render?.status === "COMPLETED" ? "发布包与记录 →" : "去出片 →"}
                </Link>
              </div>
              {s.render?.error ? <p className="text-xs text-danger">{s.render.error}</p> : null}
              {s.warnings.map((w) => (
                <p key={w} className="text-xs text-warning">{w}</p>
              ))}
              {s.publications.map((p) => (
                <p key={p.id} className="text-xs text-text-muted">
                  {p.published_on} · <a className="text-accent hover:underline" href={p.url} target="_blank" rel="noreferrer">{p.url}</a>
                </p>
              ))}
            </li>
          );
        })}
      </ul></section>)}</div>
    </div>
  );
}
