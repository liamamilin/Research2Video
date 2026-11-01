import { Fragment, useCallback, useState } from "react";
import { RefreshCw } from "lucide-react";
import { loadAssistantRequest, loadAssistantUsage, type AssistantRequest } from "@/api/telemetry";
import { usePageResource } from "@/lib/use-page-resource";
import { COST_KEY, dollars, duration, storedCost, tokens } from "./UsageAnalytics";
import { ErrorState } from "./ErrorState";

const RANGES = [{ days: 7, label: "近 7 天" }, { days: 30, label: "近 30 天" }, { days: 90, label: "近 90 天" }, { days: 0, label: "全部" }];
const PAGE = 25;
const th = "pb-2 font-normal text-right whitespace-nowrap";
const td = "py-2 text-right tabular-nums whitespace-nowrap";
const percent = (value: number | null | undefined) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const chars = (value: number | null | undefined) => value == null ? "—" : tokens(Math.round(value));
const when = (seconds: number) => new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false });
const count = (names: string[]) => {
  const seen = new Map<string, number>();
  names.forEach(name => seen.set(name, (seen.get(name) || 0) + 1));
  return [...seen].map(([name, n]) => n > 1 ? `${name}×${n}` : name).join("、");
};

function RequestDetail({ id, showCost }: { id: string; showCost: boolean }) {
  const load = useCallback((signal: AbortSignal) => loadAssistantRequest(id, signal), [id]);
  const { data, error, reload } = usePageResource(load);
  if (error) return <ErrorState error={error} onRetry={reload} compact />;
  if (!data) return <p className="text-xs text-text-muted">正在读取…</p>;
  return <table className="w-full text-xs text-left">
    <thead className="text-text-muted"><tr><th className="pb-2 font-normal">轮次</th><th className={th}>输入 token</th><th className={th}>缓存命中</th><th className={th}>输出 token</th><th className={th}>发给模型的内容</th><th className={th}>工具定义</th><th className={th}>折叠旧结果</th>{showCost ? <th className={th}>折算</th> : null}<th className="pb-2 font-normal pl-4">这一轮调用的工具（返回内容大小）</th></tr></thead>
    <tbody>{data.turns.map(t => <tr key={t.turn} className="border-t border-border">
      <td className="py-2">第 {t.turn + 1} 轮</td><td className={td}>{t.prompt_tokens == null ? "—" : tokens(t.prompt_tokens)}</td>
      <td className={td}>{t.cache_read_tokens ? tokens(t.cache_read_tokens) : "—"}</td><td className={td}>{t.completion_tokens == null ? "—" : tokens(t.completion_tokens)}</td>
      <td className={td}>{chars(t.message_chars)} 字符</td><td className={td} title={t.tool_count == null ? undefined : `${t.tool_count} 个工具`}>{chars(t.tool_schema_chars)} 字符</td>
      <td className={td}>{t.retired ?? "—"}</td>
      {showCost ? <td className={td}>{t.cost_usd == null ? "—" : `${dollars(t.cost_usd)}${t.partial ? "+" : ""}`}</td> : null}
      <td className="py-2 pl-4">{t.tools.length ? t.tools.map((tool, i) => <span key={i} className="mr-3 whitespace-nowrap">{tool.name}{tool.chars == null ? "" : `（${chars(tool.chars)}）`}</span>) : <span className="text-text-muted">直接回答</span>}</td>
    </tr>)}</tbody></table>;
}

function Requests({ rows, total, offset, onPage, showCost }: { rows: AssistantRequest[]; total: number; offset: number; onPage: (offset: number) => void; showCost: boolean }) {
  const [open, setOpen] = useState("");
  return <section className="card p-4 space-y-3">
    <div><h2 className="text-sm font-medium">按请求</h2><p className="text-xs text-text-muted mt-1">一次提问（用户的一条消息）为一个请求，助手要分几轮调用模型才能答完。点开一条看每一轮用了什么。</p></div>
    {!rows.length ? <ErrorState empty="范围内还没有按请求记录的对话" compact /> : <div className="overflow-x-auto"><table className="w-full text-xs text-left">
      <thead className="text-text-muted"><tr><th className="pb-2 font-normal">时间</th><th className="pb-2 font-normal">问题</th><th className="pb-2 font-normal">用户</th><th className={th}>轮数</th><th className="pb-2 font-normal pl-4">调用的工具</th><th className={th}>输入</th><th className={th}>输出</th><th className={th}>耗时</th>{showCost ? <th className={th}>折算</th> : null}</tr></thead>
      <tbody>{rows.map(r => <Fragment key={r.request_id}>
        <tr className="border-t border-border cursor-pointer hover:bg-surface-hover" onClick={() => setOpen(open === r.request_id ? "" : r.request_id)}>
          <td className="py-2 whitespace-nowrap">{when(r.ts)}</td><td className="py-2 max-w-[18rem] truncate" title={r.question}>{r.question || "（未找到原文）"}</td><td className="py-2">{r.user || "—"}</td>
          <td className={td}>{r.turns}</td><td className="py-2 pl-4 max-w-[16rem] truncate" title={count(r.tools)}>{r.tools.length ? count(r.tools) : "—"}</td>
          <td className={td} title={r.cache_read_tokens ? `其中缓存命中 ${tokens(r.cache_read_tokens)}` : undefined}>{tokens(r.prompt_tokens)}</td><td className={td}>{tokens(r.completion_tokens)}</td>
          <td className={td}>{duration(r.seconds)}</td>{showCost ? <td className={td}>{dollars(r.cost_usd)}{r.partial ? "+" : ""}</td> : null}
        </tr>
        {open === r.request_id ? <tr><td colSpan={showCost ? 9 : 8} className="pb-3 overflow-x-auto"><RequestDetail id={r.request_id} showCost={showCost} /></td></tr> : null}
      </Fragment>)}</tbody></table></div>}
    {total > PAGE ? <div className="flex items-center gap-3 text-xs"><button className="btn text-xs" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - PAGE))}>上一页</button>
      <span className="text-text-muted">{offset + 1}–{Math.min(total, offset + PAGE)} / {total}</span>
      <button className="btn text-xs" disabled={offset + PAGE >= total} onClick={() => onPage(offset + PAGE)}>下一页</button></div> : null}
  </section>;
}

export function AssistantAnalytics() {
  const [days, setDays] = useState(30);
  const [offset, setOffset] = useState(0);
  const [showCost, setShowCost] = useState(storedCost);
  const load = useCallback((signal: AbortSignal) => loadAssistantUsage(days, offset, signal), [days, offset]);
  const { data, error, loading, reload } = usePageResource(load, 15000);
  const toggleCost = (value: boolean) => { setShowCost(value); try { localStorage.setItem(COST_KEY, value ? "1" : "0"); } catch { /* per-viewer convenience only */ } };
  const totals = data?.totals;
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-3">
      <select aria-label="助手统计范围" className="input text-xs w-auto" value={days} onChange={e => { setDays(Number(e.target.value)); setOffset(0); }}>{RANGES.map(r => <option key={r.days} value={r.days}>{r.label}</option>)}</select>
      <label className="text-xs flex items-center gap-2"><input type="checkbox" checked={showCost} onChange={e => toggleCost(e.target.checked)} />显示折算金额</label>
      <button className="btn text-xs ml-auto" disabled={loading} onClick={() => void reload()}><RefreshCw className="w-3.5 h-3.5" />刷新</button>
    </div>
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    {data && !data.enabled ? <ErrorState empty="用量记录尚未启用" compact /> : null}
    {totals ? <>
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <Card label="对话请求" value={totals.requests.toLocaleString("zh-CN")} note={`共 ${totals.turns.toLocaleString("zh-CN")} 轮模型调用`} />
        <Card label="每个请求平均轮数" value={totals.turns_per_request ?? "—"} note={`调用工具 ${totals.tool_calls.toLocaleString("zh-CN")} 次`} />
        <Card label="输入 token" value={tokens(totals.prompt_tokens)} note={`缓存命中 ${percent(totals.cache_rate)}`} />
        <Card label="输出 token" value={tokens(totals.completion_tokens)} />
        <Card label="工具定义占上下文" value={percent(totals.tool_schema_share)} note="每一轮都要把工具列表发给模型" />
        {showCost ? <Card label="折算金额" value={`${dollars(totals.cost_usd)}${totals.partial ? "+" : ""}`} note={totals.partial ? "含未定价调用，金额偏低" : undefined} /> : null}
      </div>
      {totals.earlier_calls ? <p className="text-xs text-text-muted">另有 {totals.earlier_calls.toLocaleString("zh-CN")} 次更早的助手调用（输入 {tokens(totals.earlier_prompt_tokens)}、输出 {tokens(totals.earlier_completion_tokens)}）没有按请求和轮次记录，只计入「用量与耗时」，不在下面的表里。</p> : null}
      <section className="card p-4 space-y-3">
        <div><h2 className="text-sm font-medium">按轮次</h2><p className="text-xs text-text-muted mt-1">每个请求的第 N 轮平均发了多少给模型。工具返回的内容会留在上下文里带进后面每一轮，输入 token 随轮次涨得越快，越值得精简。</p></div>
        {data.turns?.length ? <div className="overflow-x-auto"><table className="w-full text-xs text-left">
          <thead className="text-text-muted"><tr><th className="pb-2 font-normal">轮次</th><th className={th}>到这一轮的请求</th><th className={th}>平均输入 token</th><th className={th}>平均输出 token</th><th className={th}>对话内容（字符）</th><th className={th}>工具定义（字符）</th><th className={th}>折叠的旧结果</th></tr></thead>
          <tbody>{data.turns.map(t => <tr key={t.turn} className="border-t border-border"><td className="py-2">第 {t.turn + 1} 轮</td><td className={td}>{t.requests}</td>
            <td className={td}>{t.prompt_tokens_avg == null ? "—" : tokens(t.prompt_tokens_avg)}</td><td className={td}>{t.completion_tokens_avg == null ? "—" : tokens(t.completion_tokens_avg)}</td>
            <td className={td}>{chars(t.message_chars_avg)}</td><td className={td} title={`平均 ${t.tool_count_avg} 个工具`}>{chars(t.tool_schema_chars_avg)}</td><td className={td}>{t.retired}</td></tr>)}</tbody></table></div> : <ErrorState empty="暂无记录" compact />}
      </section>
      <section className="card p-4 space-y-3">
        <div><h2 className="text-sm font-medium">按工具</h2><p className="text-xs text-text-muted mt-1">助手调用过的工具，按返回内容的总量排序。返回得大又调用得多的，是最值得瘦身的。</p></div>
        {data.tools?.length ? <div className="overflow-x-auto"><table className="w-full text-xs text-left">
          <thead className="text-text-muted"><tr><th className="pb-2 font-normal">工具</th><th className={th}>调用次数</th><th className={th}>涉及请求</th><th className={th}>平均返回（字符）</th><th className={th}>最大返回</th><th className={th}>合计返回</th></tr></thead>
          <tbody>{data.tools.map(t => <tr key={t.name} className="border-t border-border"><td className="py-2">{t.name}</td><td className={td}>{t.calls}</td><td className={td}>{t.requests}</td>
            <td className={td}>{chars(t.avg_chars)}</td><td className={td}>{chars(t.max_chars)}</td><td className={td}>{chars(t.total_chars)}</td></tr>)}</tbody></table></div> : <ErrorState empty="范围内助手还没有调用过工具" compact />}
      </section>
      <Requests rows={data.requests || []} total={data.request_total || 0} offset={offset} onPage={setOffset} showCost={showCost} />
    </> : null}
    {!data && !error ? <p className="text-sm text-text-muted">正在读取…</p> : null}
  </div>;
}

function Card({ label, value, note }: { label: string; value: React.ReactNode; note?: string }) {
  return <div className="card p-3"><p className="text-xs text-text-muted">{label}</p><p className="text-lg font-semibold tabular-nums mt-1">{value}</p>{note ? <p className="text-xs text-text-muted mt-1">{note}</p> : null}</div>;
}
