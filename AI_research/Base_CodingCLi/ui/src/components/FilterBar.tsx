import { useSearchParams } from "react-router-dom";

export interface Facet {
  key: string;
  label: string;
  options: { value: string; label: string }[];
}

/** Date-range facet shared by every list: it filters on the page's own "when was this made" field. */
export const RANGE_FACET: Facet = {
  key: "range",
  label: "时间",
  options: [
    { value: "today", label: "今天" },
    { value: "7d", label: "近 7 天" },
    { value: "30d", label: "近 30 天" },
  ],
};

const RANGE_MS: Record<string, number> = { today: 86400000, "7d": 7 * 86400000, "30d": 30 * 86400000 };

export function inRange(iso: string | null | undefined, range: string): boolean {
  if (!range || !(range in RANGE_MS)) return true;
  if (!iso) return false;
  const then = new Date(iso.replace(/([+-]\d{2})(\d{2})$/, "$1:$2")).getTime();
  if (Number.isNaN(then)) return true;
  if (range === "today") {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    return then >= start.getTime();
  }
  return Date.now() - then <= RANGE_MS[range];
}

export function matchesText(q: string, ...fields: (string | null | undefined)[]): boolean {
  const needle = q.trim().toLowerCase();
  return !needle || fields.some((f) => (f ?? "").toLowerCase().includes(needle));
}

/**
 * Filters kept in the URL (``?status=DRAFT&tier=serious&q=…``), so a filtered list can be linked to and survives a
 * reload. The page filters its own rows with the values this returns; the bar only edits them.
 */
export function useFilters(keys: string[]): { values: Record<string, string>; set: (key: string, value: string) => void; clear: () => void; active: boolean } {
  const [params, setParams] = useSearchParams();
  const values = Object.fromEntries([...keys, "q"].map((k) => [k, params.get(k) ?? ""]));
  const set = (key: string, value: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    }, { replace: true });
  const clear = () =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      [...keys, "q"].forEach((k) => next.delete(k));
      return next;
    }, { replace: true });
  return { values, set, clear, active: Object.values(values).some(Boolean) };
}

export function FilterBar({ facets, filters, shown, total, placeholder = "搜索标题" }: {
  facets: Facet[];
  filters: ReturnType<typeof useFilters>;
  shown: number;
  total: number;
  placeholder?: string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" aria-label="筛选">
      <input
        className="input text-xs py-1 w-40 max-w-full"
        type="search"
        aria-label="搜索"
        placeholder={placeholder}
        value={filters.values.q}
        onChange={(e) => filters.set("q", e.target.value)}
      />
      {facets.map((f) => (
        <select
          key={f.key}
          aria-label={f.label}
          className="input text-xs py-1 w-auto max-w-full"
          value={filters.values[f.key] ?? ""}
          onChange={(e) => filters.set(f.key, e.target.value)}
        >
          <option value="">{f.label}：全部</option>
          {f.options.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      ))}
      <span className="text-text-muted">显示 {shown} / {total}</span>
      {filters.active ? (
        <button className="text-accent hover:underline" onClick={filters.clear}>清除筛选</button>
      ) : null}
    </div>
  );
}
