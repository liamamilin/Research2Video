import { Fragment, useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import {
  loadComparison, loadPerformance, loadScriptBills, loadScriptDetail, loadStorage, loadTelemetrySummary, purgeRecords,
  type CompareBy, type CostBasis, type ModelUsage, type PurgeResult, type ScriptBill, type StageUsage, type Usage,
} from "@/api/telemetry";
import { usePageResource } from "@/lib/use-page-resource";
import { errorMessage } from "@/lib/toast";
import { ErrorState } from "./ErrorState";

const STAGES: Record<string, string> = {
  research: "调研", "story-pool": "选题池", facts: "拆事实", plan: "定方案", write: "写稿", audit: "审校", revise: "修改",
  "await-approval": "等待定稿", render: "出片（合计）", "render/prepare-input": "出片 · 准备输入", "render/narration-plan": "出片 · 旁白方案",
  "render/narration-form": "出片 · 叙述形式", "render/storyboard-plan": "出片 · 分镜规划", "render/tts-and-align": "出片 · 配音与对齐",
  "render/compile-ir": "出片 · 编译", "render/render": "出片 · 渲染", "render/verify-media": "出片 · 校验", "render/package": "出片 · 打包",
  "render/brand": "出片 · 品牌", "render/explanation": "出片 · 讲解", "render/information": "出片 · 信息", "render/diagram": "出片 · 图表",
  "render/card-editorial": "出片 · 卡片编辑", assistant: "助手", "assistant-test": "助手 · 连通测试", qa: "问答",
};
const BASES: Record<CostBasis, string> = { reported: "服务商实收", snapshot: "当时单价", table: "价格表", unpriced: "未定价" };
const RANGES = [{ days: 7, label: "近 7 天" }, { days: 30, label: "近 30 天" }, { days: 90, label: "近 90 天" }, { days: 0, label: "全部" }];
const COMPARE: { by: CompareBy; label: string }[] = [
  { by: "column_key", label: "栏目" }, { by: "form", label: "出片形式" }, { by: "matrix", label: "矩阵" },
  { by: "recipe", label: "配方" }, { by: "path", label: "写稿路径" },
];
const COST_KEY = "analytics.showCost";

export const stageLabel = (stage: string) => STAGES[stage] || stage || "（未归环节）";
export const tokens = (value: number) => value >= 1e8 ? `${(value / 1e8).toFixed(2)} 亿` : value >= 1e4 ? `${(value / 1e4).toFixed(1)} 万` : value.toLocaleString("zh-CN");
export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 1) return "<1 秒";
  if (seconds < 90) return `${Math.round(seconds)} 秒`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} 分钟`;
  return `${(seconds / 3600).toFixed(1)} 小时`;
}
const dollars = (value: number) => `$${value >= 100 ? value.toFixed(0) : value >= 1 ? value.toFixed(2) : value.toFixed(4)}`;

function Money({ usage, show }: { usage: Usage; show: boolean }) {
  if (!show) return null;
  const hint = [usage.partial ? `另有 ${usage.unpriced_calls} 次调用未定价，金额不含它们` : "", usage.bases.map(b => BASES[b]).join("、")].filter(Boolean).join("；");
  return <span title={hint}>{dollars(usage.cost_usd)}{usage.partial ? "+" : ""}</span>;
}

function Card({ label, value, note }: { label: string; value: React.ReactNode; note?: string }) {
  return <div className="card p-3"><p className="text-xs text-text-muted">{label}</p><p className="text-lg font-semibold tabular-nums mt-1">{value}</p>{note ? <p className="text-xs text-text-muted mt-1">{note}</p> : null}</div>;
}

function StageTable({ rows, showCost }: { rows: StageUsage[]; showCost: boolean }) {
  return <div className="overflow-x-auto"><table className="w-full text-xs text-left">
    <thead className="text-text-muted"><tr><th className="pb-2 font-normal">环节</th><th className="pb-2 font-normal text-right whitespace-nowrap">耗时</th><th className="pb-2 font-normal text-right whitespace-nowrap">模型调用</th><th className="pb-2 font-normal text-right whitespace-nowrap">输入</th><th className="pb-2 font-normal text-right whitespace-nowrap">输出</th>{showCost ? <th className="pb-2 font-normal text-right whitespace-nowrap">折算</th> : null}</tr></thead>
    <tbody>{rows.map(r => <tr key={r.stage} className="border-t border-border">
      <td className="py-2">{stageLabel(r.stage)}{r.failed_spans ? <span className="text-warning ml-2">失败 {r.failed_spans}</span> : null}</td>
      <td className="py-2 text-right tabular-nums whitespace-nowrap">{r.span_count ? duration(r.span_seconds) : "—"}</td>
      <td className="py-2 text-right tabular-nums whitespace-nowrap">{r.requests || "—"}</td>
      <td className="py-2 text-right tabular-nums whitespace-nowrap">{r.requests ? tokens(r.prompt_tokens) : "—"}</td>
      <td className="py-2 text-right tabular-nums whitespace-nowrap">{r.requests ? tokens(r.completion_tokens) : "—"}</td>
      {showCost ? <td className="py-2 text-right tabular-nums whitespace-nowrap">{r.requests ? <Money usage={r} show /> : "—"}</td> : null}
    </tr>)}</tbody></table></div>;
}

function ModelTable({ rows, showCost }: { rows: ModelUsage[]; showCost: boolean }) {
  return <div className="overflow-x-auto"><table className="w-full text-xs text-left">
    <thead className="text-text-muted"><tr><th className="pb-2 font-normal">服务商 / 模型</th><th className="pb-2 font-normal text-right whitespace-nowrap">请求</th><th className="pb-2 font-normal text-right whitespace-nowrap">输入</th><th className="pb-2 font-normal text-right whitespace-nowrap">输出</th><th className="pb-2 font-normal text-right whitespace-nowrap">失败</th>{showCost ? <th className="pb-2 font-normal text-right whitespace-nowrap">折算</th> : null}</tr></thead>
    <tbody>{rows.map(r => <tr key={`${r.kind}|${r.provider}|${r.model}`} className="border-t border-border">
      <td className="py-2">{r.kind === "search" ? `搜索 · ${r.provider || "（早期记录，未记服务商）"}` : <>{r.model || "（未记录模型）"}<span className="text-text-muted ml-2">{r.provider || "早期记录，未记服务商"}</span></>}</td>
      <td className="py-2 text-right tabular-nums whitespace-nowrap">{r.requests}</td>
      <td className="py-2 text-right tabular-nums whitespace-nowrap">{r.kind === "llm" ? tokens(r.prompt_tokens) : "—"}</td>
      <td className="py-2 text-right tabular-nums whitespace-nowrap">{r.kind === "llm" ? tokens(r.completion_tokens) : "—"}</td>
      <td className="py-2 text-right tabular-nums whitespace-nowrap">{r.failed || "—"}</td>
      {showCost ? <td className="py-2 text-right tabular-nums whitespace-nowrap">{r.kind === "llm" ? (r.bases.includes("unpriced") && r.bases.length === 1 ? "未定价" : <Money usage={r} show />) : "—"}</td> : null}
    </tr>)}</tbody></table></div>;
}

function BillDetail({ id, showCost }: { id: string; showCost: boolean }) {
  const load = useCallback((signal: AbortSignal) => loadScriptDetail(id, signal), [id]);
  const { data, error } = usePageResource(load);
  if (error) return <ErrorState error={error} compact />;
  if (!data) return <p className="text-xs text-text-muted py-3">正在读取…</p>;
  return <div className="space-y-4 py-3">
    <StageTable rows={data.stages} showCost={showCost} />
    <ModelTable rows={data.models} showCost={showCost} />
    <p className="text-xs text-text-muted">出片的各步骤之间有并行（分镜规划与配音同时进行），步骤耗时相加会大于「出片（合计）」。</p>
  </div>;
}

function Bills({ days, showCost }: { days: number; showCost: boolean }) {
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState("");
  const load = useCallback((signal: AbortSignal) => loadScriptBills(days, offset, signal), [days, offset]);
  const { data, error, reload } = usePageResource(load, 15000);
  const columns = 7 + (showCost ? 1 : 0);
  return <section className="card p-4 space-y-3">
    <div><h2 className="text-sm font-medium">按视频{data ? <span className="text-text-muted font-normal ml-2">{data.total} 条</span> : null}</h2>
      <p className="text-xs text-text-muted mt-1">每条视频从调研到成片的用量。调研 + 写稿 + 审校是制作耗时；等待定稿单独列出，不算制作。</p></div>
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    {data && !data.items.length ? <ErrorState empty={data.syncing ? "正在读取成片与历史记录，稍后刷新" : "这个时间范围内还没有按视频归属的记录"} compact /> : null}
    {data?.items.length ? <div className="overflow-x-auto"><table className="w-full text-xs text-left">
      <thead className="text-text-muted"><tr><th className="pb-2 font-normal min-w-[220px]">视频</th><th className="pb-2 font-normal text-right whitespace-nowrap">制作</th><th className="pb-2 font-normal text-right whitespace-nowrap">等待定稿</th><th className="pb-2 font-normal text-right whitespace-nowrap">出片</th><th className="pb-2 font-normal text-right whitespace-nowrap">模型调用</th><th className="pb-2 font-normal text-right whitespace-nowrap">输入</th><th className="pb-2 font-normal text-right whitespace-nowrap">输出</th>{showCost ? <th className="pb-2 font-normal text-right whitespace-nowrap">折算</th> : null}</tr></thead>
      <tbody>{data.items.map((b: ScriptBill) => <Fragment key={b.script_id}>
        <tr className="border-t border-border">
          <td className="py-2 pr-3"><button className="text-left text-accent hover:underline text-sm" aria-expanded={open === b.script_id} onClick={() => setOpen(open === b.script_id ? "" : b.script_id)}>{b.title || b.script_id.slice(0, 8)}</button>
            <p className="text-text-muted mt-1">{[b.column_key, b.form].filter(Boolean).join(" · ") || "—"} · <Link className="text-accent" to={`/video/${b.script_id}`}>打开文稿</Link></p></td>
          <td className="py-2 text-right tabular-nums whitespace-nowrap">{b.produce_seconds ? duration(b.produce_seconds) : "—"}</td>
          <td className="py-2 text-right tabular-nums whitespace-nowrap">{b.wait_seconds ? duration(b.wait_seconds) : "—"}</td>
          <td className="py-2 text-right tabular-nums whitespace-nowrap">{b.render_seconds ? duration(b.render_seconds) : "—"}</td>
          <td className="py-2 text-right tabular-nums whitespace-nowrap">{b.requests}</td>
          <td className="py-2 text-right tabular-nums whitespace-nowrap">{tokens(b.prompt_tokens)}</td>
          <td className="py-2 text-right tabular-nums whitespace-nowrap">{tokens(b.completion_tokens)}</td>
          {showCost ? <td className="py-2 text-right tabular-nums whitespace-nowrap"><Money usage={b} show /></td> : null}
        </tr>
        {open === b.script_id ? <tr><td colSpan={columns} className="bg-bg-muted/40 px-3"><BillDetail id={b.script_id} showCost={showCost} /></td></tr> : null}
      </Fragment>)}</tbody></table></div> : null}
    {data && data.total > 25 ? <div className="flex gap-2 items-center text-xs"><button className="btn text-xs" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>上一页</button><span className="text-text-muted">{offset + 1}–{Math.min(offset + 25, data.total)} / {data.total}</span><button className="btn text-xs" disabled={offset + 25 >= data.total} onClick={() => setOffset(offset + 25)}>下一页</button></div> : null}
  </section>;
}

function Comparison({ days, showCost }: { days: number; showCost: boolean }) {
  const [by, setBy] = useState<CompareBy>("column_key");
  const load = useCallback((signal: AbortSignal) => loadComparison(by, days, signal), [by, days]);
  const { data, error, reload } = usePageResource(load, 30000);
  return <section className="card p-4 space-y-3">
    <div className="flex flex-wrap items-center gap-3"><div className="mr-auto"><h2 className="text-sm font-medium">链路对比</h2><p className="text-xs text-text-muted mt-1">同类视频的典型耗时（中位数）和平均用量。</p></div>
      <label className="text-xs text-text-muted flex items-center gap-2">按<select aria-label="对比维度" className="input text-xs w-auto" value={by} onChange={e => setBy(e.target.value as CompareBy)}>{COMPARE.map(c => <option key={c.by} value={c.by}>{c.label}</option>)}</select></label></div>
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    {data && !data.groups.length ? <ErrorState empty="还没有可比较的视频" compact /> : null}
    {data?.groups.length ? <div className="overflow-x-auto"><table className="w-full text-xs text-left">
      <thead className="text-text-muted"><tr><th className="pb-2 font-normal">{COMPARE.find(c => c.by === by)?.label}</th><th className="pb-2 font-normal text-right whitespace-nowrap">视频数</th><th className="pb-2 font-normal text-right whitespace-nowrap">制作耗时</th><th className="pb-2 font-normal text-right whitespace-nowrap">出片耗时</th><th className="pb-2 font-normal text-right whitespace-nowrap">等待定稿</th><th className="pb-2 font-normal text-right whitespace-nowrap">平均 token</th>{showCost ? <th className="pb-2 font-normal text-right whitespace-nowrap">平均折算</th> : null}</tr></thead>
      <tbody>{data.groups.map(g => <tr key={g.value} className="border-t border-border">
        <td className="py-2">{g.value || "（未记录）"}</td><td className="py-2 text-right tabular-nums whitespace-nowrap">{g.scripts}</td>
        <td className="py-2 text-right tabular-nums whitespace-nowrap">{duration(g.produce_median_s)}</td><td className="py-2 text-right tabular-nums whitespace-nowrap">{duration(g.render_median_s)}</td><td className="py-2 text-right tabular-nums whitespace-nowrap">{duration(g.wait_median_s)}</td>
        <td className="py-2 text-right tabular-nums whitespace-nowrap">{tokens(g.tokens_avg)}</td>
        {showCost ? <td className="py-2 text-right tabular-nums whitespace-nowrap" title={g.cost_samples < g.scripts ? `${g.scripts - g.cost_samples} 条含未定价调用，未计入平均` : undefined}>{g.cost_avg_usd == null ? "未定价" : dollars(g.cost_avg_usd)}</td> : null}
      </tr>)}</tbody></table></div> : null}
  </section>;
}

function Performance({ days, showCost }: { days: number; showCost: boolean }) {
  const [by, setBy] = useState<CompareBy>("column_key");
  const [all, setAll] = useState(false);
  const load = useCallback((signal: AbortSignal) => loadPerformance(by, days, signal), [by, days]);
  const { data, error, reload } = usePageResource(load, 60000);
  const label = COMPARE.find(c => c.by === by)?.label;
  const items = data?.items ? (all ? data.items : data.items.slice(0, 10)) : [];
  const th = "pb-2 font-normal text-right whitespace-nowrap";
  const td = "py-2 text-right tabular-nums whitespace-nowrap";
  return <section className="card p-4 space-y-3">
    <div className="flex flex-wrap items-center gap-3"><div className="mr-auto"><h2 className="text-sm font-medium">成本与播放</h2><p className="text-xs text-text-muted mt-1">已发布到哔哩哔哩的视频，从调研到成片花了多少，换来多少播放。成本按发布日期落在所选范围内的视频算；播放量取自视频数据最近一次采集。</p></div>
      <label className="text-xs text-text-muted flex items-center gap-2">按<select aria-label="成本对照维度" className="input text-xs w-auto" value={by} onChange={e => setBy(e.target.value as CompareBy)}>{COMPARE.map(c => <option key={c.by} value={c.by}>{c.label}</option>)}</select></label></div>
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    {data && data.connected === false ? <p className="text-sm text-text-muted">先在平台管理里连接哔哩哔哩账号并开启视频数据采集，才有播放量可以对照。</p> : null}
    {data && data.connected !== false && !data.items.length ? <ErrorState empty="范围内还没有发布到哔哩哔哩的视频" compact /> : null}
    {data?.groups.length ? <div className="overflow-x-auto"><table className="w-full text-xs text-left">
      <thead className="text-text-muted"><tr><th className="pb-2 font-normal">{label}</th><th className={th}>视频数</th><th className={th}>播放合计</th><th className={th}>播放中位数</th><th className={th}>每千次播放 token</th>{showCost ? <th className={th}>每千次播放成本</th> : null}</tr></thead>
      <tbody>{data.groups.map(g => <tr key={g.value} className="border-t border-border">
        <td className="py-2">{g.value || "（未记录）"}</td><td className={td}>{g.videos}</td><td className={td} title={g.watched < g.videos ? `${g.videos - g.watched} 条没有播放量或没有成本记录，未计入` : undefined}>{g.views.toLocaleString("zh-CN")}</td>
        <td className={td}>{g.view_median == null ? "—" : Math.round(g.view_median).toLocaleString("zh-CN")}</td><td className={td}>{g.tokens_per_kview == null ? "—" : tokens(Math.round(g.tokens_per_kview))}</td>
        {showCost ? <td className={td} title={g.cost_samples < g.watched || g.cost_partial ? "有些调用没有价格，金额偏低；完全没有价格的视频未计入" : undefined}>{g.cost_per_kview == null ? "未定价" : `${dollars(g.cost_per_kview)}${g.cost_partial ? "+" : ""}`}</td> : null}
      </tr>)}</tbody></table></div> : null}
    {items.length ? <div className="overflow-x-auto"><table className="w-full text-xs text-left">
      <thead className="text-text-muted"><tr><th className="pb-2 font-normal">视频</th><th className={th}>播放</th><th className={th}>制作 + 出片</th><th className={th}>token</th>{showCost ? <th className={th}>成本</th> : null}{showCost ? <th className={th}>每千次播放成本</th> : null}</tr></thead>
      <tbody>{items.map(v => <tr key={v.bvid} className="border-t border-border">
        <td className="py-2 max-w-[24rem] truncate"><a className="hover:text-accent" href={`https://www.bilibili.com/video/${v.bvid}`} target="_blank" rel="noreferrer">{v.title || v.bvid}</a></td>
        <td className={td}>{v.view == null ? "—" : v.view.toLocaleString("zh-CN")}</td>
        <td className={td}>{v.billed ? duration((v.produce_seconds ?? 0) + (v.render_seconds ?? 0)) : "—"}</td>
        <td className={td}>{v.tokens == null ? "—" : tokens(v.tokens)}</td>
        {showCost ? <td className={td}>{v.cost_usd == null ? "—" : `${dollars(v.cost_usd)}${v.partial ? "+" : ""}`}</td> : null}
        {showCost ? <td className={td}>{v.cost_per_kview == null ? "—" : `${dollars(v.cost_per_kview)}${v.partial ? "+" : ""}`}</td> : null}
      </tr>)}</tbody></table></div> : null}
    {data && data.items.length > 10 ? <button className="btn text-xs" onClick={() => setAll(!all)}>{all ? "只看最近 10 条" : `显示全部 ${data.items.length} 条`}</button> : null}
  </section>;
}

const KEEP = [{ days: 30, label: "30 天" }, { days: 90, label: "90 天" }, { days: 180, label: "180 天" }, { days: 365, label: "1 年" }];
const size = (bytes: number) => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
const day = (seconds: number | null | undefined) => seconds ? new Date(seconds * 1000).toLocaleDateString("zh-CN") : "—";

/** Records are never removed on their own; this is the only way they go, and it shows what it will remove first. */
function Retention({ onChanged }: { onChanged: () => void }) {
  const [keep, setKeep] = useState(365);
  const [preview, setPreview] = useState<PurgeResult | null>(null);
  const [message, setMessage] = useState(""), [busy, setBusy] = useState(false);
  const load = useCallback((signal: AbortSignal) => loadStorage(signal), []);
  const { data, error, reload } = usePageResource(load);
  const run = async (dryRun: boolean) => {
    setBusy(true); setMessage("");
    try {
      const result = await purgeRecords(keep, dryRun);
      if (dryRun) { setPreview(result); return; }
      setPreview(null); setMessage(`已删除 ${result.calls} 条调用记录、${result.spans} 条环节记录`);
      await reload(); onChanged();
    } catch (e) { setMessage(errorMessage(e, "操作失败")); } finally { setBusy(false); }
  };
  return <section className="card p-4 space-y-3">
    <div><h2 className="text-sm font-medium">记录与清理</h2><p className="text-xs text-text-muted mt-1">记录不会自动删除，只在这里手动清理。清理不影响文稿、成片和发布记录。</p></div>
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    {data?.enabled ? <p className="text-xs text-text-muted">共 {data.calls?.toLocaleString("zh-CN")} 条调用记录、{data.spans?.toLocaleString("zh-CN")} 条环节记录，最早 {day(data.oldest)}，占用 {size(data.bytes || 0)}。</p> : null}
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <label className="flex items-center gap-2">只保留最近<select aria-label="保留时长" className="input text-xs w-auto" value={keep} onChange={e => { setKeep(Number(e.target.value)); setPreview(null); }}>{KEEP.map(k => <option key={k.days} value={k.days}>{k.label}</option>)}</select></label>
      <button className="btn text-xs" disabled={busy} onClick={() => void run(true)}>查看会清理多少</button>
      {preview ? <>
        <span role="status">将删除 {preview.calls.toLocaleString("zh-CN")} 条调用记录、{preview.spans.toLocaleString("zh-CN")} 条环节记录（{day(preview.cutoff)} 之前）</span>
        {preview.calls || preview.spans ? <button className="btn text-xs text-danger" disabled={busy} onClick={() => void run(false)}>确认删除</button> : null}
      </> : null}
    </div>
    {message ? <p role="status" className="text-xs">{message}</p> : null}
  </section>;
}

function storedCost(): boolean {
  try { return localStorage.getItem(COST_KEY) !== "0"; } catch { return true; }
}

export function UsageAnalytics() {
  const [days, setDays] = useState(30);
  const [showCost, setShowCost] = useState(storedCost);
  const load = useCallback((signal: AbortSignal) => loadTelemetrySummary(days, signal), [days]);
  const { data, error, loading, reload } = usePageResource(load, 15000);
  const toggleCost = (value: boolean) => { setShowCost(value); try { localStorage.setItem(COST_KEY, value ? "1" : "0"); } catch { /* per-viewer convenience only */ } };
  const llm = data?.llm, search = data?.search;
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-3">
      <select aria-label="统计范围" className="input text-xs w-auto" value={days} onChange={e => setDays(Number(e.target.value))}>{RANGES.map(r => <option key={r.days} value={r.days}>{r.label}</option>)}</select>
      <label className="text-xs flex items-center gap-2"><input type="checkbox" checked={showCost} onChange={e => toggleCost(e.target.checked)} />显示折算金额</label>
      <button className="btn text-xs ml-auto" disabled={loading} onClick={() => void reload()}><RefreshCw className="w-3.5 h-3.5" />刷新</button>
    </div>
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    {data && !data.enabled ? <ErrorState empty="用量记录尚未启用" compact /> : null}
    {data?.syncing ? <p role="status" className="text-xs text-text-muted">正在读取已完成的成片和历史记录，稍后会自动更新。</p> : null}
    {llm && search ? <>
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <Card label="模型请求" value={llm.requests.toLocaleString("zh-CN")} note={llm.failed ? `其中失败 / 切换 ${llm.failed}` : undefined} />
        <Card label="输入 token" value={tokens(llm.prompt_tokens)} note={llm.cache_read_tokens ? `缓存命中 ${tokens(llm.cache_read_tokens)}` : undefined} />
        <Card label="输出 token" value={tokens(llm.completion_tokens)} note={llm.reasoning_tokens ? `含推理 ${tokens(llm.reasoning_tokens)}` : undefined} />
        <Card label="搜索请求" value={search.requests.toLocaleString("zh-CN")} note={search.failed ? `失败 ${search.failed}` : undefined} />
        <Card label="累计模型耗时" value={duration(llm.seconds)} note="仅统计记录了耗时的请求" />
        {showCost ? <Card label="折算金额" value={<Money usage={llm} show />} note={llm.partial ? `${llm.unpriced_calls} 次调用未定价` : "不含搜索费用"} /> : null}
      </div>
      <Bills days={days} showCost={showCost} />
      <Comparison days={days} showCost={showCost} />
      <Performance days={days} showCost={showCost} />
      <section className="card p-4 space-y-3"><h2 className="text-sm font-medium">按环节</h2>{data.stages?.length ? <StageTable rows={data.stages} showCost={showCost} /> : <ErrorState empty="暂无记录" compact />}</section>
      <section className="card p-4 space-y-3"><h2 className="text-sm font-medium">按模型</h2>{data.models?.length ? <ModelTable rows={data.models} showCost={showCost} /> : <ErrorState empty="暂无记录" compact />}
        {showCost ? <div className="text-xs text-text-muted space-y-1">
          <p>折算金额按公开的正常标价（不计渠道折扣和套餐），免费模型记 0；优先采用服务商返回的实际花费，其次是出片时记录的当时单价，再次是 <code>config/pricing.yaml</code>，都没有就显示「未定价」，只给 token。</p>
          {data.unpriced_models?.length ? <p>未定价：{data.unpriced_models.join("、")}。早期历史记录没有服务商，按模型名取通用标价，可能与实际渠道不同。</p> : null}
        </div> : null}</section>
      <Retention onChanged={() => void reload()} />
    </> : null}
    {!data && !error ? <p className="text-sm text-text-muted">正在读取…</p> : null}
  </div>;
}
