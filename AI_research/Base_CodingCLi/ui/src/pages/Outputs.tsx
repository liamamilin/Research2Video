import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowUpRight, ChevronRight, RefreshCw, Search, SlidersHorizontal } from "lucide-react";
import { listOutputs, type OutputScript, type Outputs } from "@/api/outputs";
import { CredibilityBadge } from "@/components/CredibilityBadge";
import { ErrorState } from "@/components/ErrorState";
import { matchesText, useFilters } from "@/components/FilterBar";
import { TimeStamp } from "@/components/TimeStamp";
import { errorMessage } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { dayLabel, STAGE_CLASS, STAGE_LABEL, stageOf } from "./VideoScripts";

const RANGES = [["", "全部时间"], ["7", "近 7 天"], ["30", "近 30 天"], ["90", "近 90 天"]];
const RENDERS = [["", "全部成片状态"], ["none", "还没出片"], ["working", "出片中"], ["done", "成片完成"], ["failed", "出片未完成"], ["published", "已发布"]];
const WORKING = ["SUBMITTING", "QUEUED", "RUNNING", "DOWNLOADING"];
const MANUAL = "_manual", UNGROUPED = "_none", PAGE = 12;
const ownerKey = (s: OutputScript) => s.matrix || (s.job ? UNGROUPED : MANUAL);
const timeOf = (iso: string) => new Date(iso.replace(/([+-]\d{2})(\d{2})$/, "$1:$2")).getTime();
const reportHref = (path: string) => `/reports/view?path=${encodeURIComponent(path.replace(/^output\//, ""))}`;
const currentRender = (s: OutputScript) => s.version && s.render?.version && s.version !== s.render.version ? null : s.render;
const hasVideo = (s: OutputScript) => s.has_video ?? currentRender(s)?.status === "COMPLETED";
const matrixName = (data: Outputs, key: string) => key === MANUAL ? "独立选题" : key === UNGROUPED ? "未分组任务" : data.matrices.find((m) => m.key === key)?.name || key;
const jobName = (data: Outputs, job: string) => data.jobs.find((j) => j.job === job)?.name || job;
function renderState(s: OutputScript): string {
  if (s.published) return "published";
  const render = currentRender(s);
  if (!render) return "none";
  if (WORKING.includes(render.status)) return "working";
  return render.status === "COMPLETED" ? "done" : "failed";
}

function ScriptLine({ s, data, showMatrix = false }: { s: OutputScript; data: Outputs; showMatrix?: boolean }) {
  const stage = stageOf({ ...s, render: currentRender(s) });
  const video = s.status === "APPROVED" && hasVideo(s);
  const summary = data.jobs.find((j) => j.job === s.job);
  const runHref = s.matrix ? `/matrices/${encodeURIComponent(s.matrix)}?tab=runs&job=${encodeURIComponent(s.job)}` : `/jobs/${encodeURIComponent(s.job)}`;
  return <li className="p-4 space-y-2.5" aria-label={`文稿 ${s.title}`}>
    <div className="flex flex-wrap items-center gap-2 text-xs"><span className={cn("px-1.5 py-0.5 rounded border whitespace-nowrap", STAGE_CLASS[stage])}>{STAGE_LABEL[stage]}</span><CredibilityBadge value={s.credibility} />{s.version ? <span className="text-text-muted">v{s.version}</span> : null}{s.has_previous_video ? <span className="text-text-muted">有旧版成片</span> : null}{s.archived ? <span className="text-text-muted">已归档</span> : null}</div>
    <Link className="block text-sm leading-6 font-medium hover:underline hover:text-accent break-words" to={`/video/${s.id}`}>{s.title || "未命名文稿"}</Link>
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-text-muted leading-5">
      {showMatrix ? <span>{matrixName(data, ownerKey(s))} ·</span> : null}
      {s.job ? <Link className="hover:text-accent hover:underline" to={`/jobs/${encodeURIComponent(s.job)}`}>{jobName(data, s.job)}</Link> : <span>独立选题</span>}
      {s.report_path ? <><span>·</span><Link className="text-accent hover:underline" to={reportHref(s.report_path)}>{s.report_date || "原始"} 的报告<ArrowUpRight className="inline h-3 w-3 ml-1" /></Link></> : null}
    </div>
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <div className="text-xs text-text-muted space-y-1"><p title={s.production_guessed ? "历史文稿，根据配方与来源任务匹配的配置" : undefined}>{s.production_name || s.column_key || "未指定视频配置"}{s.auto ? " · 自动生成" : ""}</p><TimeStamp value={s.created_at} label="创建" staleDays={Infinity} /></div>
      <div className="flex items-center gap-3 text-xs"><Link className={cn(video || s.published ? "text-text-muted hover:text-accent hover:underline" : "btn text-xs")} to={`/video/${s.id}?tab=draft`}>查看文稿</Link>{video || s.published ? <Link className="btn text-xs" aria-label={`${s.published ? "发布记录" : "查看成片"}：${s.title}`} to={`/video/${s.id}?tab=${s.published ? "publish" : "video"}`}>{s.published ? "发布记录" : "查看成片"}<ArrowUpRight className="h-3.5 w-3.5" /></Link> : null}</div>
    </div>
    <details className="text-xs text-text-muted"><summary className="cursor-pointer w-fit hover:text-text" aria-label={`来源详情：${s.title}`}>来源详情</summary><div className="mt-2 rounded-md bg-bg-hover/40 p-3 space-y-2 break-words">
      <p>矩阵：{s.matrix ? <Link className="text-accent hover:underline" to={`/matrices/${encodeURIComponent(s.matrix)}`}>{matrixName(data, s.matrix)}</Link> : matrixName(data, ownerKey(s))}</p>
      {s.job ? <p>研究任务：<Link className="text-accent hover:underline" to={`/jobs/${encodeURIComponent(s.job)}`}>{s.job}</Link></p> : null}
      {s.report_path ? <p>来源报告：<Link className="text-accent hover:underline break-all" to={reportHref(s.report_path)}>{s.report_path}</Link></p> : <p>此文稿来自独立选题。</p>}
      {s.production_guessed ? <p>视频配置根据历史配方和来源匹配。</p> : null}
      {summary?.unwritten ? <p>该任务累计还有 {summary.unwritten} 次运行的报告未写视频 · <Link className="text-accent hover:underline" to={runHref}>查看运行记录</Link></p> : null}
    </div></details>
  </li>;
}

function ByOwner({ data, rows, matrix, onSelect, limit }: { data: Outputs; rows: OutputScript[]; matrix: string; onSelect: (key: string) => void; limit: number }) {
  if (matrix) return <ul className="card divide-y divide-border" aria-label="矩阵产出记录">{rows.slice(0, limit).map((s) => <ScriptLine key={s.id} s={s} data={data} />)}</ul>;
  const groups = new Map<string, OutputScript[]>();
  for (const s of rows) {
    const group = groups.get(ownerKey(s)) || [];
    group.push(s); groups.set(ownerKey(s), group);
  }
  return <div className="grid gap-4 xl:grid-cols-2 items-start" aria-label="按来源分组的产出">{[...groups].slice(0, limit).map(([key, scripts]) => {
    const reports = new Set(scripts.map((s) => s.report_path).filter(Boolean)).size;
    return <section key={key} className="card overflow-hidden" aria-label={`矩阵 ${key}`}>
      <h3><button className="w-full flex items-center gap-3 p-4 text-left hover:bg-bg-hover/50 border-b border-border" aria-label={`查看矩阵 ${matrixName(data, key)} 的产出`} onClick={() => onSelect(key)}>
        <span className="min-w-0 flex-1"><span className="block font-medium text-sm">{matrixName(data, key)}{data.matrices.find((m) => m.key === key)?.archived ? <span className="ml-2 text-xs text-text-muted font-normal">已归档</span> : null}</span><span className="block text-xs text-text-muted mt-1">{scripts.length} 篇文稿 · {reports} 份来源报告</span></span><ChevronRight className="h-4 w-4 text-text-muted shrink-0" />
      </button></h3>
      <ul><ScriptLine s={scripts[0]} data={data} /></ul>
      {scripts.length > 1 ? <button className="w-full border-t border-border px-4 py-3 text-xs text-accent hover:bg-bg-hover/50" onClick={() => onSelect(key)}>查看其余 {scripts.length - 1} 篇文稿<ChevronRight className="inline h-3.5 w-3.5 ml-1" /></button> : null}
    </section>;
  })}</div>;
}

export function OutputsPage() {
  const filters = useFilters(["matrix", "render", "range", "archived"]);
  const [params, setParams] = useSearchParams();
  const { q, matrix, render, range } = filters.values;
  const archived = ["1", "true"].includes(filters.values.archived);
  const view = params.get("view") === "time" ? "time" : "owner";
  const newest = params.get("order") !== "asc";
  const [result, setResult] = useState<{ archived: boolean; body: Outputs } | null>(null);
  const data = result?.archived === archived ? result.body : null;
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const flight = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    if (flight.current) return;
    const controller = new AbortController(); flight.current = controller; setLoading(true); setError("");
    try {
      const next = await listOutputs(archived, controller.signal);
      if (!Array.isArray(next?.scripts) || !Array.isArray(next?.jobs) || !Array.isArray(next?.matrices)) throw new Error("产出数据格式异常，请重试");
      if (!controller.signal.aborted) setResult({ archived, body: next });
    } catch (e) { if (!controller.signal.aborted) setError(errorMessage(e)); }
    finally { if (flight.current === controller) { flight.current = null; setLoading(false); } }
  }, [archived]);
  useEffect(() => {
    void load();
    const refresh = () => { if (!document.hidden) void load(); };
    const timer = window.setInterval(refresh, 30000);
    document.addEventListener("visibilitychange", refresh);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", refresh); flight.current?.abort(); flight.current = null; };
  }, [load]);
  useEffect(() => setLimit(PAGE), [matrix, render, range, archived, q, view, newest]);
  const set = (key: string, value: string) => setParams((old) => { const next = new URLSearchParams(old); if (value) next.set(key, value); else next.delete(key); return next; }, { replace: true });
  const scoped = useMemo(() => {
    if (!data) return [];
    const since = range ? Date.now() - Number(range) * 86400000 : 0;
    return data.scripts.filter((s) => (!since || timeOf(s.created_at) >= since) && (!render || renderState(s) === render)
      && matchesText(q, s.title, s.production_name, s.column_key, s.report_path, s.report_date, s.job, s.matrix, jobName(data, s.job), matrixName(data, ownerKey(s))))
      .sort((a, b) => newest ? b.created_at.localeCompare(a.created_at) : a.created_at.localeCompare(b.created_at));
  }, [data, range, render, newest, q]);
  const rows = useMemo(() => scoped.filter((s) => !matrix || ownerKey(s) === matrix), [scoped, matrix]);
  const owners = data ? [...new Set(data.scripts.map(ownerKey))].map((key) => ({ key, name: matrixName(data, key), count: scoped.filter((s) => ownerKey(s) === key).length })) : [];
  const dates = new Map<string, OutputScript[]>();
  for (const row of rows.slice(0, limit)) { const day = row.created_at.slice(0, 10); const group = dates.get(day) || []; group.push(row); dates.set(day, group); }
  const units = view === "owner" && !matrix ? new Set(rows.map(ownerKey)).size : rows.length;
  const clearConditions = () => setParams((old) => { const next = new URLSearchParams(old); ["q", "render", "range"].forEach(k => next.delete(k)); return next; }, { replace: true });
  const conditions = Boolean(q || render || range);
  return <div className="space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-xl font-semibold tracking-tight">产出</h1><p className="text-xs text-text-muted mt-1">按矩阵查看文稿与成片，追溯研究任务和原始报告。</p></div><div className="flex gap-2"><button className="btn text-xs" aria-label="刷新产出" disabled={loading} onClick={load}><RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />刷新</button><Link className="btn text-xs" to={archived ? "/matrices?archived=1" : matrix ? `/?matrix=${encodeURIComponent(matrix.startsWith("_") ? "_independent" : matrix)}&view=all` : "/"}>{archived ? "矩阵" : "视频看板"}<ArrowUpRight className="h-3.5 w-3.5" /></Link></div></header>
    <div className="flex flex-wrap items-center gap-2">
      <label className="relative flex-1 min-w-40 max-w-md"><Search className="absolute top-2.5 left-3 h-3.5 w-3.5 text-text-muted" /><input type="search" aria-label="搜索产出" className="input text-xs pl-9" placeholder="搜索文稿、来源任务或报告" value={q} onChange={(e) => filters.set("q", e.target.value)} /></label>
      <select aria-label="归档范围" className="input w-auto text-xs" value={archived ? "1" : ""} onChange={(e) => setParams((old) => { const next = new URLSearchParams(old); if (e.target.value) next.set("archived", "1"); else next.delete("archived"); next.delete("matrix"); return next; }, { replace: true })}><option value="">使用中</option><option value="1">只看已归档</option></select>
      <button className="btn text-xs" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}><SlidersHorizontal className="h-3.5 w-3.5" />筛选{render || range ? " · 已启用" : ""}</button>
      {conditions ? <button className="text-xs text-accent hover:underline" onClick={clearConditions}>清除条件</button> : null}
    </div>
    {advanced ? <div className="flex flex-wrap items-center gap-2 text-xs"><select aria-label="文稿时间" className="input w-auto text-xs" value={range} onChange={(e) => filters.set("range", e.target.value)}>{RANGES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select><select aria-label="成片状态" className="input w-auto text-xs" value={render} onChange={(e) => filters.set("render", e.target.value)}>{RENDERS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select></div> : null}
    {error ? <div><ErrorState error={error} onRetry={load} compact />{data ? <p className="text-xs text-warning mt-2">暂时显示上次读取的产出。</p> : null}</div> : null}
    {!data && !error ? <div className="card p-8 text-sm text-text-muted" role="status">正在读取产出与来源…</div> : null}
    {data ? <div className="grid lg:grid-cols-[12rem_minmax(0,1fr)] gap-5 items-start">
      <aside className="card p-2 hidden lg:block lg:sticky lg:top-6 max-h-[calc(100vh-5rem)] overflow-y-auto" aria-label="产出矩阵导航"><p className="px-3 py-2 text-xs text-text-muted">矩阵</p>{[{ key: "", name: "全部矩阵", count: scoped.length }, ...owners].map(m => <button key={m.key} aria-pressed={matrix === m.key} className={cn("w-full flex items-center justify-between gap-2 p-3 rounded-md text-left text-xs", matrix === m.key ? "bg-accent/10 text-accent font-medium" : "text-text-muted hover:bg-bg-hover hover:text-text")} onClick={() => filters.set("matrix", m.key)}><span>{m.name}</span><span className="tabular-nums shrink-0">{m.count}</span></button>)}</aside>
      <div className="min-w-0 space-y-4">
        <select aria-label="矩阵" className="input text-xs lg:hidden" value={matrix} onChange={(e) => filters.set("matrix", e.target.value)}><option value="">全部矩阵</option>{matrix && !owners.some(m => m.key === matrix) ? <option value={matrix}>{matrixName(data, matrix)}</option> : null}{owners.map(m => <option key={m.key} value={m.key}>{m.name}（{m.count}）</option>)}</select>
        <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2">{matrix ? <button className="text-text-muted hover:text-accent" aria-label="返回全部矩阵" onClick={() => filters.set("matrix", "")}><ArrowLeft className="h-4 w-4" /></button> : null}<h2 className="text-base font-medium">{matrix ? matrixName(data, matrix) : "全部矩阵"}</h2><span className="text-xs text-text-muted">{rows.length} 篇文稿</span></div><div className="flex gap-2 items-center"><div className="inline-flex rounded-md border border-border overflow-hidden text-xs" role="group" aria-label="展示方式">{(["owner", "time"] as const).map(v => <button key={v} aria-pressed={view === v} onClick={() => set("view", v)} className={cn("px-3 py-1.5", view === v ? "bg-accent-solid text-white" : "hover:bg-bg-hover")}>{v === "owner" ? "按归属" : "按时间"}</button>)}</div><button className="btn text-xs" onClick={() => set("order", newest ? "asc" : "desc")}>{newest ? "最新在前" : "最早在前"}</button></div></div>
        <section className="grid grid-cols-3 gap-2" aria-label="当前筛选的产出概况">{[{ label: "文稿", count: rows.length }, { label: "当前版本有成片", count: rows.filter(hasVideo).length }, { label: "来源报告", count: new Set(rows.map(s => s.report_path).filter(Boolean)).size }].map(s => <div key={s.label} className="card p-3"><p className="text-xs text-text-muted">{s.label}</p><p className="text-xl font-semibold mt-1 tabular-nums">{s.count}</p></div>)}</section>
        {matrix && !matrix.startsWith("_") ? <Link className="text-xs text-accent hover:underline inline-flex gap-1 items-center" to={`/matrices/${encodeURIComponent(matrix)}`}>进入矩阵，查看研究与运行记录<ArrowUpRight className="h-3.5 w-3.5" /></Link> : null}
        {!rows.length ? <section className="card p-8 text-center space-y-3"><p className="text-sm">{conditions || matrix ? "没有符合筛选的文稿" : archived ? "还没有归档的文稿" : "还没有产出文稿"}</p>{conditions ? <button className="btn text-xs" onClick={clearConditions}>清除条件</button> : matrix ? <button className="btn text-xs" onClick={() => filters.set("matrix", "")}>返回全部矩阵</button> : archived ? <button className="btn text-xs" onClick={filters.clear}>查看使用中的产出</button> : <Link className="btn text-xs" to="/matrices">从研究矩阵开始</Link>}</section>
          : view === "owner" ? <ByOwner data={data} rows={rows} matrix={matrix} onSelect={key => filters.set("matrix", key)} limit={limit} /> : <div className="space-y-4">{[...dates].map(([day, children]) => <section key={day} className="space-y-2"><h3 className="text-xs text-text-muted">{dayLabel(day)} · {children.length} 篇</h3><ul className="card divide-y divide-border">{children.map(s => <ScriptLine key={s.id} s={s} data={data} showMatrix />)}</ul></section>)}</div>}
        {units > limit ? <button className="btn text-xs w-full" onClick={() => setLimit(v => v + PAGE)}>加载更多{view === "owner" && !matrix ? "矩阵" : "文稿"}（已显示 {limit} / {units}）</button> : null}
      </div>
    </div> : null}
  </div>;
}
