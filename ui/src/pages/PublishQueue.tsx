import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listShipments, listVideoCells, type Shipment, type VideoCell } from "@/api/video";
import { CredibilityBadge, TIER_LABEL } from "@/components/CredibilityBadge";
import { FilterBar, inRange, matchesText, RANGE_FACET, useFilters, type Facet } from "@/components/FilterBar";
import { TimeStamp } from "@/components/TimeStamp";
import { cn } from "@/lib/utils";

const RENDER_NOTE: Record<string, string> = {
  SUBMITTING: "提交中", QUEUED: "排队中", RUNNING: "生成中", DOWNLOADING: "取回中",
  COMPLETED: "成片完成", FAILED: "出片失败", CANCELLED: "已取消",
};

function state(s: Shipment): { label: string; cls: string } {
  if (s.publications.length) return { label: "已发布", cls: "border-success text-success" };
  if (s.render?.status === "COMPLETED") return { label: "待发布", cls: "border-accent text-accent" };
  if (s.render && ["FAILED", "CANCELLED"].includes(s.render.status)) return { label: "出片失败", cls: "border-danger text-danger" };
  if (s.render) return { label: RENDER_NOTE[s.render.status] ?? s.render.status, cls: "border-border text-text-muted" };
  return { label: "待出片", cls: "border-border text-text-muted" };
}

export function PublishQueuePage() {
  const filters = useFilters(["stage", "tier", "column", "source", "range"]);
  const [rows, setRows] = useState<Shipment[] | null>(null);
  const [error, setError] = useState("");
  const [cells, setCells] = useState<VideoCell[]>([]);
  useEffect(() => {
    listVideoCells().then(setCells).catch(() => undefined);
  }, []);
  const cellName = (key: string) => cells.find((c) => c.key === key)?.label?.split(" · ").slice(0, 2).join(" · ") ?? key;

  useEffect(() => {
    let alive = true;
    const load = () =>
      listShipments()
        .then((r) => alive && (setRows(r), setError("")))
        .catch((e) => alive && setError(e instanceof Error ? e.message : "读取失败"));
    load();
    const timer = window.setInterval(load, 20000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const { values: f } = filters;
  const shown = (rows ?? []).filter(
    (s) =>
      (!f.stage || state(s).label === f.stage) &&
      (!f.tier || s.credibility?.tier === f.tier) &&
      (!f.column || s.column_key === f.column) &&
      (!f.source || (f.source === "auto") === Boolean(s.auto)) &&
      inRange(s.render?.completed_at ?? s.created_at, f.range) &&
      matchesText(f.q, s.title),
  );
  const facets: Facet[] = [
    { key: "stage", label: "阶段", options: ["待发布", "已发布", "待出片", "生成中", "出片失败"].map((v) => ({ value: v, label: cellName(v) })) },
    { key: "tier", label: "可信度", options: (Object.keys(TIER_LABEL) as (keyof typeof TIER_LABEL)[]).map((k) => ({ value: k, label: TIER_LABEL[k] })) },
    { key: "column", label: "栏目格子", options: Array.from(new Set((rows ?? []).map((r) => r.column_key))).map((v) => ({ value: v, label: cellName(v) })) },
    { key: "source", label: "来源", options: [{ value: "auto", label: "自动流水线" }, { value: "manual", label: "手动创建" }] },
    RANGE_FACET,
  ];

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">成片与发布</h1>
      <p className="text-xs text-text-muted">所有已定稿的文稿：成片进度、能不能发、发布链接。系统不会替你发布，发布后到文稿页「发布」里记录链接。</p>
      {error ? <p className="text-danger text-sm">{error}</p> : null}
      {rows && rows.length === 0 ? <p className="text-sm text-text-muted">还没有定稿的文稿。</p> : null}
      {rows && rows.length ? <FilterBar facets={facets} filters={filters} shown={shown.length} total={rows.length} /> : null}
      <ul className="space-y-2">
        {shown.map((s) => {
          const st = state(s);
          return (
            <li key={s.script_id} className="card space-y-2">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className={cn("badge border", st.cls)}>{st.label}</span>
                <Link to={`/video/${s.script_id}`} className="text-sm font-medium hover:underline">{s.title || s.script_id.slice(0, 8)}</Link>
                <CredibilityBadge value={s.credibility} />
                {s.auto ? <span className="badge border border-border text-text-muted">自动</span> : null}
                <span className="text-xs text-text-muted">{cellName(s.column_key)} · 定稿 v{s.approved_version}</span>
                <TimeStamp value={s.created_at} label="创建" />
                {s.render?.status === "COMPLETED" ? <TimeStamp value={s.render.completed_at} label="成片完成" /> : null}
                {s.video_url ? <a className="text-xs text-accent hover:underline" href={s.video_url} target="_blank" rel="noreferrer">看成片</a> : null}
                <Link className="text-xs text-accent hover:underline" to={`/video/${s.script_id}?tab=${s.render?.status === "COMPLETED" ? "publish" : "video"}`}>
                  {s.render?.status === "COMPLETED" ? "发布包与记录 →" : "去出片 →"}
                </Link>
              </div>
              {s.render?.error ? <p className="text-xs text-danger">{s.render.error}</p> : null}
              {s.warnings.map((w) => (
                <p key={w} className="text-xs text-warning">{w}</p>
              ))}
              {s.publications.map((p) => (
                <p key={p.id} className="text-xs text-text-muted">
                  {p.published_on} · <a className="text-accent hover:underline" href={p.url} target="_blank" rel="noreferrer">{p.url}</a>
                </p>
              ))}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
