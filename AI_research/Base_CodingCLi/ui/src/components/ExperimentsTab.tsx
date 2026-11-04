import { Fragment, useCallback, useState } from "react";
import { createExperiment, loadExperiment, loadExperiments, moveExperiment, setExperimentsEnabled,
  type Definition, type ExperimentDetail, type ExperimentRow, type Move, type SwitchRow } from "@/api/experiments";
import { usePageResource } from "@/lib/use-page-resource";
import { ErrorState } from "./ErrorState";

const STATUS: Record<ExperimentRow["status"], string> = { draft: "草稿", running: "进行中", paused: "已暂停", ended: "已结束", decided: "已处理" };
const TIMING = { before_writing: "写稿前", from_writing: "写稿起" } as const;
const REACH = { all: "全部", with_column: "要有内容栏目" } as const;
const STATE = { existing: "已有", planned: "要新增", unconfirmed: "待确认" } as const;
const METRICS: Record<string, { label: string; better: "higher" | "lower" }> = {
  completion_adjusted: { label: "时长校正后的完播率", better: "higher" }, completion: { label: "完播率", better: "higher" },
  exit3s: { label: "3 秒退出", better: "lower" }, cover_pass: { label: "封面点击超过同类", better: "higher" } };
const ACTIONS: Record<ExperimentRow["status"], [Move, string][]> = {
  draft: [["start", "开始"]], running: [["pause", "暂停"], ["end", "结束"]], paused: [["resume", "继续"], ["end", "结束"]], ended: [], decided: [] };
const th = "pb-2 font-normal whitespace-nowrap";
const list = (text: string) => text.split(/[,，\s]+/).map(s => s.trim()).filter(Boolean);
const message = (e: unknown) => e instanceof Error ? e.message : String(e);

function parseValue(raw: string, sw: SwitchRow | undefined): unknown {
  if (sw?.values.length) return sw.values.find(v => String(v) === raw) ?? raw;
  try { return JSON.parse(raw); } catch { return raw; }
}

function NewExperiment({ registry, onDone }: { registry: SwitchRow[]; onDone: () => void }) {
  const [kind, setKind] = useState<"compare" | "aa">("aa");
  const [id, setId] = useState(""); const [name, setName] = useState("");
  const [path, setPath] = useState(registry[0]?.path ?? ""); const [sub, setSub] = useState(""); const [value, setValue] = useState("");
  const [primary, setPrimary] = useState("completion_adjusted"); const [effect, setEffect] = useState(8);
  const [share, setShare] = useState(50); const [perArm, setPerArm] = useState(20); const [days, setDays] = useState(28);
  const [columns, setColumns] = useState(""); const [matrices, setMatrices] = useState(""); const [mode, setMode] = useState("");
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const sw = registry.find(r => r.path === path);
  const prefix = path.endsWith(".*");
  const submit = async () => {
    const key = prefix ? `${path.slice(0, -2)}.${sub}` : path;
    const definition: Definition = { id, name, kind, share_b: share,
      scope: { columns: list(columns), matrices: list(matrices), content_mode: (mode || null) as "individual" | "episode" | null },
      arms: { A: { name: "现状", overrides: {} }, B: { name: kind === "aa" ? "现状（对照）" : `${sw?.label ?? key}：${value}`, overrides: kind === "aa" ? {} : { [key]: parseValue(value, sw) } } },
      metric: { primary, better: METRICS[primary].better, min_effect: effect }, stop: { per_arm: perArm, max_days: days } };
    setBusy(true); setError("");
    try { await createExperiment(definition); onDone(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  const field = "input text-xs";
  return <section className="card p-4 space-y-3" aria-label="新建实验">
    <h2 className="text-sm font-medium">新建实验</h2>
    <div className="grid gap-3 sm:grid-cols-2 text-xs">
      <label className="space-y-1"><span>类型</span><select className={field} value={kind} onChange={e => { const k = e.target.value as "compare" | "aa"; setKind(k); setPerArm(k === "aa" ? 20 : 45); }}>
        <option value="aa">A/A 检查（两组相同，检查流程）</option><option value="compare">对比实验</option></select></label>
      <label className="space-y-1"><span>标识（小写字母、数字、-、_）</span><input className={field} value={id} onChange={e => setId(e.target.value)} /></label>
      <label className="space-y-1 sm:col-span-2"><span>名称</span><input className={field} value={name} onChange={e => setName(e.target.value)} /></label>
      {kind === "compare" ? <>
        <label className="space-y-1"><span>要改的开关</span><select className={field} value={path} onChange={e => { setPath(e.target.value); setValue(""); }}>
          {registry.map(r => <option key={r.path} value={r.path}>{r.label}（{r.path}）{r.status !== "existing" ? ` · ${STATE[r.status]}` : ""}</option>)}</select></label>
        {prefix ? <label className="space-y-1"><span>子项</span><input className={field} value={sub} onChange={e => setSub(e.target.value)} placeholder="voice_id" /></label> : null}
        <label className="space-y-1"><span>B 组的取值</span>{sw?.values.length ?
          <select className={field} value={value} onChange={e => setValue(e.target.value)}><option value="">选择</option>{sw.values.map(v => <option key={String(v)} value={String(v)}>{String(v)}</option>)}</select> :
          <input className={field} value={value} onChange={e => setValue(e.target.value)} />}</label>
        <label className="space-y-1"><span>主要指标</span><select className={field} value={primary} onChange={e => setPrimary(e.target.value)}>
          {Object.entries(METRICS).map(([k, m]) => <option key={k} value={k}>{m.label}</option>)}</select></label>
        <label className="space-y-1"><span>要测出的差异（个点）</span><input type="number" min={1} className={field} value={effect} onChange={e => setEffect(Number(e.target.value))} /></label>
        <label className="space-y-1"><span>B 组比例</span><select className={field} value={share} onChange={e => setShare(Number(e.target.value))}>
          {[50, 45, 40, 35, 30, 25, 20, 15, 10, 5].map(v => <option key={v} value={v}>{v}%</option>)}</select></label>
      </> : null}
      <label className="space-y-1"><span>终点：每组条数</span><input type="number" min={2} className={field} value={perArm} onChange={e => setPerArm(Number(e.target.value))} /></label>
      <label className="space-y-1"><span>终点：最多天数</span><input type="number" min={1} className={field} value={days} onChange={e => setDays(Number(e.target.value))} /></label>
      <label className="space-y-1"><span>只在这些栏目（逗号分隔，空 = 全部）</span><input className={field} value={columns} onChange={e => setColumns(e.target.value)} /></label>
      <label className="space-y-1"><span>只在这些矩阵（逗号分隔，空 = 全部）</span><input className={field} value={matrices} onChange={e => setMatrices(e.target.value)} /></label>
      <label className="space-y-1"><span>单篇还是整期</span><select className={field} value={mode} onChange={e => setMode(e.target.value)}>
        <option value="">都可以</option><option value="individual">单篇</option><option value="episode">整期节目</option></select></label>
    </div>
    {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
    <div className="flex gap-2"><button className="btn btn-primary text-xs" disabled={busy || !id || !name} onClick={() => void submit()}>保存草稿</button>
      <button className="btn text-xs" onClick={onDone}>取消</button></div>
  </section>;
}

function Details({ id }: { id: string }) {
  const load = useCallback((signal: AbortSignal) => loadExperiment(id, signal), [id]);
  const { data, error } = usePageResource(load);
  if (error) return <p className="text-xs text-danger">{message(error)}</p>;
  if (!data) return <p className="text-xs text-text-muted">正在读取…</p>;
  const d: ExperimentDetail = data;
  return <div className="text-xs space-y-1 py-2">
    <p>已分组：A {d.arms.A} 条，B {d.arms.B} 条</p>
    {d.start_problems.length ? <ul className="text-danger list-disc pl-4">{d.start_problems.map(p => <li key={p}>{p}</li>)}</ul> : null}
    <p className="text-text-muted">记录：{d.events.map(e => `${new Date(e.at * 1000).toLocaleString("zh-CN", { hour12: false })} ${e.kind}`).join("；")}</p>
  </div>;
}

/** spec/ab-testing.md §9. Analysis results arrive with the next phases. */
export function ExperimentsTab() {
  const { data, error, reload } = usePageResource(loadExperiments);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [problem, setProblem] = useState("");
  if (error) return <ErrorState error={error} onRetry={reload} compact />;
  if (!data) return <p className="text-sm text-text-muted">正在读取…</p>;
  const act = async (fn: () => Promise<unknown>) => { setProblem(""); try { await fn(); await reload(); } catch (e) { setProblem(message(e)); } };
  const toggle = () => {
    if (!data.enabled && !window.confirm("打开后，进行中的实验会给新建的自动稿件分组。确定打开？")) return;
    void act(() => setExperimentsEnabled(!data.enabled));
  };
  return <div className="space-y-4">
    <section className="card p-4 text-sm flex flex-wrap items-center gap-x-6 gap-y-2">
      <span>总开关：<strong className={data.enabled ? "text-success" : "text-text-muted"}>{data.enabled ? "开" : "关"}</strong></span>
      <span className="text-text-muted text-xs">关着时所有稿件都按现状（A）生产，进行中的实验也不分组。</span>
      <button className="btn text-xs ml-auto" onClick={toggle}>{data.enabled ? "关闭总开关" : "打开总开关"}</button>
    </section>
    {problem ? <p role="alert" className="text-xs text-danger">{problem}</p> : null}
    {creating ? <NewExperiment registry={data.registry} onDone={() => { setCreating(false); void reload(); }} /> : null}
    <section className="card p-4 space-y-3">
      <div className="flex items-center"><h2 className="text-sm font-medium">实验</h2>
        {!creating ? <button className="btn btn-primary text-xs ml-auto" onClick={() => setCreating(true)}>新建实验</button> : null}</div>
      {data.experiments.length === 0 ? <p className="text-xs text-text-muted">还没有实验。</p> :
        <div className="overflow-x-auto"><table className="w-full text-xs text-left">
          <thead className="text-text-muted"><tr><th className={th}>名称</th><th className={th}>类型</th><th className={th}>状态</th><th className={th}>B 组比例</th><th className={th}>主要指标</th><th className={th}>终点</th><th className={th}></th></tr></thead>
          <tbody>{data.experiments.map(e => <Fragment key={e.id}>
            <tr className="border-t border-border">
              <td className="py-2"><button className="text-accent hover:underline" onClick={() => setOpen(open === e.id ? null : e.id)}>{e.definition.name}</button></td>
              <td className="py-2">{e.definition.kind === "aa" ? "A/A 检查" : "对比实验"}</td>
              <td className="py-2">{STATUS[e.status]}</td><td className="py-2 tabular-nums">{e.definition.share_b}%</td>
              <td className="py-2">{METRICS[e.definition.metric.primary]?.label ?? e.definition.metric.primary}</td>
              <td className="py-2 tabular-nums">每组 {e.definition.stop.per_arm} 条 / {e.definition.stop.max_days} 天</td>
              <td className="py-2 text-right space-x-1 whitespace-nowrap">{ACTIONS[e.status].map(([action, label]) =>
                <button key={action} className="btn text-xs" onClick={() => void act(() => moveExperiment(e.id, action))}>{label}</button>)}</td></tr>
            {open === e.id ? <tr><td colSpan={7}><Details id={e.id} /></td></tr> : null}
          </Fragment>)}</tbody></table></div>}
    </section>
    <section className="card p-4 space-y-3">
      <h2 className="text-sm font-medium">开关登记表</h2>
      <p className="text-xs text-text-muted">B 组只能改这里登记过的开关。</p>
      <div className="overflow-x-auto"><table className="w-full text-xs text-left">
        <thead className="text-text-muted"><tr><th className={th}>开关</th><th className={th}>写入位置</th><th className={th}>读取时机</th><th className={th}>能生效的稿件</th><th className={th}>怎么判断生效</th><th className={th}>现状</th></tr></thead>
        <tbody>{data.registry.map(s => <tr key={s.path} className="border-t border-border align-top">
          <td className="py-2">{s.label}{s.note ? <p className="text-text-muted mt-0.5">{s.note}</p> : null}</td>
          <td className="py-2 font-mono">{s.path}</td><td className="py-2">{TIMING[s.timing]}</td><td className="py-2">{REACH[s.reaches]}</td>
          <td className="py-2">{s.applied_by}</td><td className="py-2">{STATE[s.status]}</td></tr>)}</tbody></table></div>
    </section>
  </div>;
}
