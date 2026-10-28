import { useState } from "react";
import { getThemeUsage, type ThemeUsageStage } from "@/api/themes";
import { errorMessage } from "@/lib/toast";

export function ThemeUsage({ matrix, id }: { matrix: string; id: string }) {
  const [data, setData] = useState<{ stages: ThemeUsageStage[]; note: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const read = async () => {
    setBusy(true); setError("");
    try { setData(await getThemeUsage(matrix, id)); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  };
  const number = (n: number | null) => n == null ? "未知" : n.toLocaleString();
  return <div className="text-xs space-y-2">
    <button className="text-accent" disabled={busy} onClick={() => { setOpen(!open); if (!open) void read(); }}>{busy ? "读取用量…" : open ? "收起阶段用量" : "查看阶段用量"}</button>
    {open && <>
      {error && <p role="alert" className="text-warning">{error}<button className="ml-2 underline" onClick={read}>重试</button></p>}
      {data && <><div className="overflow-x-auto"><table className="w-full text-left whitespace-nowrap" aria-label="阶段实际用量">
        <thead className="text-text-muted"><tr>{["阶段", "模型请求", "Tokens", "搜索批次", "耗时", "费用估算"].map((title) => <th className="pr-3 py-2 font-normal" key={title}>{title}</th>)}</tr></thead>
        <tbody>{data.stages.map((s) => <tr key={s.key} className="border-t border-border">
          <td className="pr-3 py-2">{s.name}{s.shared && s.batch_topics != null ? ` · ${s.batch_topics}题共享` : ""}{s.partial && " · 部分记录"}</td>
          <td className="pr-3 tabular-nums">{number(s.requests)}</td><td className="pr-3 tabular-nums">{number(s.total_tokens)}</td><td className="pr-3">{number(s.searches)}</td>
          <td className="pr-3">{s.duration_seconds == null ? "未知" : s.duration_seconds < 60 ? `${s.duration_seconds.toFixed(1)}秒` : `${(s.duration_seconds / 60).toFixed(1)}分钟`}</td>
          <td>{s.cost_usd == null ? "未知" : `$${s.cost_usd.toFixed(4)}`}</td>
        </tr>)}</tbody></table></div><p className="text-text-muted leading-5">{data.note}</p>
        {data.stages.some((s) => s.attempts.length) && <details className="text-text-muted"><summary>执行记录</summary>{data.stages.map((s) => <p key={s.key} className="mt-1">{s.name}：{s.attempts.map((a) => a.status).join("、") || "暂无"}</p>)}</details>}
      </>}
    </>}
  </div>;
}
