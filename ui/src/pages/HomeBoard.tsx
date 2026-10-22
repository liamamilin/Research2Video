import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getVideoBoard, listVideoCells, type BoardAttention, type VideoBoard, type VideoCell } from "@/api/video";
import { AlertsCard } from "@/components/AlertsCard";
import { AutopilotCard } from "@/components/AutopilotCard";
import { CredibilityBadge, TIER_LABEL } from "@/components/CredibilityBadge";
import { FilterBar, inRange, matchesText, RANGE_FACET, useFilters, type Facet } from "@/components/FilterBar";
import { TimeStamp } from "@/components/TimeStamp";
import { cn } from "@/lib/utils";

type Kind = BoardAttention["kind"];

// 排在前面的先处理：失败 > 方案 > 审校 > 复核 > 待发布 > 待成片
const KIND: Record<Kind, { label: string; cls: string; bar: string }> = {
  failed: { label: "失败", cls: "border-danger text-danger", bar: "bg-danger" },
  plan: { label: "待确认方案", cls: "border-warning text-warning", bar: "bg-warning" },
  review: { label: "待审校", cls: "border-warning text-warning", bar: "bg-warning" },
  recheck: { label: "待复核", cls: "border-warning text-warning", bar: "bg-warning" },
  render_done: { label: "待发布", cls: "border-success text-success", bar: "bg-success" },
  render: { label: "待成片", cls: "border-border text-text-muted", bar: "bg-border" },
};
const KIND_ORDER = Object.keys(KIND) as Kind[];
const PAGE = 15;

const STAGE_LINK: Record<string, string> = {
  researching: "/video?status=RESEARCHING",
  draft: "/video?status=DRAFT",
  approved: "/publish",
  rendering: "/publish",
  rendered: "/publish",
  published: "/video?status=published",
};

interface Row {
  script_id: string;
  title: string;
  column_key: string;
  created_at?: string;
  credibility?: BoardAttention["credibility"];
  kinds: Kind[]; // 同一篇文稿可能同时待发布和待复核：合成一行
  notes: string[];
}

/** 一篇文稿一行：它要处理的事都标在这一行上 */
function rowsOf(attention: BoardAttention[]): Row[] {
  const byScript = new Map<string, Row>();
  for (const a of attention) {
    const row = byScript.get(a.script_id);
    if (!row) {
      byScript.set(a.script_id, {
        script_id: a.script_id, title: a.title, column_key: a.column_key, created_at: a.created_at,
        credibility: a.credibility, kinds: [a.kind], notes: [a.text],
      });
    } else {
      if (!row.kinds.includes(a.kind)) row.kinds.push(a.kind);
      if (a.text && !row.notes.includes(a.text)) row.notes.push(a.text);
      row.credibility = row.credibility ?? a.credibility;
    }
  }
  const rank = (r: Row) => Math.min(...r.kinds.map((k) => KIND_ORDER.indexOf(k)));
  return [...byScript.values()]
    .map((r) => ({ ...r, kinds: [...r.kinds].sort((a, b) => KIND_ORDER.indexOf(a) - KIND_ORDER.indexOf(b)) }))
    .sort((a, b) => rank(a) - rank(b) || (b.created_at ?? "").localeCompare(a.created_at ?? ""));
}

export function HomeBoardPage() {
  const [board, setBoard] = useState<VideoBoard | null>(null);
  const [cells, setCells] = useState<VideoCell[]>([]);
  const [error, setError] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const filters = useFilters(["kind", "column", "tier", "range"]);
  const { values: f } = filters;

  useEffect(() => {
    let alive = true;
    const load = () =>
      getVideoBoard()
        .then((b) => alive && (setBoard(b), setError("")))
        .catch((e) => alive && setError(e instanceof Error ? e.message : "读取失败"));
    load();
    const timer = window.setInterval(load, 15000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    listVideoCells().then((c) => setCells(Array.isArray(c) ? c : [])).catch(() => undefined);
  }, []);

  const columnLabel = (key: string) => cells.find((c) => c.key === key)?.label?.split(" · ").slice(0, 2).join(" · ") ?? key;
  const rows = useMemo(() => rowsOf(board?.attention ?? []), [board]);
  const counts = useMemo(() => {
    const n = Object.fromEntries(KIND_ORDER.map((k) => [k, 0])) as Record<Kind, number>;
    for (const r of rows) for (const k of r.kinds) n[k] += 1;
    return n;
  }, [rows]);
  const shown = rows.filter(
    (r) =>
      (!f.kind || r.kinds.includes(f.kind as Kind)) &&
      (!f.column || r.column_key === f.column) &&
      (!f.tier || r.credibility?.tier === f.tier) &&
      inRange(r.created_at, f.range) &&
      matchesText(f.q, r.title),
  );
  const facets: Facet[] = [
    { key: "column", label: "栏目", options: Array.from(new Set(rows.map((r) => r.column_key))).map((key) => ({ value: key, label: columnLabel(key) })) },
    { key: "tier", label: "可信度", options: (Object.keys(TIER_LABEL) as (keyof typeof TIER_LABEL)[]).map((k) => ({ value: k, label: TIER_LABEL[k] })) },
    RANGE_FACET,
  ];
  // 筛选一变就回到第一页
  useEffect(() => setLimit(PAGE), [f.kind, f.column, f.tier, f.range, f.q]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold">视频看板</h1>
          {board ? (
            <p className="text-xs text-text-muted">
              待发布 {counts.render_done} · 待成片 {counts.render} · 待复核/审校 {counts.recheck + counts.review + counts.plan}
              {board.failed ? ` · 失败 ${board.failed}` : ""}
            </p>
          ) : null}
        </div>
        <Link to="/topics" className="btn text-sm">选题</Link>
        <Link to="/video" className="btn text-sm">全部文稿</Link>
      </div>

      <AlertsCard />

      {error ? <p className="text-danger text-sm">{error}</p> : null}
      {!board && !error ? <p className="text-text-muted text-sm">读取中…</p> : null}

      {board ? (
        <>
          <section aria-label="流水线阶段" className="grid grid-cols-3 sm:grid-cols-6 gap-2">
            {board.stages.map((stage) => (
              <Link key={stage.key} to={STAGE_LINK[stage.key] ?? "/video"} className="card text-center py-2 hover:bg-bg-hover">
                <div className="text-xl font-semibold">{stage.count}</div>
                <div className="text-xs text-text-muted">{stage.label}</div>
              </Link>
            ))}
          </section>
          {board.failed ? (
            <p className="text-sm text-danger">另有 {board.failed} 篇失败，列在下面。</p>
          ) : null}

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem] items-start">
            <section className="card space-y-3 min-w-0" aria-label="需要你处理">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-medium text-sm mr-2">需要你处理</h2>
                {([["", "全部", rows.length], ...KIND_ORDER.map((k) => [k, KIND[k].label, counts[k]] as const)] as const)
                  .filter(([k, , n]) => k === "" || n > 0 || f.kind === k)
                  .map(([k, label, n]) => (
                    <button
                      key={k}
                      aria-pressed={f.kind === k}
                      onClick={() => filters.set("kind", k)}
                      className={cn(
                        "badge border cursor-pointer",
                        f.kind === k ? "bg-accent-solid text-white border-accent" : "bg-bg-card border-border hover:bg-bg-hover",
                      )}
                    >
                      {label} {n}
                    </button>
                  ))}
              </div>
              <FilterBar facets={facets} filters={filters} shown={shown.length} total={rows.length} />
              {rows.length === 0 ? (
                <p className="text-sm text-text-muted">现在没有要处理的事。草稿不需要你审：流水线会自动定稿、出片，每条的可信度标在旁边，你只需要看片和发布。</p>
              ) : shown.length === 0 ? (
                <p className="text-sm text-text-muted">没有符合筛选的文稿。</p>
              ) : (
                <ul className="divide-y divide-border">
                  {shown.slice(0, limit).map((r) => (
                    <li key={r.script_id}>
                      <Link to={`/video/${r.script_id}`} className="flex gap-3 py-2 hover:bg-bg-hover rounded">
                        <span className={cn("w-1 rounded shrink-0", KIND[r.kinds[0]].bar)} aria-hidden />
                        <span className="min-w-0 flex-1 space-y-1">
                          <span className="flex flex-wrap items-center gap-2">
                            {r.kinds.map((k) => (
                              <span key={k} className={cn("badge border", KIND[k].cls)}>{KIND[k].label}</span>
                            ))}
                            <span className="text-sm font-medium">{r.title || r.script_id.slice(0, 8)}</span>
                          </span>
                          <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
                            <span>{columnLabel(r.column_key)}</span>
                            <CredibilityBadge value={r.credibility} />
                            <span>{r.notes.join(" · ")}</span>
                            <TimeStamp value={r.created_at} label="创建" />
                          </span>
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
              {shown.length > limit ? (
                <button className="btn text-xs" onClick={() => setLimit(limit + PAGE)}>再显示 {Math.min(PAGE, shown.length - limit)} 条（还有 {shown.length - limit} 条）</button>
              ) : null}
            </section>

            <div className="min-w-0 lg:sticky lg:top-4">
              <AutopilotCard />
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
