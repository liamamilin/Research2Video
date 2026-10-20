import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Link, useNavigate } from "react-router-dom";
import {
  createVideoScript,
  listVideoCells,
  listVideoScripts,
  listVideoTopics,
  type ScriptStatus,
  type VideoCell,
  type VideoScriptSummary,
  type VideoTopic,
} from "@/api/video";
import { CredibilityBadge, TIER_LABEL } from "@/components/CredibilityBadge";
import { ErrorState } from "@/components/ErrorState";
import { FilterBar, inRange, matchesText, RANGE_FACET, useFilters, type Facet } from "@/components/FilterBar";
import { TimeStamp } from "@/components/TimeStamp";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage, useToast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { DraftQualityPanel } from "./VideoQuality";

export const STATUS_LABEL: Record<ScriptStatus, string> = {
  RESEARCHING: "研究中",
  WRITING: "写稿中",
  DRAFT: "待审稿",
  APPROVED: "已定稿",
  FAILED: "失败",
};

export const STATUS_CLASS: Record<ScriptStatus, string> = {
  RESEARCHING: "bg-accent/15 text-accent border-accent/30",
  WRITING: "bg-accent/15 text-accent border-accent/30",
  DRAFT: "bg-warning/15 text-warning border-warning/30",
  APPROVED: "bg-success/15 text-success border-success/30",
  FAILED: "bg-danger/15 text-danger border-danger/30",
};

export const FORM_LABEL: Record<string, string> = {
  auto: "自动选择",
  information_motion: "信息动效",
  process_diagram: "流程图解",
};

export const STATUS_NOTE: Partial<Record<VideoCell["status"], string>> = {
  experimental: "未验证",
  blocked: "不可用",
};

const IN_FLIGHT: ScriptStatus[] = ["RESEARCHING", "WRITING"];

export function minutes(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

export function VideoScriptsPage() {
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";
  const toast = useToast();
  const navigate = useNavigate();

  const [scripts, setScripts] = useState<VideoScriptSummary[] | null>(null);
  const [cells, setCells] = useState<VideoCell[]>([]);
  const [params, setParams] = useSearchParams();
  const filters = useFilters(["status", "column", "tier", "source", "range"]);
  const statusFilter = filters.values.status;
  const highlight = params.get("topic");
  const [cellId, setCellId] = useState<string>(params.get("cell") ?? "");
  const [form, setForm] = useState<string>("");
  const [picked, setPicked] = useState<string[]>([]);
  // 从选题页勾选带过来的条目（?topics=a,b），只用一次
  const arrived = useRef<string[] | null>(params.get("topics") ? params.get("topics")!.split(",").filter(Boolean) : null);
  const [roundupTitle, setRoundupTitle] = useState("");
  const [compare, setCompare] = useState({ first: "", second: "", dimensions: "", audience: "" });
  const [round, setRound] = useState<string | null>(null);
  const [topics, setTopics] = useState<VideoTopic[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"topics" | "manual">("topics");
  const [busy, setBusy] = useState<string | null>(null);
  const [manual, setManual] = useState({ title: "", angle: "", audience: "" });

  const showArchived = statusFilter === "archived";
  const load = useCallback(async () => {
    try {
      const [s, c] = await Promise.all([listVideoScripts(showArchived), listVideoCells()]);
      setScripts(s);
      setCells(c);
      setCellId((current) => current || (c.find((x) => x.usable)?.id ?? ""));
      setError(null);
    } catch (e) {
      setError(errorMessage(e, "加载视频文稿失败"));
    }
  }, [showArchived]);

  useEffect(() => {
    load();
  }, [load]);

  // Follow scripts that are still being researched or written.
  const inFlight = scripts?.some((s) => IN_FLIGHT.includes(s.status)) ?? false;
  useEffect(() => {
    if (!inFlight) return;
    const timer = setInterval(() => {
      listVideoScripts(showArchived).then(setScripts).catch(() => undefined);
    }, 5000);
    return () => clearInterval(timer);
  }, [inFlight, showArchived]);

  const { values: f } = filters;
  const shownScripts = (scripts ?? []).filter(
    (x) =>
      (!f.column || x.column_key === f.column) &&
      (!f.tier || x.credibility?.tier === f.tier) &&
      (!f.source || (f.source === "auto") === Boolean(x.auto)) &&
      inRange(x.created_at, f.range) &&
      matchesText(f.q, x.title, x.topic?.title) &&
      (!f.status || showArchived ||
        (f.status === "published" ? x.published : f.status === "RESEARCHING" ? IN_FLIGHT.includes(x.status) : x.status === f.status)),
  );
  const facets: Facet[] = [
    { key: "status", label: "状态", options: [["RESEARCHING", "研究/写稿中"], ["DRAFT", "草稿"], ["APPROVED", "已定稿"], ["published", "已发布"], ["FAILED", "失败"], ["archived", "已归档"]].map(([value, label]) => ({ value, label })) },
    { key: "column", label: "栏目格子", options: Array.from(new Set((scripts ?? []).map((x) => x.column_key))).map((key) => ({ value: key, label: cells.find((c) => c.key === key)?.label ?? key })) },
    { key: "tier", label: "可信度", options: (Object.keys(TIER_LABEL) as (keyof typeof TIER_LABEL)[]).map((k) => ({ value: k, label: TIER_LABEL[k] })) },
    { key: "source", label: "来源", options: [{ value: "auto", label: "自动流水线" }, { value: "manual", label: "手动创建" }] },
    RANGE_FACET,
  ];
  const cell = cells.find((c) => c.id === cellId);
  const column = cell?.column ?? null;
  const kind = column?.topic_kind ?? "single";
  // 快讯类和盘点的选题来自发现报告（F<n>），其余来自内容机会（C<n>）
  const source = kind === "roundup" || cell?.parts?.type.key === "quick_take" ? "flash" : "opportunities";
  const forms = column?.allowed_forms ?? [];

  // 换了格子：回到它默认的视频形式，清掉已选条目，按它的选题来源重新取选题
  useEffect(() => {
    setForm("");
    setPicked(kind === "roundup" ? arrived.current ?? [] : []);
    if (kind === "roundup") arrived.current = null;
    if (kind === "comparison") return;
    let alive = true;
    listVideoTopics(source)
      .then((t) => {
        if (!alive) return;
        setRound(t.round);
        setTopics(t.topics);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [cellId, kind, source]);

  useEffect(() => {
    if (highlight && topics.some((t) => t.id === highlight)) {
      document.getElementById(`topic-${highlight}`)?.scrollIntoView?.({ block: "center" });
    }
  }, [highlight, topics]);

  async function create(body: Parameters<typeof createVideoScript>[0], key: string) {
    setBusy(key);
    try {
      const made = await createVideoScript({
        column: cell?.key ?? undefined,
        ...(form ? { video_form: form } : {}),
        ...body,
      });
      if (kind === "roundup") toast.success("已开始写稿", "盘点直接用发现报告写稿，通常几分钟");
      else toast.success("已开始研究", "研究通常需要 3–10 分钟，完成后自动写稿");
      navigate(`/video/${made.id}`);
    } catch (e) {
      toast.error("没能开始", errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold flex-1">视频文稿</h1>
      </div>

      {canEdit && cell && column ? (
        <section className="card space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-medium">新建</h2>
            <select
              aria-label="格子"
              className="input w-auto max-w-full"
              value={cellId}
              onChange={(e) => setCellId(e.target.value)}
            >
              {cells.map((c) => (
                <option key={c.id} value={c.id} disabled={!c.usable}>
                  {c.label}
                  {STATUS_NOTE[c.status] ? `（${STATUS_NOTE[c.status]}）` : ""}
                </option>
              ))}
            </select>
            {forms.length > 1 ? (
              <select
                aria-label="视频形式"
                className="input w-auto"
                value={form}
                onChange={(e) => setForm(e.target.value)}
              >
                <option value="">视频形式：默认（{FORM_LABEL[column.video_form] ?? column.video_form}）</option>
                {forms
                  .filter((f) => f !== column.video_form)
                  .map((f) => (
                    <option key={f} value={f}>
                      {FORM_LABEL[f] ?? f}
                    </option>
                  ))}
              </select>
            ) : null}
            <span className="flex-1 text-xs text-text-muted text-right">约 {column.target_minutes} 分钟</span>
            {kind === "single" ? (
              <>
                {(["topics", "manual"] as const).map((key) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={cn(
                  "badge border cursor-pointer",
                  tab === key ? "bg-accent-solid text-white border-accent" : "bg-bg-card border-border hover:bg-bg-hover",
                )}
              >
                    {key === "topics" ? (source === "flash" ? "从快讯条目选" : "从内容机会选") : "手写选题"}
                  </button>
                ))}
              </>
            ) : null}
          </div>
          {cell.status === "experimental" ? (
            <p className="text-xs text-warning">这个格子还没出过片，写出的文稿和视频效果没有验证过。</p>
          ) : null}
          <p className="text-xs text-text-muted">{column.description}</p>

          {kind === "roundup" ? (
            <div className="space-y-2">
              <p className="text-xs text-text-muted">
                选 2–10 条{round ? `（发现报告 ${round}）` : ""}，每条讲一节，按选的顺序排。已选 {picked.length} 条。
              </p>
              {topics.map((t) => (
                <label key={t.id} className="flex items-start gap-3 rounded border border-border p-3 cursor-pointer">
                  <input
                    type="checkbox"
                    aria-label={`${t.code} ${t.title}`}
                    checked={picked.includes(t.id)}
                    onChange={(e) =>
                      setPicked(e.target.checked ? [...picked, t.id] : picked.filter((id) => id !== t.id))
                    }
                  />
                  <span className="font-mono text-xs text-text-muted pt-0.5">{t.code}</span>
                  <span className="text-sm flex-1">{t.title}</span>
                  {picked.includes(t.id) ? (
                    <span className="badge border border-border text-xs">第 {picked.indexOf(t.id) + 1} 条</span>
                  ) : null}
                </label>
              ))}
              {topics.length === 0 ? <ErrorState compact empty="还没有快讯发现报告。" /> : null}
              <input
                className="input"
                placeholder="盘点标题（可选）"
                value={roundupTitle}
                onChange={(e) => setRoundupTitle(e.target.value)}
              />
              <button
                className="btn btn-primary text-xs"
                disabled={busy !== null || picked.length < 2 || picked.length > 10}
                onClick={() =>
                  create({ topic_ids: picked, ...(roundupTitle.trim() ? { topic: { title: roundupTitle.trim() } } : {}) }, "roundup")
                }
              >
                {busy === "roundup" ? "开始中..." : "写这一期盘点"}
              </button>
            </div>
          ) : kind === "comparison" ? (
            <form
              className="grid gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                create(
                  {
                    topic: {
                      subjects: [compare.first.trim(), compare.second.trim()],
                      dimensions: compare.dimensions.trim(),
                      audience: compare.audience.trim(),
                    },
                  },
                  "compare",
                );
              }}
            >
              <div className="grid grid-cols-2 gap-2">
                <input
                  className="input"
                  placeholder="对象一（必填）"
                  value={compare.first}
                  onChange={(e) => setCompare({ ...compare, first: e.target.value })}
                />
                <input
                  className="input"
                  placeholder="对象二（必填）"
                  value={compare.second}
                  onChange={(e) => setCompare({ ...compare, second: e.target.value })}
                />
              </div>
              <input
                className="input"
                placeholder="对比维度，例如：价格、上下文长度"
                value={compare.dimensions}
                onChange={(e) => setCompare({ ...compare, dimensions: e.target.value })}
              />
              <input
                className="input"
                placeholder="目标受众（可选）"
                value={compare.audience}
                onChange={(e) => setCompare({ ...compare, audience: e.target.value })}
              />
              <div>
                <button
                  type="submit"
                  className="btn btn-primary text-xs"
                  disabled={
                    busy !== null ||
                    !compare.first.trim() ||
                    !compare.second.trim() ||
                    compare.first.trim() === compare.second.trim()
                  }
                >
                  {busy === "compare" ? "开始中..." : "开始研究"}
                </button>
              </div>
            </form>
          ) : tab === "topics" ? (
            topics.length ? (
              <div className="space-y-2">
                <p className="text-xs text-text-muted">
                  {source === "flash" ? `快讯发现报告 ${round} 的条目` : `情报轮次 ${round} 的内容机会（P7）`}
                </p>
                {topics.map((t) => (
                  <div
                    key={t.id}
                    id={`topic-${t.id}`}
                    className={cn("flex items-start gap-3 rounded border p-3", highlight === t.id ? "border-accent ring-1 ring-accent" : "border-border")}
                  >
                    <span className="font-mono text-xs text-text-muted pt-0.5">{t.code}</span>
                    <div className="min-w-0 flex-1 space-y-1">
                      <p className="text-sm">{t.title}</p>
                      {t.fields["目标受众"] ? (
                        <p className="text-xs text-text-muted">受众：{t.fields["目标受众"]}</p>
                      ) : null}
                      {t.fields["核心角度"] ? (
                        <p className="text-xs text-text-muted">角度：{t.fields["核心角度"]}</p>
                      ) : null}
                    </div>
                    {t.score != null ? <span className="badge border border-border text-xs">{t.score}/10</span> : null}
                    <button
                      className="btn btn-primary text-xs shrink-0"
                      disabled={busy !== null || t.scripted}
                      onClick={() => create({ topic_id: t.id }, t.id)}
                      title={t.scripted ? "这个选题已经写过" : undefined}
                    >
                      {t.published ? "已发布" : t.scripted ? "已写过" : busy === t.id ? "开始中..." : "写这一篇"}
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <ErrorState
                compact
                empty={source === "flash" ? "还没有快讯发现报告。" : "还没有内容机会报告。跑一轮情报流水线后，这里会列出它推荐的视频选题。"}
              />
            )
          ) : (
            <form
              className="grid gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                create({ topic: manual }, "manual");
              }}
            >
              <input
                className="input"
                placeholder="选题（必填），例如：crewAI eval --models 的每次运行成本怎么看"
                value={manual.title}
                onChange={(e) => setManual({ ...manual, title: e.target.value })}
              />
              <input
                className="input"
                placeholder="核心角度（可选）"
                value={manual.angle}
                onChange={(e) => setManual({ ...manual, angle: e.target.value })}
              />
              <input
                className="input"
                placeholder="目标受众（可选）"
                value={manual.audience}
                onChange={(e) => setManual({ ...manual, audience: e.target.value })}
              />
              <div>
                <button
                  type="submit"
                  className="btn btn-primary text-xs"
                  disabled={busy !== null || manual.title.trim().length < 4}
                >
                  {busy === "manual" ? "开始中..." : "开始研究"}
                </button>
              </div>
            </form>
          )}
        </section>
      ) : null}

      <FilterBar facets={facets} filters={filters} shown={shownScripts.length} total={(scripts ?? []).length} />

      <section className="card p-0 overflow-hidden">
        {error ? (
          <ErrorState error={error} onRetry={load} />
        ) : scripts === null ? (
          <ErrorState empty="加载中..." />
        ) : scripts.length === 0 ? (
          <ErrorState empty="还没有视频文稿。" />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-text-muted border-b border-border">
              <tr>
                <th className="text-left font-normal px-4 py-2">文稿</th>
                <th className="text-left font-normal px-4 py-2">状态</th>
                <th className="text-left font-normal px-4 py-2">可信度</th>
                <th className="text-left font-normal px-4 py-2">时长</th>
                <th className="text-left font-normal px-4 py-2">创建</th>
              </tr>
            </thead>
            <tbody>
              {shownScripts.map((s) => (
                <tr key={s.id} className="border-b border-border last:border-0 hover:bg-bg-hover">
                  <td className="px-4 py-2">
                    <Link to={`/video/${s.id}`} className="text-accent hover:underline">
                      {s.title}
                    </Link>
                    {s.published ? <span className="badge border border-success/30 bg-success/15 text-success ml-2">已发布</span> : null}
                    {s.auto ? <span className="badge border border-border text-text-muted ml-2">自动</span> : null}
                  </td>
                  <td className="px-4 py-2">
                    <span className={cn("badge border", STATUS_CLASS[s.status])}>
                      {STATUS_LABEL[s.status]}
                      {s.status === "WRITING" && s.stage ? ` · ${s.stage}` : ""}
                    </span>
                  </td>
                  <td className="px-4 py-2"><CredibilityBadge value={s.credibility} /></td>
                  <td className="px-4 py-2 text-text-muted">{minutes(s.length_check?.estimated_seconds)}</td>
                  <td className="px-4 py-2"><TimeStamp value={s.created_at} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <DraftQualityPanel />
    </div>
  );
}
