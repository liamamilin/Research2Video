import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { runJob } from "@/api";
import { listMatrixRuns, type Matrix, type MatrixRun } from "@/api/matrices";
import { TimeStamp } from "@/components/TimeStamp";
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
export function MatrixRuns({ matrix }: { matrix: Matrix }) {
  const [job, setJob] = useState("");
  const [status, setStatus] = useState("");
  const [days, setDays] = useState(0);
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [rows, setRows] = useState<MatrixRun[] | null>(null);
  const [total, setTotal] = useState(0);
  const [more, setMore] = useState(false);
  const [error, setError] = useState("");
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
    let live = true;
    setRows(null);
    listMatrixRuns(matrix.key, { job: job || undefined, status: status || undefined, days: days || undefined, order, limit: PAGE })
      .then((b) => { if (live) { setRows(b.runs); setTotal(b.total); setMore(b.more); setError(""); } })
      .catch((e) => { if (live) setError(errorMessage(e)); });
    return () => { live = false; };
  }, [matrix.key, job, status, days, order]);

  const loadMore = async () => {
    try {
      const b = await listMatrixRuns(matrix.key, { job: job || undefined, status: status || undefined, days: days || undefined, order, limit: PAGE, offset: rows?.length ?? 0 });
      setRows([...(rows ?? []), ...b.runs]);
      setMore(b.more);
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  let month = "";
  return (
    <section className="card p-3 space-y-2" aria-label="运行记录">
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
      {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
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
                  <Link className="text-accent hover:underline" to={`/reports/view?path=${encodeURIComponent(r.report_path)}`}>看报告</Link>
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
                {r.error ? <span className="text-danger break-all">{r.error}</span> : null}
              </div>
            </li>
          );
        })}
      </ul>
      {more ? <button className="btn text-xs" onClick={loadMore}>再加载 {PAGE} 条</button> : null}
    </section>
  );
}
