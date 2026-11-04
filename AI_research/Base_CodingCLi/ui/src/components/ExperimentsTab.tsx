import { loadExperiments, type ExperimentRow } from "@/api/experiments";
import { usePageResource } from "@/lib/use-page-resource";
import { ErrorState } from "./ErrorState";

const STATUS: Record<ExperimentRow["status"], string> = { draft: "草稿", running: "进行中", paused: "已暂停", ended: "已结束", decided: "已处理" };
const TIMING = { before_writing: "写稿前", from_writing: "写稿起" } as const;
const REACH = { all: "全部", with_column: "要有内容栏目" } as const;
const STATE = { existing: "已有", planned: "要新增", unconfirmed: "待确认" } as const;
const th = "pb-2 font-normal whitespace-nowrap";

/** Phase 1 of spec/ab-testing.md: read-only. Creating and starting experiments comes with the creation hook. */
export function ExperimentsTab() {
  const { data, error, reload } = usePageResource(loadExperiments);
  if (error) return <ErrorState error={error} onRetry={reload} compact />;
  if (!data) return <p className="text-sm text-text-muted">正在读取…</p>;
  return <div className="space-y-4">
    <section className="card p-4 text-sm flex flex-wrap gap-x-6 gap-y-1">
      <span>总开关：<strong className={data.enabled ? "text-success" : "text-text-muted"}>{data.enabled ? "开" : "关"}</strong></span>
      <span className="text-text-muted">关着时所有稿件都按现状（A）生产。新建和开始实验在下一期提供。</span>
    </section>
    <section className="card p-4 space-y-3">
      <h2 className="text-sm font-medium">实验</h2>
      {data.experiments.length === 0 ? <p className="text-xs text-text-muted">还没有实验。</p> :
        <div className="overflow-x-auto"><table className="w-full text-xs text-left">
          <thead className="text-text-muted"><tr><th className={th}>名称</th><th className={th}>类型</th><th className={th}>状态</th><th className={th}>B 组比例</th><th className={th}>主要指标</th><th className={th}>终点</th></tr></thead>
          <tbody>{data.experiments.map(e => <tr key={e.id} className="border-t border-border">
            <td className="py-2">{e.definition.name}</td><td className="py-2">{e.definition.kind === "aa" ? "A/A 检查" : "对比实验"}</td>
            <td className="py-2">{STATUS[e.status]}</td><td className="py-2 tabular-nums">{e.definition.share_b}%</td>
            <td className="py-2">{e.definition.metric.primary}</td>
            <td className="py-2 tabular-nums">每组 {e.definition.stop.per_arm} 条 / {e.definition.stop.max_days} 天</td></tr>)}</tbody></table></div>}
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
