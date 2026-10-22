import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { listOutputs, type OutputScript, type Outputs } from "@/api/outputs";
import { errorMessage } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { STAGE_CLASS, STAGE_LABEL, stageOf } from "./VideoScripts";

const RANGES = [["", "全部时间"], ["7", "近 7 天"], ["30", "近 30 天"], ["90", "近 90 天"]];
const RENDERS = [["", "全部成片状态"], ["none", "还没出片"], ["working", "出片中"], ["done", "成片完成"], ["failed", "出片失败"], ["published", "已发布"]];
const WORKING = ["SUBMITTING", "QUEUED", "RUNNING", "DOWNLOADING"];
// 后端的时间是 2026-10-20T10:18:00+0800，有的浏览器不认没有冒号的时区
const timeOf = (iso: string) => new Date(iso.replace(/([+-]\d{2})(\d{2})$/, "$1:$2")).getTime();

function renderState(s: OutputScript): string {
  if (s.published) return "published";
  if (!s.render) return "none";
  if (s.render.status === "COMPLETED") return "done";
  return WORKING.includes(s.render.status) ? "working" : "failed";
}

function ScriptLine({ s, showOrigin }: { s: OutputScript; showOrigin: boolean }) {
  const stage = stageOf(s);
  return (
    <li className="flex flex-wrap items-baseline gap-2 text-sm py-1" aria-label={`文稿 ${s.title}`}>
      <span className={cn("text-xs px-1.5 py-0.5 rounded border whitespace-nowrap", STAGE_CLASS[stage])}>{STAGE_LABEL[stage]}</span>
      <Link className="hover:underline hover:text-accent min-w-0 flex-1" to={`/video/${s.id}`}>{s.title || "（没有标题）"}</Link>
      <span className="text-xs text-text-muted whitespace-nowrap" title={s.production_guessed ? "配置出现之前写的，按配方和 Job 认出是这套" : undefined}>
        {s.production_name || s.column_key}
        {s.auto ? " · 自动" : ""}
      </span>
      {showOrigin ? (
        <span className="text-xs text-text-muted whitespace-nowrap">{s.manual ? "手填选题" : s.report_date ? `${s.report_date} 的报告` : ""}</span>
      ) : null}
      <span className="text-xs text-text-muted whitespace-nowrap">{s.created_at.slice(0, 16).replace("T", " ")}</span>
    </li>
  );
}

/** 按归属：矩阵 › Job › 报告 › 文稿。只列做过视频的报告。 */
function ByOwner({ data, rows, newest }: { data: Outputs; rows: OutputScript[]; newest: boolean }) {
  const jobName = (job: string) => data.jobs.find((j) => j.job === job)?.name || job;
  const matrixName = (key: string) => data.matrices.find((m) => m.key === key)?.name || key;
  const order = (a: string, b: string) => (newest ? b.localeCompare(a) : a.localeCompare(b));
  const groups = new Map<string, Map<string, Map<string, OutputScript[]>>>();
  for (const s of rows) {
    const m = s.matrix || (s.job ? "_none" : "_manual");
    const byJob = groups.get(m) ?? new Map();
    const byReport = byJob.get(s.job) ?? new Map();
    const key = s.report_path || "";
    byReport.set(key, [...(byReport.get(key) ?? []), s]);
    byJob.set(s.job, byReport);
    groups.set(m, byJob);
  }
  // the matrix with the newest (or oldest) script first; the ones with none of their own come last
  const edge = (k: string) => {
    const times = [...groups.get(k)!.values()].flatMap((byReport) => [...byReport.values()].flat().map((s) => s.created_at));
    return times.length ? times.reduce((x, y) => (order(x, y) <= 0 ? x : y)) : "";
  };
  const matrixOrder = [...data.matrices.map((m) => m.key), "_none", "_manual"].filter((k) => groups.has(k))
    .sort((a, b) => order(edge(a), edge(b)));
  if (!matrixOrder.length) return <p className="text-sm text-text-muted">没有符合条件的文稿。</p>;
  return (
    <div className="space-y-3">
      {matrixOrder.map((m) => (
        <section key={m} className="card p-3 space-y-3" aria-label={`矩阵 ${m}`}>
          <h2 className="font-medium">
            {m === "_none" ? "不在矩阵里的 Job" : m === "_manual" ? "手填选题写的" : (
              <>
                <Link className="hover:underline" to={`/matrices/${m}`}>{matrixName(m)}</Link>
                {data.matrices.find((x) => x.key === m)?.archived ? <span className="text-xs text-text-muted ml-2">已归档</span> : null}
              </>
            )}
          </h2>
          {[...groups.get(m)!.entries()].map(([job, byReport]) => {
            const summary = data.jobs.find((j) => j.job === job);
            const reports = [...byReport.entries()].sort(([a, x], [b, y]) => order(x[0].report_date || a, y[0].report_date || b));
            return (
              <div key={job || "manual"} className="space-y-2" aria-label={`Job ${jobName(job) || "手填"}`}>
                {job ? (
                  <h3 className="text-sm">
                    <Link className="hover:underline" to={`/jobs/${encodeURIComponent(job)}`}>{jobName(job)}</Link>
                  </h3>
                ) : null}
                {reports.map(([path, scripts]) => (
                  <div key={path || "none"} className="border-l-2 border-border pl-3">
                    <div className="text-xs text-text-muted">
                      {path ? (
                        <Link className="hover:underline" to={`/reports/view?path=${encodeURIComponent(path.replace(/^output\//, ""))}`}>
                          {scripts[0].report_date} 那次运行的报告
                        </Link>
                      ) : "不是从报告写的"}
                      {" · "}{scripts.length} 篇
                    </div>
                    <ul>
                      {[...scripts].sort((a, b) => order(a.created_at, b.created_at)).map((s) => (
                        <ScriptLine key={s.id} s={s} showOrigin={false} />
                      ))}
                    </ul>
                  </div>
                ))}
                {summary?.unwritten ? (
                  <p className="text-xs text-text-muted pl-3">
                    另外 {summary.unwritten} 次运行的报告还没做成视频
                    {" · "}
                    <Link className="text-accent hover:underline" to={m.startsWith("_") ? `/jobs/${encodeURIComponent(job)}` : `/matrices/${m}?tab=runs&job=${encodeURIComponent(job)}`}>
                      看这个 Job 的运行
                    </Link>
                  </p>
                ) : null}
              </div>
            );
          })}
        </section>
      ))}
    </div>
  );
}

export function OutputsPage() {
  const [data, setData] = useState<Outputs | null>(null);
  const [error, setError] = useState("");
  const [view, setView] = useState<"owner" | "time">("owner");
  const [newest, setNewest] = useState(true);
  const [range, setRange] = useState("");
  const [render, setRender] = useState("");
  const [matrix, setMatrix] = useState("");
  const [archived, setArchived] = useState(false);

  useEffect(() => {
    setData(null);
    listOutputs(archived).then(setData).catch((e) => setError(errorMessage(e)));
  }, [archived]);

  const rows = useMemo(() => {
    if (!data) return [];
    const since = range ? Date.now() - Number(range) * 86400000 : 0;
    return data.scripts
      .filter((s) => !since || timeOf(s.created_at) >= since)
      .filter((s) => !render || renderState(s) === render)
      .filter((s) => !matrix || s.matrix === matrix)
      .sort((a, b) => (newest ? b.created_at.localeCompare(a.created_at) : a.created_at.localeCompare(b.created_at)));
  }, [data, range, render, matrix, newest]);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">产出</h1>
      <p className="text-xs text-text-muted">每篇文稿从哪来：哪个矩阵、哪个 Job、哪次运行的报告，用哪套视频配置写的。</p>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <div className="flex rounded-md border border-border overflow-hidden" role="group" aria-label="看法">
          {(["owner", "time"] as const).map((v) => (
            <button key={v} className={cn("px-2 py-1", view === v ? "bg-accent-solid text-white" : "hover:bg-bg-hover")} aria-pressed={view === v} onClick={() => setView(v)}>
              {v === "owner" ? "按归属" : "按时间"}
            </button>
          ))}
        </div>
        <button className="btn text-xs" onClick={() => setNewest(!newest)}>{newest ? "最新在前" : "最早在前"}</button>
        <select aria-label="文稿时间" className="input w-auto" value={range} onChange={(e) => setRange(e.target.value)}>
          {RANGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select aria-label="成片状态" className="input w-auto" value={render} onChange={(e) => setRender(e.target.value)}>
          {RENDERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {data && data.matrices.length > 1 ? (
          <select aria-label="矩阵" className="input w-auto" value={matrix} onChange={(e) => setMatrix(e.target.value)}>
            <option value="">全部矩阵</option>
            {data.matrices.filter((m) => archived || !m.archived).map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
          </select>
        ) : null}
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
          看已归档的（含已归档矩阵）
        </label>
      </div>
      {error ? <p className="text-sm text-danger">{error}</p> : null}
      {!data && !error ? <p className="text-sm text-text-muted">读取中…</p> : null}
      {data ? (
        view === "owner" ? (
          <ByOwner data={data} rows={rows} newest={newest} />
        ) : rows.length ? (
          <ul className="card p-3 divide-y divide-border">
            {rows.map((s) => <ScriptLine key={s.id} s={s} showOrigin />)}
          </ul>
        ) : (
          <p className="text-sm text-text-muted">没有符合条件的文稿。</p>
        )
      ) : null}
    </div>
  );
}
