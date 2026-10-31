import { documentPublishLink } from "@/lib/platform-links";
import { useTopicPosition } from "@/lib/use-topic-position";
import { useCallback, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { listShipments, type Shipment } from "@/api/video";
import { DistributionSummary, DistributionFilters } from "@/components/DistributionSummary";
import { matchesDistribution } from "@/lib/distributions";
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
const previousShipments = new WeakMap<object, Map<string, Shipment[]>>();
const PAGE_SIZE = 24;

function state(s: Shipment): { label: string; cls: string } {
  if (s.render?.status === "COMPLETED") {
    return { label: "成片完成", cls: "border-accent text-accent" };
  }
  if (s.render && ["FAILED", "CANCELLED"].includes(s.render.status)) return { label: "出片失败", cls: "border-danger text-danger" };
  if (s.render) return { label: ["SUBMITTING", "QUEUED", "RUNNING", "DOWNLOADING"].includes(s.render.status) ? "出片中" : RENDER_NOTE[s.render.status] ?? s.render.status, cls: "border-border text-text-muted" };
  return { label: "待出片", cls: "border-border text-text-muted" };
}

export function matchesTodo(row: Shipment, value: string, platform: string) {
  const targets = (row.distributions ?? []).filter(d => (!platform || d.platform === platform) && (d.current_account || !d.account_uid));
  // With no platform selected, only explicitly prepared or used targets constitute this video's work.
  const selected = platform ? targets : targets.filter(d => d.status !== "UNPUBLISHED");
  const states = selected.map(d => d.status);
  const done = states.filter(v => v === "PUBLISHED").length;
  if (value === "已完成") return states.length > 0 && done === states.length;
  if (value === "部分完成") return done > 0 && done < states.length;
  if (value === "需处理") return states.some(v => ["UNKNOWN", "FAILED", "CANCELLED"].includes(v));
  if (value === "发布中") return states.some(v => ["QUEUED", "UPLOADING", "SUBMITTED", "RUNNING"].includes(v));
  if (value === "草稿") return states.includes("DRAFT");
  return row.render?.status === "COMPLETED" && (!states.length || states.some(v => ["UNPUBLISHED", "DRAFT"].includes(v)));
}

export function PublishQueuePage() {
  const root = useRef<HTMLDivElement>(null);
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const watch = params.get("watch");
  const user = useAuthStore((s) => s.user);
  const role = user?.role;
  const canEdit = role === "editor" || role === "admin";
  const filters = useFilters(["stage", "tier", "column", "source", "range", "matrix", "platform", "distribution", "scope", "todo"]);
  const load = useCallback(async (signal: AbortSignal) => {
    const fresh = await listShipments(signal, filters.values.scope === "history");
    if (!signal.aborted && user) { const cache = previousShipments.get(user) ?? new Map<string, Shipment[]>(); cache.set(filters.values.scope, fresh); previousShipments.set(user, cache); }
    return fresh;
  }, [user, filters.values.scope]);
  const { data, error, loading, reload } = usePageResource(load, 20000);
  const rows = data ?? (user ? previousShipments.get(user)?.get(filters.values.scope) : undefined) ?? null;
  useTopicPosition(root, Boolean(rows));
  const [advanced, setAdvanced] = useState(false);
  const columnNames = new Map(rows?.map(r => [r.column_key, r.column_label || r.column_key]));
  const cellName = (key: string) => columnNames.get(key) ?? key;

  const { values: f } = filters;
  const limit = Math.max(PAGE_SIZE, Math.min(100000, Number(params.get("limit")) || PAGE_SIZE));
  const scope = (rows ?? []).filter((s) => !f.matrix || (s.matrix || "_independent") === f.matrix);
  const matrixOptions = [...new Map((rows ?? []).map((s) => [s.matrix || "_independent", s.matrix_name || "独立视频"]))];
  const shown = scope.filter(
    (s) =>
      (!f.stage || state(s).label === f.stage) &&
      (!f.todo || matchesTodo(s, f.todo, f.platform)) &&
      (!f.tier || s.credibility?.tier === f.tier) &&
      (!f.column || s.column_key === f.column) &&
      (!f.source || (f.source === "auto") === Boolean(s.auto)) &&
      matchesDistribution(s.distributions, f.platform, f.distribution, s.publications.length > 0) &&
      inRange(s.render?.completed_at ?? s.created_at, f.range) &&
      matchesText(f.q, s.title, s.job_name, s.matrix_name),
  );
  const groups = new Map<string, { name: string; total: number; rows: Shipment[] }>();
  const stages = new Map<string, number>();
  for (const row of scope) {
    const label = state(row).label;
    stages.set(label, (stages.get(label) ?? 0) + 1);
  }
  shown.forEach((row, index) => {
    const key = row.matrix || "_independent";
    let group = groups.get(key);
    if (!group) {
      group = { name: row.matrix_name || "独立视频", total: 0, rows: [] };
      groups.set(key, group);
    }
    group.total++;
    if (index < limit) group.rows.push(row);
  });
  const facets: Facet[] = [
    { key: "stage", label: "阶段", options: ["成片完成", "待出片", "出片中", "出片失败"].map((v) => ({ value: v, label: v })) },
    { key: "tier", label: "可信度", options: (Object.keys(TIER_LABEL) as (keyof typeof TIER_LABEL)[]).map((k) => ({ value: k, label: TIER_LABEL[k] })) },
    { key: "column", label: "视频类型", options: Array.from(new Set((rows ?? []).map((r) => r.column_key))).map((v) => ({ value: v, label: cellName(v) })) },
    { key: "source", label: "来源", options: [{ value: "auto", label: "自动流水线" }, { value: "manual", label: "手动创建" }] },
    RANGE_FACET,
  ];

  return (
    <div ref={root} className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-xl font-semibold tracking-tight">成片与发布</h1><p className="text-xs text-text-muted mt-1">先原地看片，再为这条成片准备平台草稿、跟进投稿结果。</p></div><div className="flex gap-2"><Link className="btn text-xs" to="/video?status=all">全部文稿 ↗</Link><button className="btn text-xs" disabled={loading} aria-label="刷新成片" onClick={reload}><RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />刷新</button></div></header>
      {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
      {rows && loading ? <p role="status" className="text-xs text-text-muted">正在更新成片状态…</p> : null}
      <div className="flex flex-wrap gap-2"><select aria-label="成片范围" className="input w-auto text-xs" value={f.scope} onChange={e => filters.set("scope", e.target.value)}><option value="">当前定稿</option><option value="history">全部保留成片（含旧版与编辑中）</option></select><select aria-label="发布待办" className="input w-auto text-xs" value={f.todo} onChange={e => filters.set("todo", e.target.value)}><option value="">全部待办</option>{["待发布", "草稿", "发布中", "部分完成", "需处理", "已完成"].map(v => <option key={v} value={v}>{v}</option>)}</select><select aria-label="成片矩阵" className="input w-auto text-xs max-w-full" value={f.matrix} onChange={(e) => filters.set("matrix", e.target.value)}><option value="">全部矩阵</option>{matrixOptions.map(([key,name]) => <option key={key} value={key}>{name}</option>)}</select><DistributionFilters platform={f.platform} status={f.distribution} onPlatform={v => filters.set("platform", v)} onStatus={v => filters.set("distribution", v)} /><button className="btn text-xs" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}><SlidersHorizontal className="w-3.5 h-3.5" />筛选</button></div>
      {rows ? <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">{["成片完成", "待出片", "出片中", "出片失败"].map((label) => <button key={label} aria-pressed={f.stage === label} onClick={() => filters.set("stage", f.stage === label ? "" : label)} className={cn("card p-4 text-left hover:bg-bg-hover", f.stage === label && "border-accent")}><div className="text-xs text-text-muted">{label}</div><div className="text-2xl mt-1 font-semibold">{stages.get(label) ?? 0}</div></button>)}</div> : !error ? <ErrorState empty="正在读取成片…" /> : null}
      <p className="text-xs text-text-muted">发布待办按所选平台计算；全部平台时，只计算已保存草稿或已有投稿记录的目标。</p>
      {rows ? <FilterBar facets={advanced ? facets : []} filters={filters} shown={shown.length} total={scope.length} placeholder="搜索视频或来源任务" /> : null}
      {rows && !shown.length ? <section className="card p-8 text-center text-sm text-text-muted">{rows.length ? "没有符合筛选的成片" : "还没有已定稿的文稿"}{filters.active ? <button className="btn text-xs ml-3" onClick={filters.clear}>清除筛选</button> : <Link className="text-accent ml-3 hover:underline" to="/video">查看文稿</Link>}</section> : null}
      <div className={cn("grid gap-5 items-start", !f.matrix && "xl:grid-cols-2")}>{[...groups].filter(([, group]) => group.rows.length).map(([matrix, group]) => <section key={matrix} className="space-y-3"><h2 className="text-sm font-medium flex items-center gap-2">{group.name}<span className="text-xs font-normal text-text-muted">{group.total} 个视频</span></h2>
      <ul className={cn("grid gap-3", f.matrix && "xl:grid-cols-2")}>
        {group.rows.map((s) => {
          const st = state(s);
          return (
            <li id={`shipment-${s.render?.id || s.script_id}`} key={`${s.script_id}:${s.approved_version}`} className="card p-4 space-y-3 min-w-0">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <span className={cn("badge border", st.cls)}>{st.label}</span>
                <Link to={`/video/${s.script_id}`} className="text-sm font-medium hover:underline w-full order-first leading-6">{s.title || s.script_id.slice(0, 8)}</Link>
                <CredibilityBadge value={s.credibility} />
                {s.auto ? <span className="badge border border-border text-text-muted">自动</span> : null}
                <span className="text-xs text-text-muted">{s.column_label || cellName(s.column_key)} · 成片文稿 v{s.render?.version ?? s.approved_version}</span>
                <TimeStamp value={s.created_at} label="创建" />
                {s.render?.status === "COMPLETED" ? <TimeStamp value={s.render.completed_at} label="成片完成" /> : null}
                {s.job_name ? <span className="text-xs text-text-muted w-full">来源：{s.job_name}</span> : null}
                {s.historical ? <span className="text-xs text-warning">保留成片 · 当前文稿 v{s.current_version}（{({ DRAFT: "草稿", WRITING: "写稿中", APPROVED: "已定稿", FAILED: "写稿失败", PLAN_READY: "方案待处理" } as Record<string,string>)[s.script_status ?? ""] ?? "历史版本"}）</span> : null}
                {s.video_url && s.render ? <button type="button" className="btn text-xs" onClick={() => setParams(previous => { const next = new URLSearchParams(previous); if (watch === s.render!.id) next.delete("watch"); else next.set("watch", s.render!.id); return next; }, { replace: true })}>{watch === s.render.id ? "收起成片" : "看成片"}</button> : null}
                {s.render?.status === "COMPLETED" && canEdit ? <RevealButton renderId={s.render.id} className="text-xs text-accent hover:underline" /> : null}

                {st.label === "待核实" ? <Link className="btn text-xs border-warning/40 text-warning" to={`/video/${s.script_id}?tab=review`}>先核实文稿 →</Link> : null}
                <Link className="text-xs text-accent hover:underline" to={s.render?.status === "COMPLETED" ? documentPublishLink(s.script_id, s.render.version, s.render.id, `${location.pathname}${location.search}#shipment-${s.render.id}`) : `/video/${s.script_id}?tab=video&return_to=${encodeURIComponent(location.pathname + location.search)}`}>
                  {s.render?.status === "COMPLETED" ? "各平台发布与记录 →" : "去出片 →"}
                </Link>
              </div>
              {s.video_url && watch === s.render?.id ? <video aria-label={`${s.title}成片`} controls preload="metadata" src={s.video_url} className="w-full max-h-[50vh] bg-black rounded" /> : null}
              <DistributionSummary rows={s.distributions} publications={s.publications} />
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
      {shown.length > PAGE_SIZE ? <div className="flex justify-center items-center gap-3 text-xs text-text-muted">
        <span>已展示 {Math.min(limit, shown.length)} / {shown.length} 个视频</span>
        {limit < shown.length ? <button className="btn text-xs" onClick={() => setParams(previous => { const next = new URLSearchParams(previous); next.set("limit", String(limit + PAGE_SIZE)); return next; }, { replace: true })}>显示更多成片</button> : null}
      </div> : null}
    </div>
  );
}
