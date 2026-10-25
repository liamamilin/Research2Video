import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { runJob } from "@/api";
import { listMatrixRuns, type Matrix, type MatrixRun } from "@/api/matrices";
import { TimeStamp } from "@/components/TimeStamp";
import { ErrorState } from "@/components/ErrorState";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage, useToast } from "@/lib/toast";

const STATUS: Record<string, { label: string; cls: string }> = {
  success: { label: "完成", cls: "text-success" },
  failed: { label: "没跑完", cls: "text-danger" },
  cancelled: { label: "已取消", cls: "text-warning" },
  skipped: { label: "跳过", cls: "text-text-muted" },
};
const TRIGGER: Record<string, string> = { scheduled: "定时", manual: "手动", "": "" };
const RANGES: [string, number][] = [["全部时间", 0], ["近 7 天", 7], ["近 30 天", 30], ["近 90 天", 90]];
const PAGE = 20;

const monthOf = (ts: string) => ts.slice(0, 7);

/** 矩阵详情的「运行记录」：所有 Job 的每次运行，最新在前，可以改成最早在前，按 Job、状态、时间筛选，按月分组 */
export function MatrixRuns({ matrix, initialJob = "" }: { matrix: Matrix; initialJob?: string }) {
  const [job, setJob] = useState(matrix.members.some((m) => m.job === initialJob) ? initialJob : "");
  const [status, setStatus] = useState("");
  const [days, setDays] = useState(0);
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [rows, setRows] = useState<MatrixRun[] | null>(null);
  const [total, setTotal] = useState(0);
  const [more, setMore] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const generation = useRef(0);
  const appendFlight = useRef<AbortController | null>(null);
  const offset = useRef(0);
  const failedMore = useRef(false);
  const toast = useToast();
  const role = useAuthStore((s) => s.user?.role);
  const canEdit = role === "editor" || role === "admin";
  const member = (j: string) => matrix.members.find((m) => m.job === j);

  // the engine has no resume point: a run that did not finish is started again from the beginning
  const again = async (r: MatrixRun) => {
    const spend = member(r.job)?.searches_per_run ? `上次完整跑一次用了 ${member(r.job)!.searches_per_run} 次搜索。` : "";
    if (!window.confirm(`重新运行「${r.name}」？会从头再跑一遍，花搜索额度，${spend}`)) return;
    try {
      await runJob(r.job);
      toast.success("开始运行了，刷新后能在最上面看到");
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    generation.current += 1;
    appendFlight.current?.abort(); appendFlight.current = null;
    setRows(null); setMore(false); setLoadingMore(false); setError(""); failedMore.current = false; offset.current = 0;
    listMatrixRuns(matrix.key, { job: job || undefined, status: status || undefined, days: days || undefined, order, limit: PAGE }, controller.signal)
      .then((b) => { if (!controller.signal.aborted) { setRows(b.runs); setTotal(b.total); setMore(b.more); offset.current = b.runs.length; } })
      .catch((e) => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => { controller.abort(); appendFlight.current?.abort(); generation.current += 1; };
  }, [matrix.key, job, status, days, order, retry]);

  const loadMore = async () => {
    if (appendFlight.current || rows === null) return;
    const controller = new AbortController(); appendFlight.current = controller;
    const current = generation.current;
    setLoadingMore(true);
    try {
      const b = await listMatrixRuns(matrix.key, { job: job || undefined, status: status || undefined, days: days || undefined, order, limit: PAGE, offset: offset.current }, controller.signal);
      if (controller.signal.aborted || current !== generation.current) return;
      const known = new Set(rows.map((r) => `${r.job}-${r.ts}`));
      const added = b.runs.filter((r) => !known.has(`${r.job}-${r.ts}`));
      setRows([...rows, ...added]); offset.current += b.runs.length;
      setMore(b.more && added.length > 0); setError("");
    } catch (e) {
      if (!controller.signal.aborted && current === generation.current) { setError(errorMessage(e)); failedMore.current = true; }
    } finally {
      if (appendFlight.current === controller) { appendFlight.current = null; setLoadingMore(false); }
    }
  };

  let month = "";
  return (
    <section className="card p-4 space-y-3" aria-label="运行记录">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <h2 className="font-medium text-sm flex-1">运行记录{rows ? `（${total}）` : ""}</h2>
        <select aria-label="按 Job 筛" className="input w-auto" value={job} onChange={(e) => setJob(e.target.value)}>
          <option value="">全部 Job</option>
          {matrix.members.map((m) => <option key={m.job} value={m.job}>{m.name}</option>)}
        </select>
        <select aria-label="按状态筛" className="input w-auto" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select aria-label="按时间筛" className="input w-auto" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {RANGES.map(([label, d]) => <option key={d} value={d}>{label}</option>)}
        </select>
        <button className="btn text-xs" onClick={() => setOrder(order === "desc" ? "asc" : "desc")}>
          {order === "desc" ? "最新在前" : "最早在前"}
        </button>
      </div>
      {error ? <ErrorState error={error} onRetry={() => failedMore.current ? loadMore() : setRetry((n) => n + 1)} compact /> : null}
      {rows === null && !error ? <p role="status" className="py-6 text-xs text-text-muted">正在读取运行记录…</p> : null}
      {rows && !rows.length ? <p className="text-xs text-text-muted">没有符合的运行。</p> : null}
      <ul className="space-y-1">
        {rows?.map((r) => {
          const head = monthOf(r.ts) !== month ? ((month = monthOf(r.ts)), month) : null;
          const st = STATUS[r.status] ?? { label: r.status, cls: "text-text-muted" };
          return (
            <li key={`${r.job}-${r.ts}`}>
              {head ? <div className="text-xs text-text-muted pt-2">{head.replace("-", " 年 ")} 月</div> : null}
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs border-t border-border py-1.5">
                <TimeStamp value={r.ts} />
                <Link className="text-sm text-accent hover:underline" to={`/jobs/${encodeURIComponent(r.job)}`}>{r.name}</Link>
                {r.trigger ? <span className="badge border border-border text-text-muted">{TRIGGER[r.trigger]}</span> : null}
                <span className={st.cls}>{st.label}</span>
                {r.searches ? <span className="text-text-muted">{r.searches} 次搜索</span> : null}
                {r.duration_seconds ? <span className="text-text-muted">{Math.max(1, Math.round(r.duration_seconds / 60))} 分钟</span> : null}
                {r.report_path && r.status === "success" ? (
                  <Link className="text-accent hover:underline" to={`/reports/view?path=${encodeURIComponent(r.report_path.replace(/^output\//, ""))}`}>看报告</Link>
                ) : null}
                {r.items ? <span className="text-text-muted">{r.items} 条</span> : null}
                {canEdit && (r.status === "failed" || r.status === "cancelled") && member(r.job)?.enabled !== false && !member(r.job)?.is_running ? (
                  <button className="text-accent hover:underline" onClick={() => again(r)}>重新运行</button>
                ) : null}
                {r.status === "success" && r.report_path ? (
                  r.scripts ? (
                    <span className="text-text-muted">写了 {r.scripts} 篇文稿</span>
                  ) : (
                    <Link className="text-success hover:underline" to={`/matrices/${encodeURIComponent(matrix.key)}/video/${encodeURIComponent(r.job)}`}>还没写视频 →</Link>
                  )
                ) : null}
                {r.error ? <details className="basis-full text-text-muted"><summary className="cursor-pointer text-warning">失败详情</summary><p className="mt-2 text-xs leading-5 break-words">{r.error}</p></details> : null}
              </div>
            </li>
          );
        })}
      </ul>
      {more ? <button className="btn text-xs" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "加载中…" : `再加载 ${PAGE} 条`}</button> : null}
    </section>
  );
}
