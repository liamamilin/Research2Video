import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { listSources, type SourceCatalog, type SourceRow } from "@/api/sources";
import { ErrorState } from "@/components/ErrorState";
import { TimeStamp } from "@/components/TimeStamp";
import { errorMessage } from "@/lib/toast";
import { cn } from "@/lib/utils";

type Filter = "all" | "used" | "unused" | "failing";

/** 信息源：每个源只登记一次，各矩阵的 Job 引用它；这里看谁在用、最近一次抓取怎么样。 */
export function SourcesPage() {
  const [catalog, setCatalog] = useState<SourceCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [tag, setTag] = useState("");
  const [text, setText] = useState("");

  const load = useCallback(() => {
    listSources()
      .then((body) => (setCatalog(body), setError(null)))
      .catch((e) => setError(errorMessage(e, "读取信息源失败")));
  }, []);
  useEffect(load, [load]);

  const tags = useMemo(() => Array.from(new Set((catalog?.sources ?? []).flatMap((s) => s.tags))).sort(), [catalog]);
  const shown = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return (catalog?.sources ?? []).filter((s) =>
      (filter === "all" || (filter === "used" && s.used_by.length > 0) || (filter === "unused" && s.used_by.length === 0)
        || (filter === "failing" && Boolean(s.last?.error))) &&
      (!tag || s.tags.includes(tag)) &&
      (!needle || `${s.name} ${s.id} ${s.spec} ${s.note}`.toLowerCase().includes(needle)));
  }, [catalog, filter, tag, text]);

  if (error) return <ErrorState error={error} onRetry={load} />;
  if (!catalog) return <ErrorState empty="加载中…" />;

  const failing = catalog.sources.filter((s) => s.last?.error).length;
  const FILTERS: [Filter, string][] = [["all", `全部 ${catalog.sources.length}`], ["used", "有 Job 在用"], ["unused", "还没人用"], ["failing", `最近抓取失败 ${failing}`]];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">信息源</h1>
        <p className="text-xs text-text-muted mt-1">
          每个源只在 config/sources.yaml 里登记一次；Job 的 sources 里写 <code>source:id</code> 引用一个，写 <code>tag:标签</code> 引用一类。
          一个源可以被多个矩阵的 Job 共用。
        </p>
      </div>

      {catalog.problems.length ? (
        <section className="card space-y-1 border-danger" aria-label="引用错误">
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
        <input className="input text-xs py-1 w-48" placeholder="搜索名称、网址、备注" value={text} onChange={(e) => setText(e.target.value)} />
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
            {shown.map((s) => <Row key={s.spec} s={s} />)}
          </tbody>
        </table>
        {!shown.length ? <ErrorState empty="没有符合的信息源" compact /> : null}
      </div>
    </div>
  );
}

function Row({ s }: { s: SourceRow }) {
  return (
    <tr className="align-top">
      <td className="px-4 py-2 min-w-[14rem]">
        <div>{s.name}{s.id ? null : <span className="ml-2 text-xs text-warning">没登记</span>}</div>
        <div className="text-xs text-text-muted font-mono break-all">{s.id ? `${s.id} · ` : ""}{s.spec}</div>
        {s.note ? <div className="text-xs text-text-muted mt-1">{s.note}</div> : null}
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
