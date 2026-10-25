import { useEffect, useState } from "react";

import { getMatrixCalendar, type Matrix, type MatrixCalendar } from "@/api/matrices";
import { PeriodEditor, periodWords, WEEKDAYS } from "@/components/PeriodEditor";
import { ErrorState } from "@/components/ErrorState";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage } from "@/lib/toast";

const dayKey = (ts: string) => ts.slice(0, 10);
const clock = (ts: string) => ts.slice(11, 16);
const dayLabel = (day: string) => `周${WEEKDAYS[new Date(`${day}T00:00:00`).getDay()]} ${day.slice(5)}`;

function nextSevenDays(now: string) {
  const start = new Date(`${dayKey(now)}T00:00:00`);
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(start.getTime() + i * 86400000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  });
}

/** 矩阵详情的「周期」：每个 Job 一行、每天一格的一周，接下来 7 天的所有定时运行，撞车的提醒 */
export function MatrixWeek({ matrix, onChanged }: { matrix: Matrix; onChanged: (m: Matrix) => void }) {
  const isAdmin = useAuthStore((s) => s.user?.role) === "admin";
  const [cal, setCal] = useState<MatrixCalendar | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getMatrixCalendar(matrix.key, controller.signal).then((c) => { if (!controller.signal.aborted) { setCal(c); setError(""); } }).catch((e) => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => { controller.abort(); };
  }, [matrix, retry]);

  if (!cal) return error ? <ErrorState error={error} onRetry={() => setRetry((n) => n + 1)} /> : <p role="status" className="text-sm text-text-muted">正在读取周期安排…</p>;
  const days = nextSevenDays(cal.now);
  const runs = cal.runs.filter((r) => days.includes(dayKey(r.ts)));
  const timed = matrix.members.filter((m) => cal.jobs[m.job]);
  return (
    <section className="space-y-3" aria-label="周期">
      {error ? <ErrorState error={error} onRetry={() => setRetry((n) => n + 1)} compact /> : null}
      {cal.clashes.filter((c) => days.includes(dayKey(c.ts))).map((c) => (
        <p key={c.ts} role="alert" className="card border-warning/50 p-2 text-xs text-warning">
          {dayKey(c.ts)} {clock(c.ts)} 同时到点 {c.jobs.length} 个：{c.jobs.map((j) => cal.jobs[j]?.name || j).join("、")}。一次最多跑 {cal.concurrent_runs} 个，多的要排队，可以错开几分钟。
        </p>
      ))}
      <div className="card p-4 space-y-3 overflow-x-auto">
        <h2 className="font-medium text-sm">接下来 7 天</h2>
        {timed.length ? (
          <table className="text-xs border-separate border-spacing-1 w-full min-w-[40rem]">
            <thead>
              <tr className="text-left text-text-muted">
                <th className="font-normal">Job</th>
                {days.map((d) => <th key={d} className="font-normal px-1">{dayLabel(d)}</th>)}
                <th />
              </tr>
            </thead>
            <tbody>
              {timed.map((m) => {
                const j = cal.jobs[m.job];
                return (
                  <tr key={m.job} className="align-top">
                    <td className="pr-2 whitespace-nowrap">
                      {m.name}
                      <div className="text-text-muted">{periodWords(m)}</div>
                    </td>
                    {days.map((d) => {
                      const times = j.runs.filter((t) => dayKey(t) === d).map(clock);
                      return (
                        <td key={d} className={`px-1 text-center rounded ${j.enabled ? (times.length ? "bg-accent/15" : "") : "border border-dashed border-border text-text-muted"}`}>
                          {times.join(" ") || (j.enabled ? "" : "暂停")}
                        </td>
                      );
                    })}
                    <td>
                      {isAdmin ? <button className="text-accent hover:underline" aria-label={`改 ${m.name} 的周期`} onClick={() => setEditing(editing === m.job ? "" : m.job)}>改周期</button> : null}
                    </td>
                  </tr>
                );
              })}
              <tr className="text-text-muted">
                <td>预计搜索</td>
                {days.map((d) => <td key={d} className="text-center">{cal.searches_by_day[d] ? `${cal.searches_by_day[d]} 次` : ""}</td>)}
                <td />
              </tr>
            </tbody>
          </table>
        ) : (
          <p className="text-xs text-text-muted">这个矩阵里没有定时的 Job，都是手动运行。</p>
        )}
        {timed.filter((m) => m.job === editing).map((m) => (
          <PeriodEditor key={m.job} matrixKey={matrix.key} member={m} onCancel={() => setEditing("")} onDone={(next) => { setEditing(""); onChanged(next); }} />
        ))}
      </div>
      <div className="card p-4 space-y-2">
        <h2 className="font-medium text-sm">按时间排</h2>
        {runs.length ? (
          <ul className="text-xs space-y-0.5">
            {runs.map((r) => (
              <li key={`${r.job}-${r.ts}`} className="flex gap-3">
                <span className="font-mono text-text-muted">{dayLabel(dayKey(r.ts))} {clock(r.ts)}</span>
                <span>{r.name}</span>
                {r.searches ? <span className="text-text-muted">约 {r.searches} 次搜索</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-text-muted">接下来 7 天没有定时运行。</p>
        )}
      </div>
    </section>
  );
}
