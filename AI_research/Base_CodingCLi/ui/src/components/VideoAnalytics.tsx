import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLink, RefreshCw, X } from "lucide-react";
import { enableVideoData, loadAnalytics, loadVideoData, syncVideoData, type MatrixData, type Metric, type VideoData } from "@/api/analytics";
import { usePageResource } from "@/lib/use-page-resource";
import { errorMessage } from "@/lib/toast";
import { documentPublishLink } from "@/lib/platform-links";
import { ErrorState } from "./ErrorState";

const LABELS: Record<Metric, string> = { view: "播放", like: "点赞", coin: "投币", favorite: "收藏", reply: "评论", danmaku: "弹幕", share: "分享" };
const METRICS = Object.keys(LABELS) as Metric[];
const number = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("zh-CN");
const date = (value: number | null | undefined, full = true) => value ? new Date(value * 1000).toLocaleString("zh-CN", full ? { hour12: false } : { year: "numeric", month: "2-digit", day: "2-digit" }) : "尚未采集";
const signed = (value: number | null | undefined) => value == null ? "—" : `${value > 0 ? "+" : ""}${number(value)}`;
const matrixLabel = (publication: VideoData["publication"]) => !publication ? "未关联矩阵" : publication.matrix_unavailable ? "矩阵归属暂不可用" : publication.matrix_name || "独立视频";

function MatrixSummary({ matrices, selected, onSelect }: { matrices: MatrixData[]; selected: string; onSelect: (key: string) => void }) {
  return <details className="card p-4 space-y-3">
    <summary className="cursor-pointer text-sm font-medium">按矩阵看表现<span className="text-xs text-text-muted font-normal ml-3">{matrices.filter(m => m.key.startsWith("matrix:")).length}个矩阵 · 展开比较</span></summary>
    <p className="text-xs text-text-muted">全账号视频的累计汇总，不受列表筛选影响。点击矩阵可筛选下方视频；矩阵按当前文稿来源归属，站内历史视频需关联后才能归类。</p>
    <div className="overflow-x-auto"><table className="w-full text-xs text-left"><thead className="text-text-muted"><tr>{["矩阵", "视频数 / 已公开", "播放样本", "累计播放", "单条播放中位数", "收藏率"].map(label => <th key={label} className="py-2 px-2 font-normal whitespace-nowrap">{label}</th>)}</tr></thead><tbody>{matrices.map(m => <tr key={m.key} className="border-t border-border"><td className="py-3 px-2 min-w-[150px]"><button className="text-accent hover:underline text-left" aria-pressed={selected === m.key} onClick={() => onSelect(selected === m.key ? "" : m.key)}>{m.name}</button>{m.stale_samples ? <p className="text-warning mt-1">{m.stale_samples}条保留上次成功数据</p> : null}</td><td className="py-3 px-2 tabular-nums">{m.videos} / {m.public_videos}</td><td className="py-3 px-2 tabular-nums">{m.view_samples}条</td><td className="py-3 px-2 tabular-nums">{number(m.view)}</td><td className="py-3 px-2 tabular-nums">{number(m.median_view)}</td><td className="py-3 px-2 tabular-nums">{m.favorite_rate == null ? "—" : `${(100 * m.favorite_rate).toFixed(2)}%`}<p className="text-text-muted mt-1">{m.rate_samples}条样本 · {number(m.rate_favorites)}/{number(m.rate_views)}</p></td></tr>)}</tbody></table></div>
    <p className="text-xs text-text-muted">播放样本仅含已公开且返回播放数的视频；收藏率按收藏与播放均有效的样本合计计算。累计汇总受发布天数影响，当前用于描述现状；发布后24小时/7天的同龄比较待采集覆盖后增加。</p>
  </details>;
}

function Trend({ points, label }: { points: { label: string; value: number | null }[]; label: string }) {
  const values = points.flatMap(p => p.value == null ? [] : [p.value]);
  if (!values.length) return <p className="text-sm text-text-muted py-6">暂无可展示的数据。</p>;
  const min = Math.min(0, ...values), max = Math.max(1, ...values), width = 800, height = 145;
  const x = (index: number) => 12 + index / Math.max(1, points.length - 1) * (width - 24);
  const y = (value: number) => 10 + (max - value) / (max - min) * (height - 28);
  const paths: string[][] = [[]];
  points.forEach((p, i) => { if (p.value == null) paths.push([]); else paths[paths.length - 1].push(`${x(i)},${y(p.value)}`); });
  return <div className="space-y-2">
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-36 text-accent" role="img" aria-label={label}>
      <line x1="12" x2={width - 12} y1={y(0)} y2={y(0)} stroke="currentColor" opacity=".15" />
      {paths.map((path, i) => <polyline key={i} points={path.join(" ")} stroke="currentColor" strokeWidth="2" fill="none" />)}
      {points.map((p, i) => p.value == null ? null : <circle key={i} cx={x(i)} cy={y(p.value)} r="3" fill="currentColor"><title>{p.label} · {number(p.value)}</title></circle>)}
    </svg>
    <div className="flex justify-between text-xs text-text-muted"><span>{points[0]?.label}</span><span>{points[points.length - 1]?.label}</span></div>
    <details className="text-xs text-text-muted"><summary className="cursor-pointer">查看{label}明细</summary><div className="max-h-48 overflow-auto mt-2"><table className="w-full text-left"><thead><tr><th className="py-1">日期 / 采集时间</th><th>数值</th></tr></thead><tbody>{points.map((p, i) => <tr key={i}><td className="py-1">{p.label}</td><td>{number(p.value)}</td></tr>)}</tbody></table></div></details>
  </div>;
}

function VideoDetails({ bvid, onClose }: { bvid: string; onClose: () => void }) {
  const panel = useRef<HTMLElement>(null);
  const [days, setDays] = useState(30);
  const [metric, setMetric] = useState<Metric>("view");
  const load = useCallback((signal: AbortSignal) => loadVideoData(bvid, days, signal), [bvid, days]);
  const { data, error, reload } = usePageResource(load, 15000);
  const ready = !!data;
  useEffect(() => { if (ready) panel.current?.scrollIntoView?.({ behavior: "smooth", block: "start" }); }, [bvid, ready]);
  const video = data?.video, history = data?.history;
  return <section ref={panel} className="card p-4 space-y-4 scroll-mt-4" aria-label="单条视频数据">
    <div className="flex justify-between items-start gap-3"><div><h2 className="font-medium">{video?.title || bvid}</h2><p className="text-xs text-text-muted mt-1">{bvid} · {video?.tname || "分区待同步"}</p></div><button className="btn text-xs" aria-label="关闭视频详情" onClick={onClose}><X className="w-4 h-4" /></button></div>
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    {!data && !error ? <p className="text-sm text-text-muted">正在读取视频快照…</p> : null}
    {video && history ? <>
      <p className="text-xs text-text-muted">发布于 {date(video.pubdate, false)} · 时长 {Math.floor(video.duration / 60)}分{video.duration % 60}秒 · 累计数据采集于 {date(video.stats_at)}</p>
      <p className="text-xs text-text-muted">矩阵：{matrixLabel(video.publication)}{video.publication?.matrix_archived ? "（已归档）" : ""}{video.publication?.job_name ? ` · 来源任务：${video.publication.job_name}` : ""}{video.publication?.production_name ? ` · 生产配置：${video.publication.production_name}` : ""}</p>
      {!video.available || video.error ? <p className="text-xs text-warning">{video.error || video.state_desc || "视频尚未公开，暂无公开统计。"}{video.stats_at ? " 下方保留最后一次成功数据。" : ""}</p> : null}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">{METRICS.map(key => <div className="border border-border rounded-lg p-3" key={key}><p className="text-xs text-text-muted">累计{LABELS[key]}</p><p className="text-lg font-semibold mt-1 tabular-nums">{number(video.metrics[key])}</p></div>)}</div>
      <div className="flex gap-4 flex-wrap text-xs text-text-muted">{(["like", "coin", "favorite"] as Metric[]).map(key => <span key={key}>{LABELS[key]}率 {video.metrics.view && video.metrics[key] != null ? `${(100 * video.metrics[key]! / video.metrics.view).toFixed(2)}%（${number(video.metrics[key])}/${number(video.metrics.view)}）` : "—"}</span>)}</div>
      <div className="flex flex-wrap gap-3 text-xs"><a className="text-accent inline-flex items-center gap-1" href={`https://www.bilibili.com/video/${bvid}`} target="_blank" rel="noreferrer">打开 B 站视频<ExternalLink className="w-3 h-3" /></a>{video.publication ? <Link className="text-accent" to={documentPublishLink(video.publication.script_id, video.publication.version)}>查看文稿 v{video.publication.version} →</Link> : <span className="text-text-muted">站内历史视频 · 未关联本地文稿</span>}<Link className="text-accent" to={`/platforms?tab=comments&bvid=${bvid}`}>前往评论收件箱 →</Link></div>
      <p className="text-xs text-text-muted">已采集待处理观众评论：{data.comments?.synced_at ? number(data.comments.pending) : "尚未采集"}。平台评论总数包含作者评论及回复；收件箱仅覆盖已同步的评论。</p>
      <div className="border-t border-border pt-4 space-y-3"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium mr-auto">单视频采集趋势</h3><select aria-label="趋势指标" className="input w-auto text-xs" value={metric} onChange={e => setMetric(e.target.value as Metric)}>{METRICS.map(key => <option key={key} value={key}>累计{LABELS[key]}</option>)}</select><select aria-label="单视频趋势范围" className="input w-auto text-xs" value={days} onChange={e => setDays(Number(e.target.value))}><option value={7}>近7天</option><option value={30}>近30天</option></select></div>
        <p className="text-xs text-text-muted">首次建立基线：{date(history.baseline_at)}。旧视频从首次采集开始积累，平台未提供的历史不会补算。</p>
        {history.points.length < 2 ? <p className="text-sm text-text-muted py-5">{history.baseline_at ? "已建立基线，等待下一次采集；所选范围内需要至少两次采集才能比较。" : "尚未建立公开统计基线。"}</p> : <><p className="text-sm">采集区间{LABELS[metric]}净变化 <strong className="tabular-nums">{signed(history.delta[metric])}</strong></p><p className="text-xs text-text-muted">{date(history.points[0].at)} 至 {date(history.points[history.points.length - 1].at)}；净变化可能因撤销互动或平台校正为负。</p><Trend label={`累计${LABELS[metric]}`} points={history.points.map(p => ({ label: date(p.at), value: p.metrics[metric] }))} /></>}
      </div>
    </> : null}
  </section>;
}

export function VideoAnalytics({ isAdmin, canEdit }: { isAdmin: boolean; canEdit: boolean }) {
  const [period, setPeriod] = useState(7), [query, setQuery] = useState(""), [search, setSearch] = useState("");
  const [scope, setScope] = useState("all"), [sort, setSort] = useState("date"), [page, setPage] = useState(1);
  const [matrix, setMatrix] = useState("");
  const [selected, setSelected] = useState(""), [busy, setBusy] = useState(false), [actionError, setActionError] = useState("");
  useEffect(() => { const timer = setTimeout(() => { setSearch(query.trim()); setPage(1); }, 300); return () => clearTimeout(timer); }, [query]);
  const load = useCallback((signal: AbortSignal) => loadAnalytics({ period, q: search, scope, matrix, sort, page, size: 25 }, signal), [period, search, scope, matrix, sort, page]);
  const { data, error, loading, reload } = usePageResource(load, 15000, true);
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true); setActionError("");
    try { await action(); await reload(); } catch (e) { setActionError(errorMessage(e, "操作失败")); } finally { setBusy(false); }
  };
  const collecting = data?.collector.status === "QUEUED" || data?.collector.status === "RUNNING";
  const enabled = !!data?.collector.enabled && !!data?.schedule.enabled;
  const overviewPeriod = data?.overview?.period || period;
  return <div className="space-y-4">
    <section className="card p-4 space-y-3"><div className="flex flex-wrap justify-between gap-3 items-center"><div><h2 className="text-sm font-medium">视频数据采集</h2><p className="text-xs text-text-muted mt-1">{enabled ? data?.schedule.online ? "定时采集已开启 · 独立调度器在线" : "定时采集已开启 · 执行器离线" : "定时采集已暂停"}</p></div><div className="flex gap-2">{isAdmin ? <button disabled={busy || loading} className="btn text-xs" onClick={() => void run(() => enableVideoData(!enabled))}>{enabled ? "暂停定时采集" : "开启定时采集"}</button> : null}{canEdit ? <button className="btn btn-primary text-xs" disabled={busy || collecting || !data || data.collector.blocked_until > Date.now() / 1000} onClick={() => void run(syncVideoData)}><RefreshCw className="w-3.5 h-3.5" />{collecting ? "正在同步…" : "立即同步数据"}</button> : null}</div></div>
      <p className="text-xs text-text-muted">发布首日约每30分钟，1–7天约每2小时，旧视频每天采集。账号趋势约每6小时更新；页面刷新读取本地缓存。</p>
      {data ? <p className="text-xs text-text-muted">最近采集完成：{date(data.collector.last_success_at)}{enabled ? ` · 下次调度：${date(data.schedule.next_run_at)}` : ""}{collecting ? ` · 正在读取目录第${data.collector.page}页，已保存${data.catalog_total}条` : ""}</p> : null}
      {data?.collector.error ? <p className="text-xs text-warning" role="status">{data.collector.error}{data.collector.blocked_until > Date.now() / 1000 ? ` 退避至 ${date(data.collector.blocked_until)}。` : ""} 已保留上次成功数据。</p> : null}
      {actionError ? <p role="alert" className="text-sm text-danger">{actionError}</p> : null}{error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    </section>
    {!data && !error ? <p className="text-sm text-text-muted">正在读取数据缓存…</p> : null}
    {data ? <>
      <section className="card p-4 space-y-4"><div className="flex flex-wrap gap-2 items-center justify-between"><div><h2 className="text-sm font-medium">账号概览</h2><p className="text-xs text-text-muted mt-1">账号全部视频 · 不受下方视频筛选影响</p></div><select aria-label="账号统计范围" className="input text-xs w-auto" value={period} onChange={e => setPeriod(Number(e.target.value))}><option value={7}>近7天</option><option value={30}>近30天</option></select></div>
        {data.overview_error ? <p className="text-xs text-warning">账号概览更新失败：{data.overview_error.error} 保留已有结果。</p> : null}
        <div aria-busy={loading} className="grid grid-cols-2 md:grid-cols-4 gap-3">{(["view", "like", "favorite", "fan"] as const).map(key => <div className="rounded-lg border border-border p-3" key={key}><p className="text-xs text-text-muted">{key === "fan" ? "账号净增粉丝" : `近${overviewPeriod}天${LABELS[key]}`}</p><p className="text-2xl font-semibold tabular-nums mt-2">{number(data.overview?.summary[key])}</p></div>)}</div>
        {data.overview ? <><p className="text-xs text-text-muted">统计截至 {data.overview.platform_date} · 最近同步 {date(data.overview.synced_at)} · 粉丝为账号口径，无法归因到单条视频。</p><h3 className="text-sm font-medium">账号每日新增播放</h3><Trend label="每日新增播放" points={data.overview.points.map(p => ({ label: p.date, value: p.value }))} /></> : <p className="text-sm text-text-muted">尚未采集账号统计，首次同步后显示。</p>}
      </section>
      {data.matrices?.length ? <MatrixSummary matrices={data.matrices} selected={matrix} onSelect={value => { setMatrix(value); setPage(1); }} /> : null}
      <section className="card p-4 space-y-4"><div className="flex flex-wrap items-center gap-3"><h2 className="text-sm font-medium mr-auto">视频表现 <span className="text-text-muted">{data.videos.total}条</span></h2><span className="text-xs text-text-muted">截至各视频最近采集的累计数据</span></div>
        <div className="flex gap-2 flex-wrap"><input aria-label="搜索视频数据" placeholder="搜索标题或 BV 号" className="input text-xs flex-1 min-w-[180px]" value={query} onChange={e => setQuery(e.target.value)} /><select aria-label="视频数据范围" className="input text-xs w-auto" value={scope} onChange={e => { setScope(e.target.value); setPage(1); }}><option value="all">账号全部视频</option><option value="workflow">本工作流视频</option></select>{data.matrices ? <select aria-label="视频数据矩阵" className="input text-xs w-auto max-w-[220px]" value={matrix} onChange={e => { setMatrix(e.target.value); setPage(1); }}><option value="">全部矩阵</option>{matrix && !data.matrices.some(m => m.key === matrix) ? <option value={matrix}>当前矩阵（暂无视频）</option> : null}{data.matrices.map(m => <option key={m.key} value={m.key}>{m.name}（{m.videos}）</option>)}</select> : null}<select aria-label="视频数据排序" className="input text-xs w-auto" value={sort} onChange={e => { setSort(e.target.value); setPage(1); }}><option value="date">发布时间</option><option value="view">累计播放</option><option value="favorite_rate">收藏率</option></select></div>
        {data.videos.items.length ? <div className="overflow-x-auto"><table className="w-full text-xs text-left"><thead className="text-text-muted"><tr><th className="pb-3 font-normal min-w-[240px]">视频 / 发布时间</th>{(["view", "like", "coin", "favorite", "reply"] as Metric[]).map(key => <th className="pb-3 px-3 font-normal text-right whitespace-nowrap" key={key}>累计{LABELS[key]}</th>)}</tr></thead><tbody>{data.videos.items.map(v => <tr key={v.bvid} className="border-t border-border"><td className="py-3 pr-4"><button className="text-left text-accent hover:underline text-sm" onClick={() => setSelected(v.bvid)}>{v.title}</button><p className="text-text-muted mt-1">{date(v.pubdate, false)} · {v.publication ? `文稿 v${v.publication.version}` : "站内历史视频"} · {matrixLabel(v.publication)}{v.publication?.matrix_archived ? "（已归档）" : ""}</p><p className="text-text-muted mt-1">{v.bvid} · {v.missing ? "暂不可访问" : v.state === 0 ? "已公开" : v.state_desc || "未公开"}{v.error ? " · 采集异常，保留旧数据" : ""}</p><p className="text-text-muted mt-1">统计采集：{date(v.stats_at)}</p></td>{(["view", "like", "coin", "favorite", "reply"] as Metric[]).map(key => <td className="py-3 px-3 text-right tabular-nums" key={key}>{v.available ? number(v.metrics[key]) : "—"}</td>)}</tr>)}</tbody></table></div> : <p className="text-sm text-text-muted py-6 text-center">{data.catalog_total ? "没有符合筛选的视频。" : "视频目录尚未采集，开启采集或立即同步后显示。"}</p>}
        <div className="flex justify-between items-center text-xs text-text-muted"><span>第{page} / {Math.max(1, Math.ceil(data.videos.total / 25))}页 · {data.catalog_total}条目录已缓存{loading ? " · 正在刷新" : ""}</span><div className="flex gap-2"><button className="btn text-xs" disabled={page <= 1 || loading} onClick={() => setPage(p => p - 1)}>上一页</button><button className="btn text-xs" disabled={page * 25 >= data.videos.total || loading} onClick={() => setPage(p => p + 1)}>下一页</button></div></div>
      </section>
    </> : null}
    {selected ? <VideoDetails key={selected} bvid={selected} onClose={() => setSelected("")} /> : null}
  </div>;
}
