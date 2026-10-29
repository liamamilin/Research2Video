import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Link, useNavigate } from "react-router-dom";
import { RefreshCw, SlidersHorizontal } from "lucide-react";
import { listProduction } from "@/api/production";
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
import { DistributionSummary, DistributionFilters } from "@/components/DistributionSummary";
import { matchesDistribution } from "@/lib/distributions";
import { CredibilityBadge, TIER_LABEL } from "@/components/CredibilityBadge";
import { ErrorState } from "@/components/ErrorState";
import { FilterBar, inRange, matchesText, RANGE_FACET, useFilters, type Facet } from "@/components/FilterBar";
import { TimeStamp } from "@/components/TimeStamp";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage, useToast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { DraftQualityPanel } from "./VideoQuality";
import { ArticleCreate } from "@/components/ArticleCreate";

export const STATUS_LABEL: Record<ScriptStatus, string> = {
  RESEARCHING: "研究中",
  WRITING: "写稿中",
  PLAN_READY: "方案待确认",
  DRAFT: "待审稿",
  APPROVED: "已定稿",
  FAILED: "失败",
};

export const STATUS_CLASS: Record<ScriptStatus, string> = {
  RESEARCHING: "bg-accent/15 text-accent border-accent/30",
  WRITING: "bg-accent/15 text-accent border-accent/30",
  PLAN_READY: "bg-warning/15 text-warning border-warning/30",
  DRAFT: "bg-warning/15 text-warning border-warning/30",
  APPROVED: "bg-success/15 text-success border-success/30",
  FAILED: "bg-danger/15 text-danger border-danger/30",
};

/** 文稿在流水线上走到哪：写稿的几步看文稿状态，定稿之后看它出片到哪一步 */
export type Stage = ScriptStatus | "rendering" | "rendered" | "render_failed" | "published";
const RENDERING = ["SUBMITTING", "QUEUED", "RUNNING", "DOWNLOADING"];

export function stageOf(s: { status: ScriptStatus; published?: boolean; render?: { status: string } | null }): Stage {
  if (s.status !== "APPROVED" || !s.render) return s.status;
  if (s.render.status === "COMPLETED") return "rendered";
  return RENDERING.includes(s.render.status) ? "rendering" : s.render.status === "FAILED" ? "render_failed" : s.status;
}

export const STAGE_LABEL: Record<Stage, string> = {
  ...STATUS_LABEL, APPROVED: "已定稿", rendering: "出片中", rendered: "成片完成", render_failed: "出片失败", published: "已发布",
};
export const STAGE_CLASS: Record<Stage, string> = {
  ...STATUS_CLASS,
  rendering: "bg-accent/15 text-accent border-accent/30",
  rendered: "bg-success/15 text-success border-success/30",
  render_failed: "bg-danger/15 text-danger border-danger/30",
  published: "bg-success/15 text-success border-success/30",
};

/** 列表按天分组的标题 */
export function dayLabel(iso: string | null | undefined): string {
  if (!iso) return "更早";
  const t = new Date(iso.replace(/([+-]\d{2})(\d{2})$/, "$1:$2"));
  if (Number.isNaN(t.getTime())) return iso.slice(0, 10);
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const gap = Math.round((day(new Date()) - day(t)) / 86400000);
  return gap === 0 ? "今天" : gap === 1 ? "昨天" : `${t.getMonth() + 1} 月 ${t.getDate()} 日`;
}

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
// 状态按钮的顺序：先写稿，再出片，最后发布；失败的放最后
const CHIPS = ["RESEARCHING", "PLAN_READY", "DRAFT", "APPROVED", "rendering", "rendered", "render_failed", "unpublished", "published", "FAILED"];

export function minutes(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

export function VideoScriptsPage() {
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";
  const toast = useToast();
  const navigate = useNavigate();

  const [result, setResult] = useState<{ archived: boolean; scripts: VideoScriptSummary[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const flight = useRef<AbortController | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [quality, setQuality] = useState(false);
  const [limit, setLimit] = useState(24);
  const [cellError, setCellError] = useState("");
  const [cellLoading, setCellLoading] = useState(false);
  const [cellRetry, setCellRetry] = useState(0);
  const cellReady = useRef(false);
  const [topicError, setTopicError] = useState("");
  const [topicLoading, setTopicLoading] = useState(false);
  const [topicRetry, setTopicRetry] = useState(0);
  const [cells, setCells] = useState<VideoCell[]>([]);
  // 生产配置派生出来的栏目（如 flash-nr）不在格子目录里：用配置的名字，不显示内部的键
  const [configNames, setConfigNames] = useState<Record<string, string>>({});
  const [params, setParams] = useSearchParams();
  const filters = useFilters(["status", "column", "tier", "source", "range", "matrix", "archived", "platform", "distribution"]);
  const requestedStatus = params.get("status") ?? "all";
  const statusFilter = ["archived", "all"].includes(requestedStatus) ? "" : requestedStatus;
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
  // 新建的表单很长，平时收起来，让列表在最上面；从选题页带着选题过来时直接展开
  const [creating, setCreating] = useState(Boolean(params.get("topic") || params.get("topics") || params.get("cell")));
  const [articleOpen, setArticleOpen] = useState(false);
  const [articleMounted, setArticleMounted] = useState(false);

  const showArchived = filters.values.archived === "1" || filters.values.status === "archived";
  const scripts = result?.archived === showArchived ? result.scripts : null;
  const load = useCallback(async () => {
    if (flight.current) return;
    const controller = new AbortController(); flight.current = controller; setLoading(true);
    try {
      const rows = await listVideoScripts(showArchived, controller.signal);
      if (!Array.isArray(rows)) throw new Error("文稿数据格式异常，请重试");
      if (!controller.signal.aborted) { setResult({ archived: showArchived, scripts: rows }); setError(null); }
    } catch (e) { if (!controller.signal.aborted) setError(errorMessage(e, "加载视频文稿失败")); }
    finally { if (flight.current === controller) { flight.current = null; setLoading(false); } }
  }, [showArchived]);
  useEffect(() => { setError(null); void load(); return () => { flight.current?.abort(); flight.current = null; }; }, [load]);
  const inFlight = scripts?.some((s) => IN_FLIGHT.includes(s.status) || (s.status !== "FAILED" && s.stage) || (s.render && RENDERING.includes(s.render.status))) ?? false;
  useEffect(() => {
    const refresh = () => { if (!document.hidden) void load(); };
    const timer = window.setInterval(refresh, inFlight ? 5000 : 30000);
    document.addEventListener("visibilitychange", refresh);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [inFlight, load]);
  const namesLoaded = useRef(false);
  useEffect(() => {
    if (namesLoaded.current || !scripts?.some((s) => !s.column_label)) return;
    namesLoaded.current = true;
    const controller = new AbortController();
    listProduction(undefined, controller.signal).then((rows) => {
      if (!controller.signal.aborted && Array.isArray(rows)) setConfigNames(Object.fromEntries(rows.flatMap((c) => [[c.key, c.name], [c.recipe, c.name]])));
    }).catch(() => { namesLoaded.current = false; });
    return () => { controller.abort(); };
  }, [scripts]);
  useEffect(() => {
    if (!creating || (cellReady.current && cellRetry === 0)) return;
    const controller = new AbortController(); setCellLoading(true);
    listVideoCells(controller.signal).then((rows) => {
      if (!Array.isArray(rows)) throw new Error("配方数据格式异常，请重试");
      if (!controller.signal.aborted) { cellReady.current = true; setCells(rows); setCellId((current) => current || rows.find((c) => c.usable)?.id || ""); setCellError(""); }
    }).catch((e) => { if (!controller.signal.aborted) setCellError(errorMessage(e)); })
      .finally(() => { if (!controller.signal.aborted) setCellLoading(false); });
    return () => { controller.abort(); };
  }, [creating, cellRetry]);

  const { values: f } = filters;
  // 研究和写稿合成一格：对用户来说都是「还在写」
  const chipOf = (x: VideoScriptSummary): string => {
    const stage = stageOf(x);
    return stage === "WRITING" ? "RESEARCHING" : stage;
  };
  const scope = (scripts ?? []).filter((s) => !f.matrix || (s.matrix || "_independent") === f.matrix);
  const matrixOptions = [...new Map((scripts ?? []).map((s) => [s.matrix || "_independent", s.matrix_name || "独立视频"]))];
  const columnName = (key: string) => (scripts ?? []).find((s) => s.column_key === key)?.column_label || cells.find((c) => c.key === key)?.label?.split(" · ").slice(0, 2).join(" · ") || configNames[key] || key;
  const matchesStatus = (x: VideoScriptSummary, key: string) => key === "unpublished" ? !x.published : key === "published" ? Boolean(x.published) : chipOf(x) === key;
  const chipCount = (key: string) => scope.filter((x) => matchesStatus(x, key)).length;
  const shownScripts = scope.filter(
    (x) =>
      (!f.column || x.column_key === f.column) &&
      (!f.tier || x.credibility?.tier === f.tier) &&
      (!f.source || (f.source === "auto") === Boolean(x.auto)) &&
      matchesDistribution(x.distributions, f.platform, f.distribution, x.published) &&
      inRange(x.created_at, f.range) &&
      matchesText(f.q, x.title, x.topic?.title, x.job_name, x.job, x.matrix_name) &&
      (!statusFilter || matchesStatus(x, statusFilter)),
  ).sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  const facets: Facet[] = [
    { key: "column", label: "视频类型", options: Array.from(new Set(scope.map((x) => x.column_key))).map((key) => ({ value: key, label: columnName(key) })) },
    { key: "tier", label: "可信度", options: (Object.keys(TIER_LABEL) as (keyof typeof TIER_LABEL)[]).map((k) => ({ value: k, label: TIER_LABEL[k] })) },
    { key: "source", label: "来源", options: [{ value: "auto", label: "自动流水线" }, { value: "manual", label: "手动创建" }] },
    RANGE_FACET,
  ];
  const cell = cells.find((c) => c.id === cellId);
  const column = cell?.column ?? null;
  const kind = column?.topic_kind ?? "single";
  // 选题来源由配方的 topic.sources 决定（第一个）；从选题页带来的手动选题、或配方没写来源时，列手动添加的选题
  const incomingSource = params.get("cell") === cellId ? params.get("topic_source") : null;
  const source = incomingSource || (highlight?.startsWith("manual/") || !cell?.topic_sources?.length ? "manual" : cell.topic_sources[0]);
  const sourceRound = incomingSource ? params.get("topic_round") || undefined : undefined;
  const forms = column?.allowed_forms ?? [];

  useEffect(() => {
    setForm("");
    setPicked(kind === "roundup" ? arrived.current ?? [] : []);
    if (kind === "roundup") arrived.current = null;
  }, [cellId, kind]);
  useEffect(() => {
    if (!creating || !column || kind === "comparison") return;
    const controller = new AbortController(); setTopicLoading(true); setTopics([]); setRound(null); setTopicError("");
    listVideoTopics(source, sourceRound, controller.signal).then((body) => {
      if (!controller.signal.aborted) { setRound(body.round); setTopics(body.topics); }
    }).catch((e) => { if (!controller.signal.aborted) setTopicError(errorMessage(e)); })
      .finally(() => { if (!controller.signal.aborted) setTopicLoading(false); });
    return () => { controller.abort(); };
  }, [creating, cellId, column, kind, source, sourceRound, topicRetry]);
  useEffect(() => { setLimit(24); }, [f.q, f.matrix, f.column, f.tier, f.source, f.range, f.platform, f.distribution, statusFilter, showArchived]);

  useEffect(() => {
    if (highlight && topics.some((t) => t.id === highlight)) {
      document.getElementById(`topic-${highlight}`)?.scrollIntoView?.({ block: "center" });
    }
  }, [highlight, topics]);

  async function create(body: Parameters<typeof createVideoScript>[0], key: string) {
    if (busy !== null) return;
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
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div><h1 className="text-xl font-semibold tracking-tight">视频文稿</h1><p className="text-xs text-text-muted mt-1">按矩阵查找文稿，查看当前版本、事实审计与制作进度。</p></div>
        <div className="flex flex-wrap gap-2"><button className="btn text-xs" aria-label="刷新文稿" disabled={loading} onClick={load}><RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />刷新</button>{canEdit ? <button className={cn("btn text-xs", !creating && "btn-primary")} aria-expanded={creating} onClick={() => { setCreating(!creating); setArticleOpen(false); }}>{creating ? "收起新建" : "＋ 新建文稿"}</button> : null}</div>
      </header>
      {canEdit ? <button className="btn text-xs" aria-expanded={articleOpen} onClick={() => { setArticleMounted(true); setArticleOpen(!articleOpen); setCreating(false); }}>{articleOpen ? "收起文章创作" : "粘贴／上传文章"}</button> : null}
      {articleMounted && canEdit ? <div hidden={!articleOpen}><ArticleCreate onCreated={id => navigate(`/video/${id}`)} /></div> : null}
      {creating && canEdit && (!column || cellLoading) ? <section className="card p-4 text-sm space-y-3">{cellError ? <ErrorState error={cellError} onRetry={() => setCellRetry((n) => n + 1)} compact /> : cellLoading ? <p role="status" className="text-text-muted">正在读取文稿配方…</p> : <><p>所选配方暂不可用，请选择其他配方。</p><select aria-label="选择可用配方" className="input text-xs" value={cellId} onChange={(e) => setCellId(e.target.value)}><option value="">选择配方</option>{cells.filter((c) => c.usable).map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</select><Link className="text-xs text-accent" to="/video-recipes">查看文稿配方</Link></>}</section> : null}

      {canEdit && cell && column && creating ? (
        <section className="card p-4 space-y-3" aria-label="新建文稿">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-medium text-sm">从选题新建</h2>
            <select
              aria-label="格子"
              disabled={busy !== null}
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
                    {key === "topics" ? "从选题库选择" : "手写选题"}
                  </button>
                ))}
              </>
            ) : null}
          </div>
          {cell.status === "experimental" ? (
            <p className="text-xs text-warning">这个格子还没出过片，写出的文稿和视频效果没有验证过。</p>
          ) : null}
          <p className="text-xs text-text-muted">{column.description}</p>
          {f.matrix && f.matrix !== "_independent" ? <p className="text-xs text-text-muted">使用矩阵的研究报告写稿，可从 <Link className="text-accent hover:underline" to={`/matrices/${encodeURIComponent(f.matrix)}`}>研究任务的视频配置</Link> 进入。</p> : null}
          {topicError && tab === "topics" ? <ErrorState error={topicError} onRetry={() => setTopicRetry((n) => n + 1)} compact /> : null}
          {topicLoading && (kind === "roundup" || tab === "topics") ? <p role="status" className="text-xs text-text-muted">正在读取选题…</p> : null}

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
              {topics.length === 0 && !topicLoading && !topicError ? <ErrorState compact empty="这个来源还没有可用的选题报告。" /> : null}
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
            topicLoading || topicError ? null : topics.length ? (
              <div className="space-y-2">
                <p className="text-xs text-text-muted">
                  {`选题报告 ${round || ""} 的条目`}
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
                empty="这个来源还没有可用选题。可前往选题页选择，或手写选题。"
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

      <div className="flex flex-wrap gap-2 items-center">
        <select aria-label="文稿矩阵" className="input w-auto max-w-full text-xs" value={f.matrix} onChange={(e) => setParams((old) => { const next = new URLSearchParams(old); ["q", "status", "column", "tier", "source", "range"].forEach((k) => next.delete(k)); if (showArchived) next.set("archived", "1"); if (e.target.value) next.set("matrix", e.target.value); else next.delete("matrix"); return next; }, { replace: true })}><option value="">全部矩阵</option>{matrixOptions.map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select>
        <select aria-label="文稿归档范围" className="input w-auto text-xs" value={showArchived ? "1" : ""} onChange={(e) => setParams((old) => { const next = new URLSearchParams(old); ["status", "matrix"].forEach((k) => next.delete(k)); if (e.target.value) next.set("archived", "1"); else next.delete("archived"); return next; }, { replace: true })}><option value="">使用中</option><option value="1">已归档</option></select>
        <DistributionFilters platform={f.platform} status={f.distribution} onPlatform={v => filters.set("platform", v)} onStatus={v => filters.set("distribution", v)} />
        <button className="btn text-xs" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}><SlidersHorizontal className="h-3.5 w-3.5" />筛选{f.column || f.tier || f.source || f.range ? " · 已启用" : ""}</button>
      </div>
      {scripts ? <div className="flex flex-wrap items-center gap-2" aria-label="状态">{([["", "全部"], ...CHIPS.map((c) => [c, c === "RESEARCHING" ? "研究/写稿中" : c === "unpublished" ? "从未发布到任何平台" : STAGE_LABEL[c as Stage]])] as [string, string][]).filter(([key]) => key === "" || key === "unpublished" || statusFilter === key || chipCount(key) > 0).map(([key, label]) => <button key={key} aria-pressed={statusFilter === key} onClick={() => filters.set("status", key || "all")} className={cn("badge border cursor-pointer", statusFilter === key ? "bg-accent-solid text-white border-accent" : "bg-bg-card border-border hover:bg-bg-hover")}>{label} {key ? chipCount(key) : scope.length}</button>)}</div> : null}
      <FilterBar facets={advanced ? facets : []} filters={filters} shown={shownScripts.length} total={scope.length} placeholder="搜索文稿或来源任务" />
      {error ? <div><ErrorState error={error} onRetry={load} compact />{scripts ? <p className="text-xs text-warning mt-2">暂时显示上次读取的文稿。</p> : null}</div> : null}
      {scripts === null && !error ? <div className="card p-8 text-sm text-text-muted" role="status">正在读取文稿…</div> : null}
      {scripts ? !shownScripts.length ? <section className="card p-8 text-center space-y-3"><p className="text-sm">{scripts.length ? statusFilter === "unpublished" ? "暂无未发布文稿" : "没有符合筛选的文稿" : "还没有视频文稿"}</p>{filters.active ? <button className="btn text-xs" onClick={filters.clear}>清除筛选</button> : scripts.length ? <button className="btn text-xs" onClick={() => filters.set("status", "all")}>查看全部文稿</button> : <Link className="btn text-xs" to="/matrices">从研究矩阵开始</Link>}</section> : <section className="card overflow-hidden"><div className="overflow-x-auto"><table className="w-full min-w-[42rem] text-sm">
        <thead className="text-xs text-text-muted border-b border-border"><tr><th className="text-left font-normal px-4 py-3">文稿与来源</th><th className="text-left font-normal px-4 py-3">制作进度</th><th className="text-left font-normal px-4 py-3">事实审计</th><th className="text-left font-normal px-4 py-3">创建时间</th></tr></thead>
        <tbody>{shownScripts.slice(0, limit).map((s, i) => <Fragment key={s.id}>{i === 0 || dayLabel(s.created_at) !== dayLabel(shownScripts[i - 1].created_at) ? <tr className="bg-bg-hover/50"><td colSpan={4} className="px-4 py-2 text-xs text-text-muted">{dayLabel(s.created_at)}</td></tr> : null}<tr className="border-b border-border last:border-0 hover:bg-bg-hover">
          <td className="px-4 py-3 min-w-60"><Link to={`/video/${s.id}`} className="font-medium text-sm leading-6 hover:text-accent hover:underline">{s.title}</Link><div className="text-xs text-text-muted flex flex-wrap gap-x-2 gap-y-1 mt-1"><span>{s.matrix_name || "独立视频"}</span>{s.current_version ? <span>v{s.current_version}</span> : null}{s.has_previous_video ? <span>有旧版成片</span> : null}{s.auto ? <span>自动生成</span> : null}</div><p className="text-xs text-text-muted mt-1">{columnName(s.column_key)}{s.length_check?.estimated_seconds ? ` · 预计口播 ${minutes(s.length_check.estimated_seconds)}` : ""}</p>{s.job_name ? <p className="text-xs text-text-muted mt-1">{s.job_name}</p> : null}</td>
          <td className="px-4 py-3 align-top pt-4"><span className={cn("badge border", STAGE_CLASS[stageOf(s)])}>{STAGE_LABEL[stageOf(s)]}{s.stage ? ` · ${s.stage}` : ""}</span><div className="mt-2"><DistributionSummary rows={s.distributions} /></div></td>
          <td className="px-4 py-3 align-top pt-4"><Link to={`/video/${s.id}?tab=review`} className="inline-flex hover:opacity-80" aria-label={`查看审校：${s.title}`}><CredibilityBadge value={s.credibility} /></Link></td>
          <td className="px-4 py-3 align-top pt-4"><TimeStamp value={s.created_at} staleDays={Infinity} /></td>
        </tr></Fragment>)}</tbody>
      </table></div>{shownScripts.length > limit ? <button className="w-full p-3 text-xs text-accent border-t border-border hover:bg-bg-hover" onClick={() => setLimit((n) => n + 24)}>继续显示更早的文稿（还有 {shownScripts.length - limit} 篇）</button> : null}</section> : null}
      <details className="card" open={quality} onToggle={(e) => setQuality(e.currentTarget.open)}><summary className="p-4 text-sm cursor-pointer">改稿统计<span className="text-xs text-text-muted ml-2">查看人工修改与版本保留情况</span></summary>{quality ? <div className="px-4 pb-4"><DraftQualityPanel /></div> : null}</details>
    </div>
  );
}
