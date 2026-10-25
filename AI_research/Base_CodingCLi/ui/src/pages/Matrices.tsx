import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Activity, ArrowUpRight, Boxes, ChevronRight, RefreshCw, Search, TriangleAlert } from "lucide-react";

import { runJob } from "@/api";
import { ApiError } from "@/api/client";
import { MatrixRuns } from "@/components/MatrixRuns";
import { MatrixWeek } from "@/components/MatrixWeek";
import { periodWords, PeriodEditor } from "@/components/PeriodEditor";
import {
  archiveMatrix, copyMatrix, createMatrix, deleteMatrix, getMatrix, listMatrices, pauseMatrix, resumeMatrix, splitKeywords, updateMatrix,
  type Matrix, type MatrixMember,
} from "@/api/matrices";
import { ProductionConfigs } from "@/components/ProductionConfigs";
import { listProduction, removeProduction, type ProductionConfig } from "@/api/production";
import { TimeStamp } from "@/components/TimeStamp";
import { ErrorState } from "@/components/ErrorState";
import { matchesText, useFilters } from "@/components/FilterBar";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage, useToast } from "@/lib/toast";
import { cn } from "@/lib/utils";

function summary(matrix: Matrix) {
  const running = matrix.members.filter((m) => m.is_running).length;
  const issues = matrix.members.filter((m) => !m.exists || (!m.is_running && m.enabled !== false && m.state?.last_status === "failed")).length;
  const scheduled = matrix.members.filter((m) => m.exists && m.enabled !== false && m.schedule?.enabled).length;
  const latest = matrix.members.map((m) => m.state?.last_run_at || "").filter(Boolean)
    .sort((a, b) => Date.parse(a) - Date.parse(b)).slice(-1)[0] || "";
  return { running, issues, scheduled, latest, attention: issues > 0 || matrix.errors.length > 0 };
}

function MatrixHealth({ matrix }: { matrix: Matrix }) {
  const s = summary(matrix);
  const label = matrix.archived ? "已归档" : s.running ? `${s.running} 个任务运行中`
    : s.attention ? `${s.issues || matrix.errors.length} 项需要关注` : matrix.paused?.length ? "定时已暂停"
      : s.scheduled ? "按计划运行" : matrix.members.length ? "手动运行" : "待添加任务";
  return <span className={cn("inline-flex items-center gap-1.5 text-xs", matrix.archived ? "text-text-muted"
    : s.running ? "text-accent" : s.attention ? "text-warning" : "text-text-muted")}>
    <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />{label}
  </span>;
}

function useCanEdit() {
  const role = useAuthStore((s) => s.user?.role);
  return role === "editor" || role === "admin";
}

function useIsAdmin() {
  return useAuthStore((s) => s.user?.role) === "admin";
}

function Keywords({ words, empty }: { words: string[]; empty: string }) {
  if (!words.length) return <span className="text-text-muted">{empty}</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {words.map((w) => (
        <span key={w} className="px-1.5 py-0.5 rounded bg-bg-hover text-xs">{w}</span>
      ))}
    </span>
  );
}

/** 新建一个空矩阵：名字、英文名（文件名和 Job 目录名）、领域、关键词 */
function NewMatrixForm({ onDone }: { onDone: (key: string) => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [words, setWords] = useState("");
  const [busy, setBusy] = useState(false);
  if (!open) {
    return (
      <button className="btn btn-primary text-xs" onClick={() => setOpen(true)}>
        新建矩阵
      </button>
    );
  }
  const submit = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const made = await createMatrix({ key, name, domain, keywords: splitKeywords(words) });
      onDone(made.key);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card p-4 space-y-3 text-sm w-full" aria-label="新建矩阵">
      <h2 className="font-medium">新建矩阵</h2>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="space-y-1 text-xs">矩阵名称<input className="input" placeholder="名字，如 医疗矩阵" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="space-y-1 text-xs">英文标识<input className="input" placeholder="英文名，如 medical" value={key} onChange={(e) => setKey(e.target.value)} /></label>
        <label className="space-y-1 text-xs">研究领域<input className="input" placeholder="领域，如 医疗健康" value={domain} onChange={(e) => setDomain(e.target.value)} /></label>
        <label className="space-y-1 text-xs">共用关键词<input className="input" placeholder="关键词，用逗号分开" value={words} onChange={(e) => setWords(e.target.value)} /></label>
      </div>
      <p className="text-xs text-text-muted">建好后再把 Job 放进来。想照着现有矩阵换关键词，去那个矩阵里点「换关键词复制」。</p>
      <div className="flex gap-2">
        <button className="btn btn-primary text-xs" disabled={busy || !name.trim() || !key.trim()} onClick={submit}>
          {busy ? "创建中…" : "创建矩阵"}
        </button>
        <button className="btn text-xs" disabled={busy} onClick={() => setOpen(false)}>
          取消
        </button>
      </div>
    </section>
  );
}

function MatrixMaintenance({ rows, canEdit }: { rows: Matrix[]; canEdit: boolean }) {
  const [configs, setConfigs] = useState<ProductionConfig[] | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [removing, setRemoving] = useState("");
  const toast = useToast();
  useEffect(() => {
    const controller = new AbortController();
    listProduction(undefined, controller.signal).then((list) => { if (!controller.signal.aborted) { if (!Array.isArray(list)) throw new Error("视频配置数据异常，请重试"); setConfigs(list); setError(""); } })
      .catch((e) => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => { controller.abort(); };
  }, [retry]);
  const grouped = new Set(rows.flatMap((m) => m.members.map((m) => m.job)));
  const stray = (configs ?? []).filter((c) => !grouped.has(c.job));
  const drop = async (c: ProductionConfig) => {
    if (removing || !window.confirm(`删掉「${c.name || c.key}」这套配置？已经写好的文稿和成片都还在。`)) return;
    setRemoving(c.key);
    try {
      await removeProduction(c.key);
      setConfigs((current) => current?.filter((x) => x.key !== c.key) ?? null);
    } catch (e) { toast.error(errorMessage(e)); }
    finally { setRemoving(""); }
  };
  return <div className="p-4 pt-0 space-y-3 text-xs">
    {error ? <ErrorState error={error} onRetry={() => setRetry((n) => n + 1)} compact /> : configs === null ? <p role="status" className="text-text-muted">正在检查视频配置…</p>
      : !stray.length ? <p className="text-text-muted">所有视频配置都能找到所属矩阵。</p> : <>
        <h3 className="font-medium">{stray.length} 套视频配置挂的 Job 不在任何矩阵里</h3>
        <p className="text-text-muted">可以把来源任务加入矩阵后继续使用，或删除不再需要的配置。</p>
        <ul className="divide-y divide-border">{stray.map((c) => <li key={c.key} className="flex flex-wrap items-center gap-2 py-2">
          <span>{c.name || c.key}</span><span className="font-mono text-text-muted break-all">{c.job}</span>
          {c.errors?.length ? <span className="text-warning">{c.errors.join("；")}</span> : null}
          {canEdit ? <button className="text-accent hover:underline ml-auto" disabled={!!removing} onClick={() => drop(c)}>删除</button> : null}
        </li>)}</ul>
      </>}
  </div>;
}

export function MatricesPage() {
  const navigate = useNavigate();
  const canEdit = useCanEdit();
  const [data, setData] = useState<{ matrices: Matrix[]; ungrouped: { job: string; name: string }[] } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [maintenance, setMaintenance] = useState(false);
  const flight = useRef<AbortController | null>(null);
  const filters = useFilters(["status", "archived"]);
  const [, setParams] = useSearchParams();
  const { q, status, archived } = filters.values;
  const load = useCallback(async () => {
    if (flight.current) return;
    const controller = new AbortController(); flight.current = controller; setLoading(true);
    try {
      const next = await listMatrices(controller.signal);
      if (!Array.isArray(next?.matrices) || !Array.isArray(next?.ungrouped)) throw new Error("矩阵数据格式异常，请重试");
      if (!controller.signal.aborted) { setData(next); setError(""); }
    } catch (e) { if (!controller.signal.aborted) setError(errorMessage(e)); }
    finally { if (flight.current === controller) { flight.current = null; setLoading(false); } }
  }, []);
  useEffect(() => {
    void load();
    const refresh = () => { if (!document.hidden) void load(); };
    const timer = window.setInterval(refresh, 30000);
    document.addEventListener("visibilitychange", refresh);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", refresh); flight.current?.abort(); flight.current = null; };
  }, [load]);
  const rows = data?.matrices ?? [];
  const active = rows.filter((m) => !m.archived);
  const scope = archived === "1" ? rows.filter((m) => m.archived) : active;
  const attention = active.filter((m) => summary(m).attention).length;
  const running = active.filter((m) => summary(m).running > 0).length;
  const shown = scope.filter((m) => (!status || (status === "attention" ? summary(m).attention : summary(m).running > 0))
    && matchesText(q, m.name, m.domain, m.key, ...m.keywords, ...m.members.flatMap((j) => [j.name, j.job])))
    .sort((a, b) => Number(summary(b).running > 0) - Number(summary(a).running > 0)
      || Number(summary(b).attention) - Number(summary(a).attention) || a.name.localeCompare(b.name, "zh-CN"));
  const changeScope = (archived: string, status = "") => setParams((old) => {
    const next = new URLSearchParams(old);
    if (archived) next.set("archived", archived); else next.delete("archived");
    if (status) next.set("status", status); else next.delete("status");
    return next;
  }, { replace: true });
  return <div className="space-y-5">
    <header className="flex flex-wrap items-start gap-3">
      <div className="flex-1 min-w-48"><h1 className="text-xl font-semibold tracking-tight">矩阵</h1><p className="text-xs text-text-muted mt-1">按研究领域组织任务，跟进报告与视频产出。</p></div>
      <button className="btn text-xs" aria-label="刷新矩阵" disabled={loading} onClick={load}><RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />刷新</button>
      {canEdit ? <NewMatrixForm onDone={(key) => navigate(`/matrices/${key}`)} /> : null}
    </header>
    {error ? <div><ErrorState error={error} onRetry={load} compact />{data ? <p className="text-xs text-warning mt-2">暂时显示上次读取的矩阵。</p> : null}</div> : null}
    {!data && !error ? <div className="card p-8 text-sm text-text-muted" role="status">正在读取矩阵和研究任务…</div> : null}
    {data ? <>
      <section className="grid grid-cols-3 gap-3" aria-label="矩阵概况">
        {[{ key: "", label: "使用中的矩阵", count: active.length, icon: Boxes }, { key: "attention", label: "需要关注", count: attention, icon: TriangleAlert }, { key: "running", label: "正在运行", count: running, icon: Activity }].map((s) => <button key={s.key} aria-label={`${s.label} ${s.count}`} aria-pressed={archived !== "1" && status === s.key}
          onClick={() => changeScope("", s.key)} className={cn("card text-left p-4", archived !== "1" && status === s.key ? "border-accent/70 bg-accent/5" : "hover:bg-bg-hover")}>
          <span className="flex items-center justify-between gap-2 text-xs text-text-muted">{s.label}<s.icon className="hidden sm:block h-4 w-4" /></span><span className="block text-2xl font-semibold mt-2 tabular-nums">{s.count}</span>
        </button>)}
      </section>
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative flex-1 min-w-40 max-w-sm"><Search className="absolute top-2.5 left-3 h-3.5 w-3.5 text-text-muted" /><input aria-label="搜索矩阵" type="search" placeholder="搜索矩阵、领域或研究任务" value={q} onChange={(e) => filters.set("q", e.target.value)} className="input text-xs pl-9" /></label>
        <select className="input w-auto text-xs" aria-label="矩阵范围" value={archived === "1" ? "1" : ""} onChange={(e) => changeScope(e.target.value)}><option value="">使用中（{active.length}）</option><option value="1">已归档（{rows.length - active.length}）</option></select>
        <span className="text-xs text-text-muted ml-auto">{shown.length} 个矩阵</span>{filters.active ? <button className="text-xs text-accent hover:underline" onClick={filters.clear}>清除筛选</button> : null}
      </div>
      {!shown.length ? <section className="card p-8 text-center space-y-2"><p className="text-sm">{q || status ? "没有符合条件的矩阵" : archived === "1" ? "还没有归档的矩阵" : "还没有矩阵"}</p><p className="text-xs text-text-muted">{q || status ? "调整搜索或清除筛选，再查看其他矩阵。" : "可以新建矩阵，再把研究任务加入其中。"}</p></section> : <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 items-start">
        {shown.map((m) => {
          const s = summary(m);
          return <section key={m.key} className="card overflow-hidden" aria-label={`矩阵 ${m.name}`}>
            <Link to={`/matrices/${m.key}`} aria-label={m.name} className="block p-4 space-y-3 hover:bg-bg-hover/50">
              <div className="flex items-start justify-between gap-2"><div className="min-w-0"><h2 className="font-medium text-sm leading-6">{m.name}</h2><p className="text-xs text-text-muted mt-1">{m.domain || "待设置研究领域"}</p></div><ChevronRight className="w-4 h-4 text-text-muted shrink-0 mt-1" /></div>
              <MatrixHealth matrix={m} />
              <p className="text-xs text-text-muted">{m.members.length} 个研究任务 · {s.scheduled} 个定时任务</p>
              {m.keywords.length ? <p className="text-xs text-text-muted truncate" title={m.keywords.join("、")}>{m.keywords.slice(0, 3).join(" · ")}{m.keywords.length > 3 ? ` 等 ${m.keywords.length} 个关键词` : ""}</p> : null}
              <div className="text-xs text-text-muted">{s.latest ? <TimeStamp value={s.latest} label="最近运行" staleDays={Infinity} /> : "尚无运行记录"}</div>
            </Link>
            <div className="flex items-center justify-between gap-2 px-4 py-3 border-t border-border text-xs"><Link className="text-accent hover:underline" to={`/matrices/${m.key}`}>研究任务<ChevronRight className="inline h-3.5 w-3.5 ml-1" /></Link>{!m.archived ? <Link className="text-text-muted hover:text-accent" to={`/?matrix=${encodeURIComponent(m.key)}&view=all`}>查看视频<ArrowUpRight className="inline h-3.5 w-3.5 ml-1" /></Link> : <span className="text-text-muted">已归档</span>}</div>
          </section>;
        })}
      </div>}
      {data.ungrouped.length ? <details className="card"><summary className="p-4 cursor-pointer text-sm">未分组任务<span className="text-xs text-text-muted ml-2">{data.ungrouped.length} 个</span></summary><div className="px-4 pb-4"><p className="text-xs text-text-muted mb-3">在矩阵设置中可将这些任务加入分组。</p><div className="flex flex-wrap gap-3">{data.ungrouped.map((j) => <Link key={j.job} className="text-xs text-accent hover:underline" to={`/jobs/${encodeURIComponent(j.job)}`}>{j.name}<ArrowUpRight className="inline h-3 w-3 ml-1" /></Link>)}</div></div></details> : null}
      <details className="card" open={maintenance} onToggle={(e) => setMaintenance(e.currentTarget.open)}><summary className="p-4 cursor-pointer text-sm">配置检查<span className="text-xs text-text-muted ml-2">检查没有归属矩阵的视频配置</span></summary>{maintenance ? <MatrixMaintenance rows={rows} canEdit={canEdit} /> : null}</details>
    </> : null}
  </div>;
}

/** Job 自己的关键词：有的 Job 有两百多个，只列前几个，其余点开看 */
const KEYWORDS_SHOWN = 6;
function MemberKeywords({ words }: { words: string[] }) {
  const [all, setAll] = useState(false);
  const more = words.length - KEYWORDS_SHOWN;
  return (
    <div className="text-xs mt-1">
      <span className="text-text-muted">自己的关键词：</span>
      {(all || more <= 0 ? words : words.slice(0, KEYWORDS_SHOWN)).join("、")}
      {more > 0 ? (
        <button className="ml-1 text-accent hover:underline" onClick={() => setAll(!all)}>
          {all ? "收起" : `…共 ${words.length} 个，展开`}
        </button>
      ) : null}
    </div>
  );
}

type RowConfigs = { count: number; auto: number } | null;

function MemberCard({ member, matrixKey, configs, canEdit, isAdmin, onChanged }: {
  member: MatrixMember; matrixKey: string; configs: RowConfigs; canEdit: boolean; isAdmin: boolean; onChanged: (m?: Matrix) => void;
}) {
  const state = member.state;
  const toast = useToast();
  const [editingPeriod, setEditingPeriod] = useState(false);
  const [starting, setStarting] = useState(false);
  const runnable = canEdit && member.exists && member.enabled !== false && !member.is_running;
  const run = async () => {
    if (starting) return;
    const spend = member.searches_per_run ? `上次用了 ${member.searches_per_run} 次搜索。` : "";
    if (!window.confirm(`运行「${member.name}」一次？会花搜索额度，${spend}跑完后到视频配置里写成视频。`)) return;
    setStarting(true);
    try { await runJob(member.job); toast.success("开始运行了"); onChanged(); }
    catch (e) { toast.error(errorMessage(e)); }
    finally { setStarting(false); }
  };
  const latest = member.latest_report;
  const reportNote = member.is_running || !latest || !configs?.count ? null : latest.scripts ? `最新报告已写 ${latest.scripts} 篇`
    : configs.auto ? "最新报告还没写，会自动写" : "最新报告还没写视频 →";
  const status = member.is_running ? "运行中" : !member.exists ? "任务文件缺失" : member.enabled === false ? "已停用"
    : state?.last_status === "failed" ? "上次运行失败" : state?.last_status === "success" ? "上次运行完成"
      : state?.last_status === "cancelled" ? "上次已取消" : "还没跑过";
  const videoPath = `/matrices/${encodeURIComponent(matrixKey)}/video/${encodeURIComponent(member.job)}`;
  return <article className="card p-4 space-y-3" aria-label={`研究任务 ${member.name}`}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0 space-y-1">{member.exists ? <Link className="font-medium text-sm hover:text-accent hover:underline break-words" to={`/jobs/${encodeURIComponent(member.job)}`}>{member.name}</Link> : <span className="font-medium text-sm text-warning">{member.name}</span>}
        <div className={cn("flex flex-wrap items-center gap-2 text-xs", member.is_running ? "text-accent" : !member.exists || state?.last_status === "failed" ? "text-warning" : "text-text-muted")}><span>{status}</span>{state?.last_run_at && !member.is_running ? <TimeStamp value={state.last_run_at} staleDays={Infinity} /> : null}</div>
      </div>
      {runnable ? <button className="btn text-xs" disabled={starting} aria-label={`运行 ${member.name} 一次`} onClick={run}>{starting ? "正在启动…" : "运行一次"}</button> : member.is_running ? <Link to={`/jobs/${encodeURIComponent(member.job)}`} className="btn text-xs">查看进度<ArrowUpRight className="h-3.5 w-3.5" /></Link> : null}
    </div>
    <div className="grid gap-4 sm:grid-cols-3 text-xs">
      <div className="space-y-1.5"><p className="text-text-muted">运行周期</p><p>{member.exists && member.enabled === false ? "已停用" : periodWords(member)}</p>
        {member.schedule?.enabled && member.enabled !== false && member.schedule.next_runs[0] ? <div><TimeStamp value={member.schedule.next_runs[0]} label="下次" staleDays={Infinity} /></div> : null}
        {isAdmin && member.exists && !editingPeriod ? <button className="text-accent hover:underline" aria-label={`改 ${member.name} 的周期`} onClick={() => setEditingPeriod(true)}>改周期</button> : null}
      </div>
      <div className="space-y-1.5"><p className="text-text-muted">研究报告</p>{latest ? <><Link className="text-accent hover:underline inline-flex items-center gap-1" to={`/reports/view?path=${encodeURIComponent(latest.path.replace(/^output\//, ""))}`}>查看报告<ArrowUpRight className="h-3 w-3" /></Link><div><TimeStamp value={latest.ts} staleDays={Infinity} /></div></> : <p className="text-text-muted">尚无成功报告</p>}
        {state?.last_status === "failed" && !member.is_running ? <Link className="text-warning hover:underline" to={`/jobs/${encodeURIComponent(member.job)}`}>查看失败原因</Link> : null}
      </div>
      <div className="space-y-1.5"><p className="text-text-muted">视频制作</p>{member.exists ? <Link className="text-accent hover:underline inline-flex items-center gap-1" to={videoPath}>视频配置<ArrowUpRight className="h-3 w-3" /></Link> : null}
        <p className="text-text-muted">{configs === null ? "配置数量待同步" : configs.count ? `${configs.count} 套${configs.auto ? `，${configs.auto} 套自动写` : ""}` : "还没配"}</p>
        {reportNote ? !latest!.scripts && !configs!.auto ? <Link className="text-accent hover:underline" to={videoPath}>{reportNote}</Link> : <p className="text-text-muted">{reportNote}</p> : null}
      </div>
    </div>
    {editingPeriod ? <PeriodEditor matrixKey={matrixKey} member={member} onCancel={() => setEditingPeriod(false)} onDone={(m) => { setEditingPeriod(false); onChanged(m); }} /> : null}
    <details className="text-xs text-text-muted border-t border-border pt-2"><summary className="cursor-pointer">任务详情</summary><div className="mt-2 space-y-2"><p className="font-mono break-all">{member.job}</p>{member.description ? <p className="leading-5">{member.description}</p> : null}{member.keywords?.length ? <MemberKeywords words={member.keywords} /> : null}{member.topics ? <p>选题来源：{member.topics}</p> : null}{state?.last_error ? <p className="break-words text-warning leading-5">{state.last_error}</p> : null}</div></details>
  </article>;
}

/** 每月搜索上限：用完这个矩阵的 Job 都不再启动（定时和手动都停），到下个月自动恢复 */
function MonthlyLimit({ matrix, isAdmin, onSaved }: { matrix: Matrix; isAdmin: boolean; onSaved: (m: Matrix) => void }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const cap = matrix.monthly_searches ?? 0;
  const used = matrix.searches_this_month ?? 0;
  const save = async () => {
    if (busy) return;
    const n = Number(value);
    if (!Number.isInteger(n) || n < 0) { toast.error("填每月最多几次搜索，0 是不限"); return; }
    setBusy(true);
    try {
      onSaved(await updateMatrix(matrix.key, { name: matrix.name, domain: matrix.domain, keywords: matrix.keywords, monthly_searches: n }));
      setEditing(false);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="text-sm">
      <span className="text-text-muted text-xs">每月搜索上限：</span>
      {cap ? (
        <span className={used >= cap ? "text-danger" : used >= cap * 0.8 ? "text-warning" : ""}>
          {cap} 次，本月已用 {used} 次{used >= cap ? "，这个矩阵的 Job 本月不会再启动" : ""}
        </span>
      ) : (
        <span className="text-text-muted">未设置上限</span>
      )}
      {isAdmin && !editing ? (
        <button className="ml-2 text-xs text-accent hover:underline" onClick={() => { setValue(String(cap || "")); setEditing(true); }}>
          {cap ? "改上限" : "设上限"}
        </button>
      ) : null}
      {editing ? (
        <span className="ml-2 inline-flex items-center gap-2">
          <input aria-label="每月搜索上限" className="input w-28" type="number" min={0} value={value} onChange={(e) => setValue(e.target.value)} />
          <button className="btn btn-primary text-xs" disabled={busy} onClick={save}>{busy ? "保存中…" : "保存"}</button>
          <button className="btn text-xs" disabled={busy} onClick={() => setEditing(false)}>取消</button>
          <span className="text-xs text-text-muted">0 是不限；到了上限定时和手动都不再启动，下个月自动恢复</span>
        </span>
      ) : null}
    </div>
  );
}

function MatrixVideoDefaults({ matrix, canEdit, onSaved }: { matrix: Matrix; canEdit: boolean; onSaved: (m: Matrix) => void }) {
  const toast = useToast();
  const [speed, setSpeed] = useState(String(matrix.render?.video_speed ?? 1));
  const [busy, setBusy] = useState(false);
  useEffect(() => setSpeed(String(matrix.render?.video_speed ?? 1)), [matrix.key, matrix.render?.video_speed]);
  const save = async () => {
    if (busy) return;
    setBusy(true);
    try {
      onSaved(await updateMatrix(matrix.key, { name: matrix.name, domain: matrix.domain, keywords: matrix.keywords, render: { ...matrix.render, video_speed: Number(speed) } }));
      toast.success("默认输出倍速已保存，下次生成视频生效");
    } catch (e) { toast.error(errorMessage(e)); }
    finally { setBusy(false); }
  };
  return <section className="card p-4 space-y-3" aria-label="视频生成默认设置">
    <h2 className="font-medium text-sm">视频生成默认设置</h2>
    <p className="text-xs text-text-muted">本矩阵内所有研究任务的视频统一继承；任务单独设置倍速时优先使用任务设置。新加入的任务也会继承。</p>
    <div className="flex flex-wrap items-center gap-3 text-sm">
      <label htmlFor="matrix-video-speed">默认输出倍速</label>
      <select id="matrix-video-speed" className="input w-auto" value={speed} disabled={!canEdit || busy} onChange={(e) => setSpeed(e.target.value)}>
        {[0.5, 1, 1.25, 1.5, 2].map((value) => <option key={value} value={value}>{value} 倍速</option>)}
      </select>
      {canEdit ? <button className="btn btn-primary text-xs" disabled={busy || Number(speed) === (matrix.render?.video_speed ?? 1)} onClick={save}>{busy ? "保存中…" : "保存倍速"}</button> : null}
    </div>
    <p className="text-xs text-text-muted">保存后，新生成或重新生成的视频生效。已有成片和已提交的出片任务保持原设置。</p>
  </section>;
}

/** 换关键词复制：挑出能复制的 Job，用不分领域的模板重新建一份，放进新矩阵；新 Job 都是手动，不会自己跑 */
function CopyForm({ matrix, onDone }: { matrix: Matrix; onDone: (key: string) => void }) {
  const toast = useToast();
  const copyable = matrix.members.filter((m) => m.copyable);
  const stuck = matrix.members.filter((m) => !m.copyable);
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [words, setWords] = useState("");
  const [picked, setPicked] = useState<string[]>(copyable.map((m) => m.job));
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      const made = await copyMatrix(matrix.key, { key, name, domain, keywords: splitKeywords(words), jobs: picked });
      toast.success(`已复制成「${made.name}」：新 Job 都是手动，点运行才会跑`);
      onDone(made.key);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card p-3 space-y-2 text-sm" aria-label="换关键词复制">
      <h2 className="font-medium">换关键词复制</h2>
      {copyable.length ? (
        <>
          <div className="grid gap-2 sm:grid-cols-2">
            <input className="input" placeholder="新矩阵的名字，如 医疗矩阵" value={name} onChange={(e) => setName(e.target.value)} />
            <input className="input" placeholder="英文名，如 medical" value={key} onChange={(e) => setKey(e.target.value)} />
            <input className="input" placeholder="领域，如 医疗健康" value={domain} onChange={(e) => setDomain(e.target.value)} />
            <input className="input" placeholder="关键词，用逗号分开" value={words} onChange={(e) => setWords(e.target.value)} />
          </div>
          <fieldset className="space-y-1 text-xs">
            <legend className="text-text-muted">带上哪些 Job</legend>
            {copyable.map((m) => (
              <label key={m.job} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={picked.includes(m.job)}
                  onChange={(e) => setPicked(e.target.checked ? [...picked, m.job] : picked.filter((j) => j !== m.job))}
                />
                {m.name}
              </label>
            ))}
          </fieldset>
          <button
            className="btn btn-primary text-xs"
            disabled={busy || !key.trim() || !name.trim() || !domain.trim() || !splitKeywords(words).length || !picked.length}
            onClick={submit}
          >
            复制
          </button>
          <p className="text-xs text-text-muted">新 Job 放在 jobs/{key || "<英文名>"}/，都是手动运行，不会自己花搜索额度。</p>
        </>
      ) : (
        <p className="text-xs text-text-muted">这个矩阵里还没有能换关键词复制的 Job。</p>
      )}
      {stuck.length ? (
        <p className="text-xs text-text-muted">
          暂不能复制（提示词里写死了「{matrix.domain || matrix.name}」）：{stuck.map((m) => m.name).join("、")}
        </p>
      ) : null}
    </section>
  );
}

export function MatrixDetailPage() {
  const { key = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const canEdit = useCanEdit();
  const isAdmin = useIsAdmin();
  const [params, setParams] = useSearchParams();
  const tab = ["runs", "week", "settings"].includes(params.get("tab") ?? "") ? params.get("tab")! : "jobs";
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [words, setWords] = useState("");
  const [domain, setDomain] = useState("");
  const [adding, setAdding] = useState("");
  const [copying, setCopying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [configs, setConfigs] = useState<ProductionConfig[] | null>(null);
  const [configError, setConfigError] = useState("");
  const [configRetry, setConfigRetry] = useState(0);
  const [ungrouped, setUngrouped] = useState<{ job: string; name: string }[]>([]);
  const [memberError, setMemberError] = useState("");
  const [memberRetry, setMemberRetry] = useState(0);
  const flight = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    if (flight.current) return;
    const controller = new AbortController(); flight.current = controller; setLoading(true);
    try {
      const next = await getMatrix(key, controller.signal);
      if (next?.key !== key || !Array.isArray(next.members)) throw new Error("矩阵数据格式异常，请重试");
      if (!controller.signal.aborted) { setMatrix(next); setError(""); }
    } catch (e) { if (!controller.signal.aborted) setError(errorMessage(e)); }
    finally { if (flight.current === controller) { flight.current = null; setLoading(false); } }
  }, [key]);
  useEffect(() => {
    setMatrix(null); setConfigs(null); setError(""); setConfigError(""); setMemberError(""); setEditing(false); setCopying(false); setAdding(""); setUngrouped([]);
    void load();
    return () => { flight.current?.abort(); flight.current = null; };
  }, [load]);
  useEffect(() => {
    if (tab !== "jobs") return;
    const controller = new AbortController();
    listProduction(undefined, controller.signal).then((rows) => { if (!controller.signal.aborted) { if (!Array.isArray(rows)) throw new Error("视频配置数据异常"); setConfigs(rows); setConfigError(""); } })
      .catch((e) => { if (!controller.signal.aborted) setConfigError(errorMessage(e)); });
    return () => { controller.abort(); };
  }, [key, tab, configRetry]);
  useEffect(() => {
    if (tab !== "settings" || !canEdit) return;
    const controller = new AbortController();
    listMatrices(controller.signal).then((body) => { if (!controller.signal.aborted) { if (!Array.isArray(body?.ungrouped)) throw new Error("未分组任务数据异常，请重试"); setUngrouped(body.ungrouped); setMemberError(""); } })
      .catch((e) => { if (!controller.signal.aborted) setMemberError(errorMessage(e)); });
    return () => { controller.abort(); };
  }, [key, tab, canEdit, memberRetry]);
  const anyRunning = !!matrix?.members.some((m) => m.is_running);
  useEffect(() => {
    const refresh = () => { if (!document.hidden) void load(); };
    const timer = window.setInterval(refresh, anyRunning ? 5000 : 30000);
    document.addEventListener("visibilitychange", refresh);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [anyRunning, load]);
  if (!matrix || matrix.key !== key) return <div className="space-y-4"><Link className="text-xs text-accent hover:underline" to="/matrices">← 全部矩阵</Link>{error ? <ErrorState error={error} onRetry={load} /> : <div className="card p-8 text-sm text-text-muted" role="status">正在读取矩阵…</div>}</div>;
  const stats = summary(matrix);
  const jobs = matrix.members.map((m) => m.job);
  const save = async (fields: { domain?: string; keywords?: string[]; jobs?: string[] }) => {
    if (busy) return false;
    setBusy(true);
    try {
      setMatrix(await updateMatrix(matrix.key, { name: matrix.name, domain: fields.domain ?? matrix.domain, keywords: fields.keywords ?? matrix.keywords, jobs: fields.jobs }));
      setMemberRetry((n) => n + 1);
      return true;
    } catch (e) { toast.error(errorMessage(e)); return false; }
    finally { setBusy(false); }
  };
  const pause = async () => {
    if (busy || !window.confirm("暂停后这个矩阵所有 Job 的定时都不跑，各自的周期保留，手动运行不受影响。确定？")) return;
    setBusy(true);
    try { setMatrix(await pauseMatrix(matrix.key)); toast.success("已暂停整个矩阵"); }
    catch (e) { toast.error(errorMessage(e)); }
    finally { setBusy(false); }
  };
  const archive = async () => {
    if (busy) return;
    const putAway = !matrix.archived;
    if (putAway && !window.confirm("归档后矩阵列表里不再显示，里面的 Job 和已有的文稿、成片都不动。确定？")) return;
    setBusy(true);
    try { setMatrix(await archiveMatrix(matrix.key, putAway)); toast.success(putAway ? "已归档" : "已取消归档"); }
    catch (e) { toast.error(errorMessage(e)); }
    finally { setBusy(false); }
  };
  const resume = async () => {
    if (busy) return;
    setBusy(true);
    try {
      let next: Matrix;
      try { next = await resumeMatrix(matrix.key, false); }
      catch (e) {
        if (!(e instanceof ApiError) || e.status !== 409 || e.code !== "confirm_needed") throw e;
        if (!window.confirm(`${errorMessage(e)}。确定恢复？`)) return;
        next = await resumeMatrix(matrix.key, true);
      }
      setMatrix(next); toast.success("已恢复定时");
    } catch (e) { toast.error(errorMessage(e)); }
    finally { setBusy(false); }
  };
  const selectTab = (value: string) => setParams((old) => { const next = new URLSearchParams(old); next.delete("job"); if (value === "jobs") next.delete("tab"); else next.set("tab", value); return next; }, { replace: true });
  return <div className="space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-2"><Link className="text-xs text-text-muted hover:text-accent" to="/matrices">← 全部矩阵</Link><h1 className="text-xl font-semibold tracking-tight">{matrix.name}</h1><div className="flex flex-wrap gap-3 items-center"><p className="text-xs text-text-muted">{matrix.domain || "待设置研究领域"}</p><MatrixHealth matrix={matrix} /></div></div>
      <div className="flex flex-wrap gap-2"><button className="btn text-xs" aria-label="刷新矩阵详情" disabled={loading} onClick={load}><RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />刷新</button>{!matrix.archived ? <Link className="btn btn-primary text-xs" to={`/?matrix=${encodeURIComponent(matrix.key)}&view=all`}>查看视频<ArrowUpRight className="h-3.5 w-3.5" /></Link> : null}</div>
    </header>
    {error ? <div><ErrorState error={error} onRetry={load} compact /><p className="text-xs text-warning mt-2">暂时显示上次读取的数据。</p></div> : null}
    {matrix.archived ? <p className="card p-3 text-xs text-text-muted">这个矩阵已归档，研究任务和历史产出仍保留。可在矩阵设置中取消归档。</p> : null}
    {matrix.paused?.length ? <div role="status" className="card border-warning/50 p-3 text-xs text-warning flex flex-wrap items-center gap-2"><span className="flex-1">矩阵已暂停：{matrix.paused.length} 个 Job 的定时没在跑，各自的周期还留着；「运行一次」仍可手动点。暂停期间错过的不补跑。</span>{isAdmin ? <button className="btn text-xs" disabled={busy} onClick={resume}>恢复定时</button> : null}</div> : null}
    {matrix.errors.length ? <div className="card p-3 text-xs text-warning space-y-1" role="status"><p className="font-medium">矩阵配置需要检查</p>{matrix.errors.map((e) => <p key={e}>{e}</p>)}</div> : null}
    {matrix.monthly_searches ? <MonthlyLimit matrix={matrix} isAdmin={false} onSaved={setMatrix} /> : null}
    <section className="grid grid-cols-3 gap-3" aria-label="当前矩阵概况">{[{ label: "研究任务", value: matrix.members.length }, { label: "需要关注", value: stats.issues }, { label: "已启用定时", value: stats.scheduled }].map((s) => <div key={s.label} className="card p-3"><p className="text-xs text-text-muted">{s.label}</p><p className="text-xl font-semibold tabular-nums mt-1">{s.value}</p></div>)}</section>
    <div role="tablist" aria-label="矩阵工作区" className="flex flex-wrap gap-2 border-b border-border text-sm">
      {([["jobs", "研究任务"], ["runs", "运行记录"], ["week", "周期"], ["settings", "矩阵设置"]] as const).map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={cn("px-3 py-2 -mb-px border-b-2", tab === id ? "border-accent text-text font-medium" : "border-transparent text-text-muted hover:text-text")} onClick={() => selectTab(id)}>{label}</button>)}
    </div>
    {tab === "runs" ? <MatrixRuns key={matrix.key} matrix={matrix} initialJob={params.get("job") ?? ""} /> : null}
    {tab === "week" ? <MatrixWeek matrix={matrix} onChanged={setMatrix} /> : null}
    {tab === "jobs" ? <section aria-label="矩阵里的 Job" className="space-y-3">
      <p className="text-xs text-text-muted">先运行研究任务，再从报告进入视频制作；任务提示词可在任务详情中编辑。</p>
      {configError ? <div className="card p-3 text-xs text-warning flex flex-wrap gap-2"><span>视频配置数量暂未同步：{configError}</span><button className="text-accent hover:underline" onClick={() => setConfigRetry((n) => n + 1)}>重试视频配置</button></div> : null}
      {matrix.members.map((m) => <MemberCard key={m.job} member={m} matrixKey={matrix.key} canEdit={canEdit} isAdmin={isAdmin} configs={configs ? { count: configs.filter((c) => c.job === m.job).length, auto: configs.filter((c) => c.job === m.job && c.auto > 0).length } : null} onChanged={(next) => { if (next) setMatrix(next); else void load(); }} />)}
      {!matrix.members.length ? <div className="card p-8 text-center space-y-3"><p className="text-sm">还没有研究任务</p><p className="text-xs text-text-muted">把已有任务加入这个矩阵，即可跟进报告和视频。</p>{canEdit ? <button className="btn text-xs" onClick={() => selectTab("settings")}>添加研究任务</button> : null}</div> : null}
    </section> : null}
    {tab === "settings" ? <div className="space-y-4">
      <section className="card p-4 space-y-3 text-sm" aria-label="领域和关键词"><h2 className="font-medium">研究范围</h2>{editing ? <>
        <label className="block space-y-1 text-xs">领域<input className="input" aria-label="领域" value={domain} onChange={(e) => setDomain(e.target.value)} /></label><label className="block space-y-1 text-xs">共用关键词<input className="input" aria-label="关键词" value={words} onChange={(e) => setWords(e.target.value)} /></label><p className="text-xs text-text-muted">各任务运行时会先使用这里的关键词，再追加自己的关键词。保存后，下次运行生效。</p>
        <div className="flex gap-2"><button className="btn btn-primary text-xs" disabled={busy} onClick={async () => { if (await save({ domain, keywords: splitKeywords(words) })) setEditing(false); }}>保存</button><button className="btn text-xs" disabled={busy} onClick={() => setEditing(false)}>取消</button></div>
      </> : <><div><span className="text-xs text-text-muted">领域：</span>{matrix.domain || "未设置"}</div><div className="text-xs"><span className="text-text-muted">共用关键词：</span><Keywords words={matrix.keywords} empty="使用各研究任务自己的关键词" /></div>{canEdit ? <button className="btn text-xs" onClick={() => { setDomain(matrix.domain); setWords(matrix.keywords.join("，")); setEditing(true); }}>改领域和关键词</button> : null}</>}<div className="border-t border-border pt-3"><MonthlyLimit matrix={matrix} isAdmin={isAdmin} onSaved={setMatrix} /></div></section>
      <MatrixVideoDefaults key={matrix.key} matrix={matrix} canEdit={canEdit} onSaved={setMatrix} />
      <section className="card p-4 space-y-3" aria-label="管理研究任务"><h2 className="font-medium text-sm">任务归属</h2><p className="text-xs text-text-muted">将已有研究任务加入或移出这个矩阵。</p><ul className="divide-y divide-border">{matrix.members.map((m) => <li key={m.job} className="flex items-center justify-between gap-3 py-3 text-xs"><div className="min-w-0"><p>{m.name}</p><p className="text-text-muted font-mono break-all mt-1">{m.job}</p></div>{canEdit ? <button className="btn text-xs shrink-0" disabled={busy} aria-label={`把 ${m.name} 移出矩阵`} onClick={() => save({ jobs: jobs.filter((j) => j !== m.job) })}>移出</button> : null}</li>)}</ul>
        {memberError ? <ErrorState error={memberError} onRetry={() => setMemberRetry((n) => n + 1)} compact /> : null}
        {canEdit && ungrouped.length ? <div className="flex flex-wrap items-center gap-2 text-xs"><select aria-label="放进来的 Job" className="input w-auto max-w-full" value={adding} onChange={(e) => setAdding(e.target.value)}><option value="">选择未分组的研究任务…</option>{ungrouped.map((j) => <option key={j.job} value={j.job}>{j.name}（{j.job}）</option>)}</select><button className="btn text-xs" disabled={busy || !adding} onClick={async () => { if (await save({ jobs: [...jobs, adding] })) setAdding(""); }}>放进来</button></div> : null}
      </section>
      {canEdit ? <section className="card p-4 space-y-3"><h2 className="font-medium text-sm">矩阵管理</h2><div className="flex flex-wrap gap-2">{isAdmin && matrix.members.some((m) => m.schedule?.enabled) ? <button className="btn text-xs" disabled={busy} onClick={pause}>整个暂停</button> : null}<button className="btn text-xs" disabled={busy} onClick={archive}>{matrix.archived ? "取消归档" : "归档"}</button><button className="btn text-xs" onClick={() => setCopying(!copying)}>换关键词复制</button></div>{copying ? <CopyForm matrix={matrix} onDone={(next) => { setCopying(false); navigate(`/matrices/${next}`); }} /> : null}
        <details className="border-t border-border pt-3 text-xs"><summary className="cursor-pointer text-text-muted">删除分组</summary><div className="space-y-2 mt-3"><p className="text-text-muted">删除后，研究任务仍保留，可重新建立分组。</p><button className="btn text-xs text-danger" disabled={busy} onClick={async () => { if (busy || !window.confirm(`删掉「${matrix.name}」这个分组？里面的 Job 都还在，只是不再归在一起。`)) return; setBusy(true); try { await deleteMatrix(matrix.key); navigate("/matrices"); } catch (e) { toast.error(errorMessage(e)); } finally { setBusy(false); } }}>删除这个矩阵</button></div></details>
      </section> : null}
    </div> : null}
  </div>;
}


/** 一个 Job 的视频配置：它的报告写成哪几种视频，给谁看、多长、要不要自动写都在这里设。 */
export function MatrixJobVideoPage() {
  const { key = "", job = "" } = useParams();
  const canEdit = useCanEdit();
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getMatrix(key, controller.signal).then((next) => {
      if (next?.key !== key || !Array.isArray(next.members)) throw new Error("矩阵数据格式异常，请重试");
      if (!controller.signal.aborted) { setMatrix(next); setError(""); }
    }).catch((e) => { if (!controller.signal.aborted) setError(errorMessage(e)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); };
  }, [key, retry]);
  if (!matrix || matrix.key !== key) return <div className="space-y-4"><Link className="text-xs text-accent" to={`/matrices/${encodeURIComponent(key)}`}>← 返回矩阵</Link>{error ? <ErrorState error={error} onRetry={() => setRetry((n) => n + 1)} /> : <p className="text-sm text-text-muted" role="status">正在读取视频配置…</p>}</div>;
  const member = matrix.members.find((m) => m.job === job);
  return <div className="space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-3"><div className="space-y-2"><div className="flex gap-2 text-xs text-text-muted"><Link className="hover:text-accent" to="/matrices">矩阵</Link><span>/</span><Link className="hover:text-accent" to={`/matrices/${encodeURIComponent(matrix.key)}`}>{matrix.name}</Link></div><h1 className="text-xl font-semibold">{member?.name ?? job} · 视频配置</h1><p className="text-xs text-text-muted">设置这项研究的报告要写成哪些视频。</p></div><button className="btn text-xs" disabled={loading} onClick={() => setRetry((n) => n + 1)}><RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />刷新</button></header>
    {error ? <ErrorState error={error} onRetry={() => setRetry((n) => n + 1)} compact /> : null}
    {member ? <><div className="flex flex-wrap items-center gap-3 text-xs"><Link className="text-accent hover:underline" to={`/jobs/${encodeURIComponent(member.job)}`}>研究任务详情<ArrowUpRight className="inline h-3.5 w-3.5 ml-1" /></Link><Link className="text-text-muted hover:text-accent" to={`/?matrix=${encodeURIComponent(matrix.key)}&view=all`}>本矩阵的视频<ArrowUpRight className="inline h-3.5 w-3.5 ml-1" /></Link></div><ProductionConfigs key={member.job} members={[member]} canEdit={canEdit} /></>
      : <section className="card p-8 text-center space-y-2"><p className="text-sm">这个研究任务已不在当前矩阵中</p><p className="font-mono text-xs text-text-muted break-all">{job}</p><Link className="btn text-xs" to={`/matrices/${encodeURIComponent(matrix.key)}`}>返回矩阵</Link></section>}
  </div>;
}
