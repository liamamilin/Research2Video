import { useCallback, useState } from "react";
import { RefreshCw } from "lucide-react";
import { loadWeeklyReview, type ReviewGroup } from "@/api/telemetry";
import { usePageResource } from "@/lib/use-page-resource";
import { cn } from "@/lib/utils";
import { ErrorState } from "./ErrorState";

const th = "pb-2 font-normal text-right whitespace-nowrap";
const td = "py-2 text-right tabular-nums whitespace-nowrap";
const day = (seconds: number) => new Date(seconds * 1000).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
const when = (seconds: number) => new Date(seconds * 1000).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
const num = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("zh-CN");
const week = (start: number) => `${day(start)}–${day(start + 6 * 86400)}`;
const STATE = { pending: "未满 24 小时", gap: "采集中断" } as const;

function Groups({ label, groups, minN }: { label: string; groups: ReviewGroup[]; minN: number }) {
  return <section className="card p-4 space-y-3">
    <h2 className="text-sm font-medium">按{label}</h2>
    <div className="overflow-x-auto"><table className="w-full text-xs text-left">
      <thead className="text-text-muted"><tr><th className="pb-2 font-normal">{label}</th><th className={th}>视频</th><th className={th}>有 24h 数据</th><th className={th}>24h 中位数</th><th className={th}>上周中位数</th><th className={th}>最高</th><th className="pb-2 font-normal pl-4">最高的一条</th></tr></thead>
      <tbody>{groups.map(g => <tr key={g.value} className="border-t border-border">
        <td className="py-2">{g.value}{g.flag ? <span className={cn("ml-2 px-1.5 rounded text-[11px]", g.flag === "high" ? "bg-success/15 text-success" : "bg-danger/15 text-danger")}>{g.flag === "high" ? "明显偏高" : "明显偏低"}</span> : null}</td>
        <td className={td}>{g.videos}</td><td className={td} title={g.measured < minN ? `少于 ${minN} 条，不下结论` : undefined}>{g.measured}{g.measured && g.measured < minN ? <span className="text-text-muted">（少）</span> : null}</td>
        <td className={td}>{num(g.median)}</td><td className={td}>{num(g.previous_median)}</td><td className={td}>{num(g.max)}</td>
        <td className="py-2 pl-4 max-w-[22rem] truncate" title={g.best_title}>{g.best_title || "—"}</td></tr>)}</tbody></table></div>
  </section>;
}

export function WeeklyReview() {
  const [start, setStart] = useState<number | null>(null);
  const load = useCallback((signal: AbortSignal) => loadWeeklyReview(start, signal), [start]);
  const { data, error, loading, reload } = usePageResource(load);
  if (error) return <ErrorState error={error} onRetry={reload} compact />;
  if (!data) return <p className="text-sm text-text-muted">正在读取…</p>;
  if (!data.connected) return <ErrorState empty="先在平台管理里连接哔哩哔哩账号" compact />;
  const s = data.summary;
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-3">
      <select aria-label="复盘的周" className="input text-xs w-auto" value={data.start} onChange={e => setStart(Number(e.target.value))}>
        {data.weeks.map(w => <option key={w} value={w}>{week(w)}{w + 7 * 86400 > data.now ? "（本周，未完）" : ""}</option>)}</select>
      <p className="text-xs text-text-muted">按每条视频发布后 24 小时的播放比较，用中位数；一组少于 {data.min_n} 条只列出不下结论，中位数达到整体 {data.ratio} 倍或一半以下记为明显偏高或偏低。</p>
      <button className="btn text-xs ml-auto" disabled={loading} onClick={() => void reload()}><RefreshCw className="w-3.5 h-3.5" />刷新</button>
    </div>
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <Card label="本周发布" value={s.videos} note={`有 24h 数据 ${s.ok} 条${s.pending ? `，未满 24 小时 ${s.pending}` : ""}${s.gap ? `，采集中断 ${s.gap}` : ""}`} />
      <Card label="24h 播放中位数" value={num(s.median_24h)} note={`上周 ${num(s.previous_median_24h)}（${s.previous_videos} 条）`} />
      <Card label="目前总播放" value={num(s.views)} />
      <Card label="最高一条占总播放" value={s.top_share == null ? "—" : `${Math.round(s.top_share * 100)}%`} note="越高越说明靠单条爆款" />
    </div>
    <section className="card p-4 space-y-2">
      <h2 className="text-sm font-medium">本周结论</h2>
      {data.findings.length ? <ul className="text-sm list-disc pl-5 space-y-1">{data.findings.map(f => <li key={f}>{f}</li>)}</ul> : <p className="text-sm text-text-muted">没有哪一组明显偏离整体。</p>}
      <h3 className="text-sm font-medium pt-2">建议（需要你确认后再改配置）</h3>
      {data.suggestions.length ? <ul className="text-sm list-disc pl-5 space-y-1">{data.suggestions.map(f => <li key={f}>{f}</li>)}</ul> : <p className="text-sm text-text-muted">数据还不足以给出连续两周成立的建议。</p>}
    </section>
    {data.dimensions.map(d => <Groups key={d.key} label={d.label} groups={d.groups} minN={data.min_n} />)}
    <section className="card p-4 space-y-3">
      <h2 className="text-sm font-medium">本周每一条</h2>
      {data.videos.length ? <div className="overflow-x-auto"><table className="w-full text-xs text-left">
        <thead className="text-text-muted"><tr><th className="pb-2 font-normal">发布</th><th className="pb-2 font-normal">标题</th><th className="pb-2 font-normal">栏目</th><th className="pb-2 font-normal">形式</th><th className={th}>24h</th><th className={th}>72h</th><th className={th}>7 天</th><th className={th}>目前</th></tr></thead>
        <tbody>{data.videos.map(v => <tr key={v.bvid} className="border-t border-border">
          <td className="py-2 whitespace-nowrap">{when(v.pubdate)}</td>
          <td className="py-2 max-w-[22rem] truncate" title={v.title}><a className="hover:text-accent" href={`https://www.bilibili.com/video/${v.bvid}`} target="_blank" rel="noreferrer">{v.title}</a>{v.structure ? <span className="ml-2 text-text-muted">{v.structure}</span> : null}</td>
          <td className="py-2 whitespace-nowrap">{v.column}</td><td className="py-2 whitespace-nowrap">{v.form}</td>
          <td className={td}>{v.state === "ok" ? num(v.h24) : <span className="text-text-muted">{STATE[v.state]}</span>}</td>
          <td className={td}>{num(v.h72)}</td><td className={td}>{num(v.h168)}</td><td className={td}>{num(v.view)}</td></tr>)}</tbody></table></div>
        : <ErrorState empty="这一周没有发布工作流视频" compact />}
    </section>
  </div>;
}

function Card({ label, value, note }: { label: string; value: React.ReactNode; note?: string }) {
  return <div className="card p-3"><p className="text-xs text-text-muted">{label}</p><p className="text-lg font-semibold tabular-nums mt-1">{value}</p>{note ? <p className="text-xs text-text-muted mt-1">{note}</p> : null}</div>;
}
