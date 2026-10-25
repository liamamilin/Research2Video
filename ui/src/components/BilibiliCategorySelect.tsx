import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronDown, Search, X } from "lucide-react";
import { type BilibiliCategory, type BilibiliCategoryCatalog } from "@/api/platforms";
import { recommendBilibiliCategories, searchBilibiliCategories, type CategoryContent } from "@/lib/bilibili-categories";
import { cn } from "@/lib/utils";

type CategoryResource = {
  data: BilibiliCategoryCatalog | null;
  error: string;
  loading: boolean;
  reload: () => Promise<void>;
};

const EMPTY: BilibiliCategory[] = [];

export function BilibiliCategorySelect({ label, value, onChange, resource, required = false, content }: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  resource: CategoryResource;
  required?: boolean;
  content?: CategoryContent;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const categories = resource.data?.categories || EMPTY;
  const selected = categories.find(row => row.tid === value);
  const matches = useMemo(() => searchBilibiliCategories(categories, query), [categories, query]);
  const recommendations = useMemo(() => content ? recommendBilibiliCategories(categories, content) : [], [categories, content]);
  const highlighted = Math.min(active, matches.length - 1);
  useEffect(() => {
    if (open) list.current?.querySelector<HTMLElement>(`[data-index="${highlighted}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [open, highlighted, matches]);
  function expand() { setQuery(""); setActive(0); setOpen(true); }
  function choose(tid: number) { onChange(tid); setOpen(false); setQuery(""); }
  return <div className="min-w-0 space-y-2" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <label htmlFor={id} className="block text-xs">{label}</label>
    <div className="relative">
      <Search className="absolute left-3 top-3 w-3.5 h-3.5 text-text-muted pointer-events-none" />
      <input ref={input} id={id} role="combobox" aria-autocomplete="list" aria-expanded={open} aria-controls={`${id}-list`} aria-required={required}
        aria-activedescendant={open && matches[highlighted] ? `${id}-${matches[highlighted].tid}` : undefined}
        className="input w-full pl-8 pr-16 text-xs" placeholder={resource.loading && !categories.length ? "正在读取分区…" : "搜索名称或关键词，如科普、AI、历史"}
        value={open ? query : selected?.path || ""} disabled={!categories.length} autoComplete="off"
        onFocus={expand} onClick={() => { if (!open) expand(); }}
        onChange={event => { setQuery(event.target.value); setActive(0); setOpen(true); }}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) expand();
            else setActive(index => matches.length ? (index + (event.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length : 0);
          } else if (event.key === "Enter") {
            event.preventDefault();
            if (open && matches[highlighted]) choose(matches[highlighted].tid);
            else if (!open) expand();
          } else if (event.key === "Escape") { event.preventDefault(); setOpen(false); }
        }} />
      <div className="absolute right-2 top-2 flex gap-1">
        {selected ? <button type="button" aria-label={`清除${label}`} className="p-1 text-text-muted hover:text-text" onMouseDown={event => event.preventDefault()} onClick={() => { onChange(0); setQuery(""); input.current?.focus(); }}><X className="w-3.5 h-3.5" /></button> : null}
        <button type="button" aria-label={`展开${label}列表`} className="p-1 text-text-muted hover:text-text" disabled={!categories.length} onMouseDown={event => event.preventDefault()} onClick={() => { if (open) setOpen(false); else { input.current?.focus(); expand(); } }}><ChevronDown className="w-3.5 h-3.5" /></button>
      </div>
      {open ? <div className="absolute z-30 left-0 right-0 top-full mt-1 rounded-lg border border-border bg-bg-card shadow-xl">
        <p className="px-3 py-2 text-xs text-text-muted border-b border-border" role="status">{query.trim() ? `找到 ${matches.length} 个分区` : `全部 ${categories.length} 个分区`}{selected ? ` · 已选 ${selected.path}` : ""}</p>
        <div ref={list} id={`${id}-list`} role="listbox" aria-label={`${label}候选`} className="max-h-64 overflow-y-auto">
          {matches.map((row, index) => <button type="button" role="option" aria-selected={row.tid === value} id={`${id}-${row.tid}`} key={row.tid} data-index={index}
            className={cn("w-full px-3 py-2 text-left text-xs block", index === highlighted ? "bg-accent/10 text-accent" : "hover:bg-bg-hover", row.tid === value && "font-medium")}
            onMouseDown={event => event.preventDefault()} onMouseEnter={() => setActive(index)} onClick={() => choose(row.tid)}>
            <span className="block">{row.path}{row.tid === value ? " · 已选" : ""}</span><span className="block text-text-muted text-[11px] mt-0.5 line-clamp-2">{row.description}</span>
          </button>)}
          {!matches.length ? <p className="px-3 py-4 text-xs text-text-muted">没有匹配的分区，试试其他关键词。当前选择会保留。</p> : null}
        </div>
      </div> : null}
    </div>
    {selected?.description ? <p className="text-xs text-text-muted">{selected.description}</p> : null}
    {content && categories.length ? recommendations.length ? <div className="rounded border border-accent/20 bg-accent/5 p-2 space-y-1">
      <p className="text-xs font-medium">推荐分区</p>
      <button type="button" className="text-xs text-accent hover:underline text-left disabled:no-underline" disabled={recommendations[0].category.tid === value} onClick={() => choose(recommendations[0].category.tid)}>{recommendations[0].category.tid === value ? "已选推荐：" : "采用推荐："}{recommendations[0].category.path}</button>
      <p className="text-xs text-text-muted">{recommendations[0].reason}</p>
      {recommendations.length > 1 ? <details className="text-xs"><summary className="text-text-muted cursor-pointer">其他建议</summary><div className="mt-1 space-y-2">{recommendations.slice(1).map(row => <div key={row.category.tid}><button type="button" className="text-accent hover:underline text-left" onClick={() => choose(row.category.tid)}>采用：{row.category.path}</button><p className="text-text-muted">{row.reason}</p></div>)}</div></details> : null}
    </div> : <p className="text-xs text-text-muted">暂未找到明确推荐，可搜索并手动选择分区。</p> : null}
    {value > 0 && resource.data && !selected ? <p className="text-xs text-warning">原分区已不在当前可用列表中，请重新选择。</p> : null}
    {resource.error ? <p role="alert" className="text-xs text-warning">分区读取失败：{resource.error}</p> : null}
    {resource.data?.stale ? <p className="text-xs text-warning">分区更新失败，暂显示上次获取的分区。</p> : null}
    {resource.error || resource.data?.stale ? <button type="button" className="btn text-xs" disabled={resource.loading} onClick={() => void resource.reload()}>重新获取分区</button> : null}
  </div>;
}
