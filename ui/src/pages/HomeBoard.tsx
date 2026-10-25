import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Activity, ArrowUpRight, CheckCircle2, ChevronRight, ClipboardCheck, Layers, RefreshCw, Search, SlidersHorizontal } from "lucide-react";
import { getVideoBoard, type BoardItem, type VideoBoard } from "@/api/video";
import { AlertsCard } from "@/components/AlertsCard";
import { AutopilotCard } from "@/components/AutopilotCard";
import { CredibilityBadge, TIER_LABEL } from "@/components/CredibilityBadge";
import { ErrorState } from "@/components/ErrorState";
import { inRange, matchesText, RANGE_FACET } from "@/components/FilterBar";
import { TimeStamp } from "@/components/TimeStamp";
import { cn } from "@/lib/utils";

type View = "todo" | "publish" | "working" | "all";
const VIEWS = [
  { key: "todo", label: "待处理", hint: "核实、定稿或继续制作", icon: ClipboardCheck },
  { key: "publish", label: "待发布", hint: "当前版本成片已完成", icon: CheckCircle2 },
  { key: "working", label: "制作中", hint: "研究、写稿与出片", icon: Activity },
  { key: "all", label: "全部视频", hint: "当前未归档的视频", icon: Layers },
] as const;
const PRIORITY = ["failed", "retry_render", "review", "recheck", "plan", "approve", "render", "publish", "working", "published"];
const PAGE = 12;
const PREVIEW = 1;
const INDEPENDENT = "_independent";
const STATUS: Record<BoardItem["action"], string> = {
  failed: "任务失败", retry_render: "出片未完成", review: "待核实", recheck: "待复核", plan: "待确认方案",
  approve: "待定稿", render: "待出片", publish: "待发布", working: "制作中", published: "已发布",
};

function destination(item: BoardItem): string {
  const tab = ["review", "recheck"].includes(item.action) ? "review"
    : ["publish", "published"].includes(item.action) ? "publish"
      : ["render", "retry_render"].includes(item.action) || item.stage === "rendering" ? "video" : "draft";
  return `/video/${item.script_id}?tab=${tab}`;
}

function VideoRow({ item }: { item: BoardItem }) {
  const label = item.action === "review" ? item.credibility?.tier === "unaudited" ? "审计未完成"
    : item.credibility?.tier === "serious" ? "事实需修正" : "待核实" : STATUS[item.action];
  return <article className="p-4 space-y-2" aria-label={`视频 ${item.title}`}>
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className={cn("inline-flex items-center gap-1.5 font-medium", item.group === "todo"
        ? item.action === "failed" || item.credibility?.tier === "serious" ? "text-danger" : "text-warning" : item.group === "done" ? "text-text-muted" : "text-accent")}>
        <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />{label}
      </span>
      {item.action !== "review" && item.group !== "working" && item.credibility ? <CredibilityBadge value={item.credibility} /> : null}
      {item.version > 0 ? <span className="text-text-muted">v{item.version}</span> : null}
      {item.has_previous_video ? <span className="text-text-muted" title="旧版成片保留，当前文稿按自己的版本判断进度">有旧版成片</span> : null}
    </div>
    <Link to={destination(item)} className="block text-sm leading-6 font-medium hover:text-accent hover:underline break-words">{item.title || "未命名视频"}</Link>
    <p className="text-xs leading-5 text-text-muted line-clamp-2 break-words" title={item.note}>{item.note}</p>
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 pt-1">
      <div className="min-w-0 text-xs text-text-muted space-y-0.5">
        <p className="truncate max-w-72" title={`${item.job_name} · ${item.column_label}`}>{item.job_name} · {item.column_label}</p>
        <TimeStamp value={item.updated_at || item.created_at} staleDays={Infinity} />
      </div>
      <Link to={destination(item)} aria-label={`${item.action_label}：${item.title}`} className="btn text-xs shrink-0">{item.action_label}<ArrowUpRight className="h-3.5 w-3.5" aria-hidden /></Link>
    </div>
  </article>;
}

export function HomeBoardPage() {
  const [board, setBoard] = useState<VideoBoard | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const [advanced, setAdvanced] = useState(false);
  const [automation, setAutomation] = useState(false);
  const [params, setParams] = useSearchParams();
  const flight = useRef<AbortController | null>(null);
  const automationPanel = useRef<HTMLDetailsElement | null>(null);
  const matrix = params.get("matrix") ?? "";
  const view = (VIEWS.some((v) => v.key === params.get("view")) ? params.get("view") : "todo") as View;
  const q = params.get("q") ?? "", column = params.get("column") ?? "", tier = params.get("tier") ?? "", range = params.get("range") ?? "";

  const load = useCallback(async () => {
    if (flight.current) return;
    const controller = new AbortController();
    flight.current = controller;
    setLoading(true);
    try {
      const next = await getVideoBoard(controller.signal);
      if (!Array.isArray(next?.items)) throw new Error("看板数据尚未就绪，请稍后刷新");
      if (!controller.signal.aborted) { setBoard(next); setError(""); }
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "读取失败");
    } finally {
      if (flight.current === controller) { flight.current = null; setLoading(false); }
    }
  }, []);
  useEffect(() => {
    load();
    const refresh = () => { if (!document.hidden) void load(); };
    const timer = window.setInterval(refresh, 15000);
    document.addEventListener("visibilitychange", refresh);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", refresh); flight.current?.abort(); flight.current = null; };
  }, [load]);
  useEffect(() => setLimit(PAGE), [matrix, view, q, column, tier, range]);

  const set = (key: string, value: string) => setParams((previous) => {
    const next = new URLSearchParams(previous);
    if (value) next.set(key, value); else next.delete(key);
    return next;
  }, { replace: true });
  const chooseMatrix = (key: string) => setParams((previous) => {
    const next = new URLSearchParams(previous);
    if (key) next.set("matrix", key); else next.delete("matrix");
    ["q", "column", "tier", "range"].forEach((k) => next.delete(k));
    return next;
  }, { replace: true });
  const clearFilters = () => setParams((previous) => {
    const next = new URLSearchParams(previous);
    ["q", "column", "tier", "range"].forEach((k) => next.delete(k));
    return next;
  }, { replace: true });

  const items = useMemo(() => board?.items ?? [], [board]);
  const matrices = useMemo(() => {
    const names = new Map((board?.matrices ?? []).map((m) => [m.key, m.name]));
    for (const item of items) if (!names.has(item.matrix || INDEPENDENT)) names.set(item.matrix || INDEPENDENT, item.matrix_name || "独立视频");
    return [...names].map(([key, name]) => ({ key, name, count: items.filter((i) => (i.matrix || INDEPENDENT) === key).length }))
      .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "zh-CN"));
  }, [board, items]);
  const scope = items.filter((i) => !matrix || (i.matrix || INDEPENDENT) === matrix);
  const scopeName = matrix === INDEPENDENT ? "独立视频" : matrix ? matrices.find((m) => m.key === matrix)?.name ?? "该矩阵" : "全部矩阵";
  const counts = { todo: scope.filter((i) => i.group === "todo").length, publish: scope.filter((i) => i.group === "publish").length,
    working: scope.filter((i) => i.group === "working").length, all: scope.length };
  const shown = scope.filter((i) => (view === "all" || i.group === view) && (!column || i.column_key === column)
    && (!tier || i.credibility?.tier === tier) && inRange(i.created_at, range) && matchesText(q, i.title, i.job_name))
    .sort((a, b) => PRIORITY.indexOf(a.action) - PRIORITY.indexOf(b.action) || b.updated_at.localeCompare(a.updated_at));
  const groups = matrices.map((m) => ({ ...m, items: shown.filter((i) => (i.matrix || INDEPENDENT) === m.key) }))
    .filter((m) => m.items.length).sort((a, b) => PRIORITY.indexOf(a.items[0].action) - PRIORITY.indexOf(b.items[0].action) || b.items.length - a.items.length);
  const filtered = Boolean(q || column || tier || range);

  return <div className="space-y-5">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div><h1 className="text-xl font-semibold tracking-tight">视频看板</h1><p className="text-xs text-text-muted mt-1">按矩阵查看视频进度，处理下一步工作。</p></div>
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn text-xs" onClick={() => { setAutomation(true); window.requestAnimationFrame(() => automationPanel.current?.scrollIntoView({ behavior: "smooth", block: "start" })); }}>自动化</button>
        <button className="btn text-xs" onClick={load} disabled={loading} aria-label="刷新看板"><RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />刷新</button>
        <Link to="/outputs" className="btn text-xs">产出记录</Link><Link to="/topics" className="btn btn-primary text-xs">选择选题<ArrowUpRight className="h-3.5 w-3.5" /></Link>
      </div>
    </header>
    <AlertsCard compact />
    {error ? <div><ErrorState error={error} onRetry={load} compact />{board ? <p className="text-xs text-warning mt-2">暂时显示上次读取的数据。</p> : null}</div> : null}
    {!board && !error ? <div className="card p-8 text-sm text-text-muted" role="status">正在读取视频进度…</div> : null}
    {board ? <div className="grid gap-5 xl:grid-cols-[11.5rem_minmax(0,1fr)] items-start">
      <aside className="hidden xl:block card p-2 xl:sticky xl:top-0" aria-label="矩阵导航">
        <p className="px-3 pt-2 pb-3 text-xs text-text-muted">矩阵</p>
        {[{ key: "", name: "全部矩阵", count: items.length }, ...matrices].map((m) => <button key={m.key}
          aria-pressed={matrix === m.key} aria-label={`${m.name}，${m.count} 个视频`} onClick={() => chooseMatrix(m.key)}
          className={cn("w-full flex items-center justify-between gap-2 rounded-md px-3 py-2.5 text-left text-xs transition-colors", matrix === m.key ? "bg-accent/10 text-accent font-medium" : "hover:bg-bg-hover text-text-muted")}>
          <span className="truncate">{m.name}</span><span className="tabular-nums text-[11px]">{m.count}</span>
        </button>)}
      </aside>
      <div className="min-w-0 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2"><h2 className="font-medium">{scopeName}</h2>{matrix && matrix !== INDEPENDENT ? <Link to={`/matrices/${matrix}`} className="text-text-muted hover:text-accent" aria-label="查看矩阵配置"><ArrowUpRight className="h-4 w-4" /></Link> : null}</div>
          <span className="text-xs text-text-muted">{board.updated_at ? <TimeStamp value={board.updated_at} label="更新" staleDays={Infinity} /> : "自动更新"}</span>
          <select className="input text-sm xl:hidden" aria-label="选择矩阵" value={matrix} onChange={(e) => chooseMatrix(e.target.value)}><option value="">全部矩阵（{items.length}）</option>{matrices.map((m) => <option key={m.key} value={m.key}>{m.name}（{m.count}）</option>)}</select>
        </div>
        <section className="grid grid-cols-2 md:grid-cols-4 gap-2" aria-label="当前矩阵进度">
          {VIEWS.map((v) => <button key={v.key} onClick={() => set("view", v.key)} aria-pressed={view === v.key} aria-label={`${v.label} ${counts[v.key]}`}
            className={cn("card p-3 text-left transition-colors", view === v.key ? "border-accent/70 bg-accent/5" : "hover:bg-bg-hover")}>
            <span className="flex items-center justify-between text-xs text-text-muted">{v.label}<v.icon className={cn("h-4 w-4", view === v.key && "text-accent")} /></span>
            <span className="block text-2xl font-semibold tabular-nums mt-1">{counts[v.key]}</span><span className="block text-[11px] text-text-muted mt-1">{v.hint}</span>
          </button>)}
        </section>
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <label className="relative flex-1 min-w-40 max-w-sm"><Search className="absolute top-2.5 left-3 h-3.5 w-3.5 text-text-muted" /><input aria-label="搜索视频" type="search" placeholder="搜索视频标题或来源任务" value={q} onChange={(e) => set("q", e.target.value)} className="input py-2 pl-9 text-xs" /></label>
            <button className="btn text-xs" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}><SlidersHorizontal className="w-3.5 h-3.5" />筛选{column || tier || range ? " · 已启用" : ""}</button>
            <span className="text-xs text-text-muted ml-auto">{filtered ? `筛选后 ${shown.length} 个` : `${shown.length} 个视频`}</span>{filtered ? <button className="text-xs text-accent hover:underline" onClick={clearFilters}>清除筛选</button> : null}
          </div>
          {advanced ? <div className="flex flex-wrap gap-2">
            <select className="input w-auto text-xs py-1.5" aria-label="视频类型" value={column} onChange={(e) => set("column", e.target.value)}><option value="">全部视频类型</option>{[...new Map(scope.map((i) => [i.column_key, i.column_label]))].map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
            <select className="input w-auto text-xs py-1.5" aria-label="可信度" value={tier} onChange={(e) => set("tier", e.target.value)}><option value="">全部可信度</option>{Object.entries(TIER_LABEL).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
            <select className="input w-auto text-xs py-1.5" aria-label="创建时间" value={range} onChange={(e) => set("range", e.target.value)}><option value="">全部创建时间</option>{RANGE_FACET.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
          </div> : null}
        </div>
        {!shown.length ? <section className="card p-8 text-center space-y-2" aria-label="视频列表为空"><CheckCircle2 className="h-6 w-6 text-text-muted mx-auto" /><p className="text-sm">{filtered ? "没有符合筛选的视频" : view === "todo" ? "当前矩阵没有待处理的视频" : view === "publish" ? "当前没有待发布的视频" : view === "working" ? "当前没有视频正在制作" : "这个矩阵还没有视频"}</p><p className="text-xs text-text-muted">{counts.all ? "可切换上方进度，查看其他视频。" : "选题完成后，可以从这里跟进视频进度。"}</p>{filtered ? <button className="btn text-xs" onClick={clearFilters}>清除筛选</button> : counts.all ? <button className="btn text-xs" onClick={() => set("view", "all")}>查看全部视频</button> : <Link className="btn text-xs" to="/topics">去选题</Link>}</section> : <div className={cn("grid items-start gap-4", !matrix && "lg:grid-cols-2")}>
          {groups.map((group) => <section key={group.key} className="card overflow-hidden" aria-label={`矩阵视频 ${group.name}`}>
            {!matrix ? <header className="px-4 py-3 border-b border-border flex items-center justify-between gap-2"><button className="font-medium text-sm hover:text-accent text-left" onClick={() => chooseMatrix(group.key)}>{group.name}<span className="text-xs text-text-muted font-normal ml-2">{group.items.length}</span></button><button className="text-text-muted hover:text-accent p-1" onClick={() => chooseMatrix(group.key)} aria-label={`进入${group.name}`}><ChevronRight className="h-4 w-4" /></button></header> : null}
            <div className="divide-y divide-border">{group.items.slice(0, matrix ? limit : PREVIEW).map((item) => <VideoRow key={item.script_id} item={item} />)}</div>
            {group.items.length > (matrix ? limit : PREVIEW) ? <button className="w-full text-xs text-accent hover:bg-bg-hover p-3 border-t border-border" onClick={() => matrix ? setLimit(limit + PAGE) : chooseMatrix(group.key)}>{matrix ? "继续显示" : "进入矩阵，查看其余"} {group.items.length - (matrix ? limit : PREVIEW)} 个视频<ChevronRight className="inline h-3.5 w-3.5 ml-1" /></button> : null}
          </section>)}
        </div>}
      </div>
    </div> : null}
    <details ref={automationPanel} className="card scroll-mt-4" open={automation} onToggle={(e) => setAutomation(e.currentTarget.open)}>
      <summary className="cursor-pointer p-4 text-sm font-medium">自动化管理<span className="text-xs font-normal text-text-muted ml-3">全局计划与最近批次</span></summary>
      {automation ? <div className="p-4 pt-0"><p className="text-xs text-text-muted mb-3">这里管理全局自动化计划；批次中的视频进度以当前文稿版本为准。</p><AutopilotCard currentItems={items} /></div> : null}
    </details>
  </div>;
}
