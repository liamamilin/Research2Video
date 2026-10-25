import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { listSources, type SourceRow } from "@/api/sources";
import { ErrorState } from "@/components/ErrorState";
import { TimeStamp } from "@/components/TimeStamp";
import { RefreshCw } from "lucide-react";
import { usePageResource } from "@/lib/use-page-resource";
import { cn } from "@/lib/utils";
import { SourceEditor } from '@/components/SourceEditor';
import { useAuthStore } from '@/lib/auth-store';

type Filter = "all" | "used" | "unused" | "failing";

/** 信息源：每个源只登记一次，各矩阵的 Job 引用它；这里看谁在用、最近一次抓取怎么样。 */
export function SourcesPage() {
  const { data: catalog, error, loading, reload: load } = usePageResource(listSources);
  const [filter, setFilter] = useState<Filter>("all");
  const [tag, setTag] = useState("");
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const role = useAuthStore(s => s.user?.role);
  const editable = role === 'admin' || role === 'editor';

  const tags = useMemo(() => Array.from(new Set((catalog?.sources ?? []).flatMap((s) => s.tags))).sort(), [catalog]);
  const shown = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return (catalog?.sources ?? []).filter((s) =>
      (filter === "all" || (filter === "used" && s.used_by.length > 0) || (filter === "unused" && s.used_by.length === 0)
        || (filter === "failing" && Boolean(s.last?.error))) &&
      (!tag || s.tags.includes(tag)) &&
      (!needle || `${s.name} ${s.id} ${s.spec} ${s.note}`.toLowerCase().includes(needle)));
  }, [catalog, filter, tag, text]);

  if (!catalog) return <ErrorState error={error} onRetry={load} empty="加载中…" />;

  const failing = catalog.sources.filter((s) => s.last?.error).length;
  const FILTERS: [Filter, string][] = [["all", `全部 ${catalog.sources.length}`], ["used", "有 Job 在用"], ["unused", "还没人用"], ["failing", `最近抓取失败 ${failing}`]];

  return (
    <div className="space-y-4">
      {error ? <ErrorState error={error} onRetry={load} compact /> : null}
      <div className="flex flex-wrap items-start gap-3"><div className="flex-1">
        <h1 className="text-xl font-semibold tracking-tight">信息源</h1>
        <p className="text-xs text-text-muted mt-1">
          查看共享信息源、引用它的研究任务，以及最近一次抓取结果。
        </p></div>{editable && catalog.revision && <button className="btn-primary text-xs" onClick={() => setEditing('')}>新增信息源</button>}
        <button className="btn text-xs" disabled={loading} onClick={load}><RefreshCw className="h-3.5 w-3.5" />刷新</button>
      </div>
      <details className="text-xs text-text-muted"><summary className="cursor-pointer">登记与引用信息源</summary><p className="mt-2">在此页或通过助手登记来源，研究任务使用 source:id 引用一个来源，或使用 tag:标签 引用一组。删除前会检查引用任务。</p></details>
      {editing !== null && catalog.revision && <SourceEditor key={editing} id={editing || undefined} revision={catalog.revision} onClose={() => setEditing(null)} onSaved={load} />}

      {catalog.problems.length ? (
        <section className="card p-4 space-y-1 border-danger" aria-label="引用错误">
          <h2 className="text-sm font-medium text-danger">有 Job 引用了注册表里没有的源（这些 Job 跑不起来）</h2>
          <ul className="text-xs space-y-1">
            {catalog.problems.map((p) => (
              <li key={p.job}><Link className="hover:underline" to={`/jobs/${encodeURIComponent(p.job)}`}>{p.job}</Link>：{p.problem}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map(([key, label]) => (
          <button key={key} onClick={() => setFilter(key)}
            className={cn("badge border cursor-pointer", filter === key ? "bg-accent-solid text-white border-accent" : "bg-bg-card border-border")}>
            {label}
          </button>
        ))}
        <select className="input text-xs py-1 w-auto" aria-label="标签" value={tag} onChange={(e) => setTag(e.target.value)}>
          <option value="">全部标签</option>
          {tags.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <input className="input text-xs py-1 w-48" placeholder="搜索名称、网址、备注" aria-label="搜索信息源" value={text} onChange={(e) => setText(e.target.value)} />
        <span className="ml-auto text-xs text-text-muted">显示 {shown.length} / {catalog.sources.length}</span>
      </div>

      <div className="card overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead className="text-xs text-text-muted">
            <tr className="text-left">
              <th className="font-normal px-4 py-2">源</th>
              <th className="font-normal px-4 py-2">标签</th>
              <th className="font-normal px-4 py-2">谁在用</th>
              <th className="font-normal px-4 py-2">最近一次抓取</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {shown.map((s) => <Row key={s.id || s.spec} s={s} onEdit={editable && s.id ? () => setEditing(s.id) : undefined} />)}
          </tbody>
        </table>
        {!shown.length ? <ErrorState empty="没有符合的信息源" compact /> : null}
      </div>
    </div>
  );
}

function Row({ s, onEdit }: { s: SourceRow; onEdit?: () => void }) {
  return (
    <tr className="align-top">
      <td className="px-4 py-2 min-w-[14rem]">
        <div>{s.name}{s.id ? null : <span className="ml-2 text-xs text-warning">没登记</span>}</div>
        <div className="text-xs text-text-muted font-mono break-all">{s.id ? `${s.id} · ` : ""}{s.spec}</div>
        {s.note ? <div className="text-xs text-text-muted mt-1">{s.note}</div> : null}
        {onEdit && <button className="text-xs text-accent mt-1" onClick={onEdit}>修改来源</button>}
      </td>
      <td className="px-4 py-2 text-xs text-text-muted">{s.tags.join("、") || "—"}</td>
      <td className="px-4 py-2 text-xs">
        {s.used_by.length ? s.used_by.map((u) => (
          <div key={u.job}><Link className="hover:underline" to={`/jobs/${encodeURIComponent(u.job)}`}>{u.name}</Link></div>
        )) : <span className="text-text-muted">还没人用</span>}
      </td>
      <td className="px-4 py-2 text-xs">
        {s.last ? (
          s.last.error ? <span className="text-danger">{s.last.error} <TimeStamp value={s.last.at} /></span>
            : <span>{s.last.items} 条 · <TimeStamp value={s.last.at} /></span>
        ) : <span className="text-text-muted">{s.used_by.length ? "还没跑过" : "—"}</span>}
      </td>
    </tr>
  );
}
