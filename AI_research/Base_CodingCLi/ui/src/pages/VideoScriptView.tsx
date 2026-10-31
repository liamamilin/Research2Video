import { EpisodeOutline, EpisodePlayer } from "@/components/EpisodeProgramme";
import { useCallback, useEffect, useRef, useState } from "react";
import { createSearchParams, Link, useParams, useSearchParams, type SetURLSearchParams } from "react-router-dom";
import {
  approveVideoScript,
  archiveVideoScript,
  getAutoVideoStatus,
  getVideoFacts,
  getVideoScript,
  listVideoStyles,
  renderVideoScript,
  restyleVideoScript,
  rerunVideoStep,
  saveVideoScript,
  type AutoVideoStatus,
  type Evidence,
  type Fact,
  type RenderStatus,
  type StyleCatalog,
  type VideoRender,
  type VideoScriptDetail,
} from "@/api/video";
import { CredibilityBadge } from "@/components/CredibilityBadge";
import { ReportReuse } from "@/components/ReportReuse";
import { PlanReview } from "@/components/PlanReview";
import { ScriptSteps } from "@/components/ScriptSteps";
import { ScriptIteration } from "@/components/ScriptIteration";
import { VideoComparisonPanel } from "@/components/VideoComparison";
import { TimeStamp } from "@/components/TimeStamp";
import { ErrorState } from "@/components/ErrorState";
import { DistributionSummary } from "@/components/DistributionSummary";
import { publishedPlatformCount } from "@/lib/distributions";
import { PublicationFlow } from "@/components/PublicationFlow";
import { UPLOAD_LABEL } from "@/lib/platform-links";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage, useToast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { currentPublications, nextStep, renderForVersion, renderPhase, TAB_LABEL, type ScriptTab } from "./videoNext";
import { ChecksPanel, DescriptionPanel, FactChips, IssueList, PlanPanel, ReviewNotes, StylePanel } from "./VideoAdaptation";
import { PublishPanel, RenderFolder } from "./VideoPublish";
import { STATUS_CLASS, STATUS_LABEL, minutes } from "./VideoScripts";
import { ArticleSource } from "@/components/ArticleSource";
import { ScriptScreenshots } from "@/components/ScriptScreenshots";
import { workflowReturn } from "@/lib/platform-links";
import { SelectionSkipLinks, skipLabel } from "@/components/SelectionSkip";
import { useScriptEditor } from "@/lib/use-script-editor";

const EVIDENCE: Record<Evidence, { label: string; cls: string }> = {
  CITED: { label: "有出处", cls: "bg-success/15 text-success border-success/30" },
  UNCITED: { label: "无引用", cls: "bg-bg-card text-text-muted border-border" },
  NEEDS_REVIEW: { label: "需核实", cls: "bg-warning/15 text-warning border-warning/30" },
};

const LENGTH: Record<string, { label: string; cls: string }> = {
  OK: { label: "接近参考时长", cls: "text-success" },
  OVER: { label: "偏长", cls: "text-warning" },
  UNDER: { label: "偏短", cls: "text-warning" },
};

const RENDER_LABEL: Record<RenderStatus, string> = {
  SUBMITTING: "提交中",
  QUEUED: "排队中",
  RUNNING: "制作中",
  DOWNLOADING: "正在取回成片",
  COMPLETED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
};
const RENDER_ACTIVE: RenderStatus[] = ["SUBMITTING", "QUEUED", "RUNNING", "DOWNLOADING"];

/** "比目标长 21%" — the status alone hides how far within the tolerance a script is. */
export function deviation(ratio: number): string {
  const pct = Math.round((ratio - 1) * 100);
  if (pct === 0) return "和目标基本一致";
  return pct > 0 ? `比目标长 ${pct}%` : `比目标短 ${-pct}%`;
}

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function VideoScriptViewPage() {
  const { id = "" } = useParams();
  const [search, setRawSearch] = useSearchParams();
  const setSearch: SetURLSearchParams = (next, options) => setRawSearch(prev => {
    const result = createSearchParams(typeof next === "function" ? next(new URLSearchParams(prev)) : next);
    const back = workflowReturn(prev.get("return_to"));
    if (back && !result.has("return_to")) result.set("return_to", back);
    return result;
  }, options);
  const backToTopics = workflowReturn(search.get("return_to"));
  const versionParam = Number(search.get("version")) || undefined;
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";
  const toast = useToast();

  const [detail, setDetail] = useState<VideoScriptDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const editor = useScriptEditor(id, user?.username ?? "", detail?.version_shown);
  const { editing, title: draftTitle, sections: draftSections, setTitle: setDraftTitle, setSections: setDraftSections } = editor;
  const [reuseOpen, setReuseOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [autovideo, setAutovideo] = useState<AutoVideoStatus | null>(null);
  const [voice, setVoice] = useState("");
  const [productionMode, setProductionMode] = useState<"" | "standard" | "fast">("");
  const [renderStatusError, setRenderStatusError] = useState(false);
  const [statusTick, setStatusTick] = useState(0);
  const [operationError, setOperationError] = useState("");
  const actionLock = useRef(false);
  const [focusTarget, setFocusTarget] = useState<{ section: number; paragraph: number | null; at: number } | null>(null);
  const [catalog, setCatalog] = useState<StyleCatalog | null>(null);
  const [facts, setFacts] = useState<Record<string, Fact>>({});
  const [publishVersion, setPublishVersion] = useState<number | null>(null);

  const flight = useRef<AbortController | null>(null);
  const activePage = useRef(id);
  const load = useCallback(async () => {
    if (flight.current) return;
    const controller = new AbortController(); flight.current = controller;
    try {
      const fresh = await getVideoScript(id, versionParam, controller.signal);
      if (!controller.signal.aborted) { setDetail(fresh); setError(null); }
    } catch (e) {
      if (!controller.signal.aborted) setError(errorMessage(e, "加载文稿失败"));
    } finally { if (flight.current === controller) flight.current = null; }
  }, [id, versionParam]);

  useEffect(() => {
    activePage.current = id;
    setDetail(null); setError(null); setPublishVersion(null); setVoice(""); setProductionMode(""); setOperationError(""); void load();
    return () => { activePage.current = ""; flight.current?.abort(); flight.current = null; };
  }, [load, id]);

  const staged = detail && ["DRAFT", "APPROVED"].includes(detail.status);
  const localVideo = detail?.topic.local_video;
  const tabs: ScriptTab[] = localVideo ? ['video', 'publish'] : !detail ? [] : !staged && !detail.renders.length && !detail.publications?.length
    ? []
    : detail.status === "APPROVED" || detail.renders.some(r => r.status === "COMPLETED") || detail.publications?.length
      ? ["draft", "review", "video", "publish"]
      : detail.renders.length
        ? ["draft", "review", "video"]
        : ["draft", "review"];
  if (detail && (detail.versions.length > 1 || detail.renders.length > 1)) tabs.push('compare');
  const asked = search.get("tab") as ScriptTab | null;
  const current = detail?.version_shown?.version === detail?.current_version;
  const defaultTab = detail && (current || !detail.version_shown) ? nextStep(detail).tab : "draft";
  const tab: ScriptTab = editing || !tabs.length ? "draft" : asked && tabs.includes(asked) ? asked : defaultTab;

  const busyServer = detail && (["RESEARCHING", "WRITING"].includes(detail.status) || (detail.status !== "FAILED" && Boolean(detail.stage)) || detail.renders.some((r) => RENDER_ACTIVE.includes(r.status)) || detail.distributions?.some(row => ["QUEUED", "UPLOADING", "RUNNING", "SUBMITTED"].includes(row.task?.status ?? "") || (["kuaishou","xiaohongshu"].includes(row.platform) && row.task?.status === "UNKNOWN")));
  useEffect(() => {
    if (editing) return;
    const refresh = () => { if (!document.hidden) void load(); };
    // Another worker/tab can advance a draft between two stages. Keep a slower
    // refresh when idle so that a brief DRAFT handover never stops live progress.
    const timer = window.setInterval(refresh, busyServer ? 5000 : 30000);
    document.addEventListener("visibilitychange", refresh);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [busyServer, editing, load]);

  useEffect(() => {
    if (tab !== "draft" || !canEdit || detail?.topic.article || detail?.column?.writer !== "adapt" || catalog) return;
    const controller = new AbortController();
    listVideoStyles(controller.signal).then((value) => { if (!controller.signal.aborted) setCatalog(value); }).catch(() => undefined);
    return () => controller.abort();
  }, [tab, canEdit, detail?.topic.article, detail?.column?.writer, catalog]);

  // The fact sheet behind the version on screen, for the fact labels' tooltips.
  const sheetId = detail?.version_shown?.meta?.fact_sheet_id;
  const shownVersion = detail?.version_shown?.version;
  useEffect(() => {
    if (tab !== "draft" || !sheetId) {
      setFacts({});
      return;
    }
    const controller = new AbortController(); setFacts({});
    getVideoFacts(id, shownVersion, controller.signal)
      .then((sheet) => { if (!controller.signal.aborted) setFacts(Object.fromEntries(sheet.facts.map((f) => [f.id, f]))); })
      .catch(() => { if (!controller.signal.aborted) setFacts({}); });
    return () => controller.abort();
  }, [tab, id, sheetId, shownVersion]);

  const needsRenderStatus = detail?.status === "APPROVED" && tab === "video" && !localVideo;
  useEffect(() => {
    if (!canEdit || !needsRenderStatus) return;
    const controller = new AbortController();
    setAutovideo(null); setRenderStatusError(false);
    getAutoVideoStatus(controller.signal).then((value) => { if (!controller.signal.aborted) setAutovideo(value); })
      .catch(() => { if (!controller.signal.aborted) setRenderStatusError(true); });
    return () => controller.abort();
  }, [canEdit, needsRenderStatus, id, statusTick]);

  useEffect(() => {
    if (!focusTarget) return;
    const frame = requestAnimationFrame(() => {
      const suffix = focusTarget.paragraph == null ? "" : `-paragraph-${focusTarget.paragraph}`;
      const target = document.getElementById(`script-section-${focusTarget.section}${suffix}`);
      target?.scrollIntoView?.({ block: "center" });
      (target?.querySelector("textarea, input") as HTMLElement | null)?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [focusTarget, editing]);

  // Pin the default too: a background completion must never switch the video being prepared.
  useEffect(() => {
    if (tab !== "publish" || search.has("render") || !detail) return;
    const done = detail.renders.filter(row => row.status === "COMPLETED");
    const requested = publishVersion ?? Number(search.get("publish_version"));
    const version = done.find(row => row.version === requested)?.version ?? done.find(row => row.version === detail.version_shown?.version)?.version ?? done[0]?.version;
    const chosen = done.find(row => row.version === version && row.files.some(file => file.name === "video.mp4")) ?? done.find(row => row.version === version);
    if (chosen) setRawSearch(previous => { const next = new URLSearchParams(previous); if (next.get("tab") === "publish" && !next.has("render")) { next.set("render", chosen.id); next.set("publish_version", String(chosen.version)); } return next; }, { replace: true });
  }, [detail, tab, search, publishVersion, setRawSearch]);

  if (!detail) return <ErrorState error={error ?? undefined} empty="加载中..." onRetry={load} />;

  const shown = detail.version_shown;
  const isCurrent = shown?.version === detail.current_version;
  const writable = canEdit && isCurrent && !detail.stage && !localVideo;
  const check = shown?.length_check;
  const problems = shown?.meta?.problems ?? [];
  const step = isCurrent || !shown ? nextStep(detail) : { tab: "draft" as const, text: `正在查看历史 v${shown.version}。返回当前 v${detail.current_version} 后可继续审校、编辑与定稿。`, tone: "wait" };
  const completed = detail.renders.filter((r) => r.status === "COMPLETED");
  const publishVersions = [...new Set(completed.map((r) => r.version))];
  const publishingVersion = publishVersions.find((v) => v === (publishVersion ?? Number(search.get("publish_version"))))
    ?? publishVersions.find((v) => v === shown?.version) ?? publishVersions[0] ?? shown?.version;
  const requestedRender = search.get("render");
  const publishingRender = requestedRender ? completed.find(r => r.id === requestedRender && r.version === publishingVersion) : (completed.find((r) => r.version === publishingVersion && r.files?.some(file => file.name === "video.mp4")) ?? completed.find((r) => r.version === publishingVersion));
  const publishingUpload = detail.platform_uploads?.find(task => task.version === publishingVersion);

  const goTab = (t: ScriptTab) =>
    setSearch((prev) => {
      const next = new URLSearchParams(prev);
      next.set("tab", t);
      return next;
    });

  function startEdit() {
    if (!shown || !writable) return;
    setOperationError(""); editor.start();
  }

  function locateIssue(section: number, paragraph: number | null) {
    if (!shown?.sections[section] || (paragraph != null && !shown.sections[section].paragraphs[paragraph])) return;
    if (!editing && writable) startEdit();
    goTab("draft");
    setFocusTarget({ section, paragraph, at: Date.now() });
  }

  async function save() {
    if (!editor.dirty || actionLock.current) return;
    actionLock.current = true; setOperationError("");
    setBusy("save");
    try {
      const fresh = await getVideoScript(id);
      if (activePage.current !== id) return;
      if (fresh.current_version !== editor.baseVersion || fresh.stage || !["DRAFT", "APPROVED"].includes(fresh.status)) {
        throw new Error(`保存版已变化或任务正在处理，请先退出编辑并刷新后对照；本地草稿仍保留在 v${editor.baseVersion}。`);
      }
      const saved = await saveVideoScript(id, { title: draftTitle, sections: draftSections, base_version: editor.baseVersion });
      if (activePage.current !== id) return;
      editor.discard();
      setDetail(saved);
      setSearch({ tab: "draft" });
      toast.success(`已保存为 v${saved.version_shown?.version}`);
    } catch (e) {
      setOperationError(errorMessage(e));
      toast.error("保存失败", errorMessage(e));
    } finally {
      setBusy(null);
      actionLock.current = false;
    }
  }

  async function archive() {
    if (editing || actionLock.current) return;
    actionLock.current = true;
    setBusy("archive");
    try {
      setDetail(await archiveVideoScript(id, !detail?.archived));
      toast.success(detail?.archived ? "已取消归档" : "已归档，列表和看板里不再显示");
    } catch (e) {
      toast.error("归档失败", errorMessage(e));
    } finally {
      setBusy(null);
      actionLock.current = false;
    }
  }

  async function approve() {
    if (editing || editor.recovery || actionLock.current) return;
    actionLock.current = true; setOperationError("");
    setBusy("approve");
    try {
      const approved = await approveVideoScript(id, shown?.version);
      if (activePage.current !== id) return;
      setDetail(approved);
      setSearch({ tab: "video" });
      toast.success(`已定稿 v${shown?.version}`);
    } catch (e) {
      setOperationError(errorMessage(e));
      toast.error("定稿失败", errorMessage(e));
    } finally {
      setBusy(null);
      actionLock.current = false;
    }
  }

  async function render() {
    if (editing || editor.recovery || actionLock.current || !autovideo?.ready || !autovideo.reachable || detail?.renders.some(r => RENDER_ACTIVE.includes(r.status))) return;
    actionLock.current = true; setOperationError("");
    setBusy("render");
    try {
      const made = await renderVideoScript(id, voice || undefined, productionMode || undefined);
      if (activePage.current !== id) return;
      setDetail((d) => (d ? { ...d, renders: [made, ...d.renders.filter(r => r.id !== made.id)] } : d));
      setSearch(prev => { const next = new URLSearchParams(prev); next.set("tab", "video"); next.set("render", made.id); return next; });
      toast.success("已交给 AutoVideo", "正在后台制作，完成后可在这里查看");
    } catch (e) {
      setOperationError(errorMessage(e));
      toast.error("出片没有开始", errorMessage(e));
    } finally {
      setBusy(null);
      actionLock.current = false;
    }
  }

  async function rewrite() {
    if (editing || actionLock.current) return;
    if (!window.confirm(detail?.topic.article ? "用保存的文章，按本次选择的处理方式重新生成文稿并审校？将保存为新版本，旧稿和旧视频保留。" : "用同一份研究报告，按当前配方重新生成文稿并审校？将保存为新版本，旧稿和旧视频保留。新稿需要重新定稿后再出片。")) return;
    actionLock.current = true; setBusy("rewrite");
    try {
      setDetail(await rerunVideoStep(id, "write"));
      setSearch({ tab: "draft" });
      toast.success("已开始重新生成文稿", detail?.topic.article ? "沿用保存的文章，完成后可查看新版本" : "沿用原报告，完成后可查看新版本");
    } catch (e) { toast.error("没有开始重新生成", errorMessage(e)); }
    finally { setBusy(null); actionLock.current = false; }
  }

  async function restyle(depth: string, tone: string) {
    if (editing || actionLock.current) return;
    actionLock.current = true;
    setBusy("restyle");
    try {
      await restyleVideoScript(id, { depth, tone });
      setSearch({});
      await load();
      toast.success("开始用新风格重写", "完成后会多出一个版本，之前的版本都还在");
    } catch (e) {
      toast.error("没有开始重写", errorMessage(e));
    } finally {
      setBusy(null);
      actionLock.current = false;
    }
  }

  function setParagraph(si: number, pi: number, text: string) {
    setDraftSections((secs) =>
      secs.map((s, i) =>
        i !== si ? s : { ...s, paragraphs: s.paragraphs.map((p, j) => (j !== pi ? p : { ...p, text })) },
      ),
    );
  }

  const sections = editing ? draftSections : shown?.sections ?? [];
  const reportPath = detail.report_path.replace(/^output\//, "");

  return (
    <div className="space-y-5 max-w-6xl">
      {error ? <ErrorState error={error} onRetry={load} compact /> : null}
      <Link to={backToTopics ?? "/video"} className="text-xs text-accent hover:underline">
        {backToTopics?.startsWith("/publish") ? "← 返回成片与发布" : backToTopics ? "← 返回选题，继续处理" : "← 视频文稿"}
      </Link>

      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className={cn("badge border", detail.selection_skip ? "border-border text-text-muted" : STATUS_CLASS[detail.status])}>{detail.selection_skip ? skipLabel(detail.selection_skip) : localVideo ? '本地视频' : STATUS_LABEL[detail.status]}</span>
            <DistributionSummary rows={detail.distributions?.filter(d => d.version === detail.current_version)} publications={currentPublications(detail)} />
            {!localVideo && <CredibilityBadge value={detail.credibility} />}
            {detail.auto ? <span className="badge border border-border text-text-muted">自动流水线</span> : null}
            {detail.topic.content_mode === "episode" ? <span className="badge border border-accent text-accent">整期节目</span> : null}
            {detail.column ? <span className="text-xs text-text-muted">{detail.column.name}</span> : null}
            {detail.content_context?.column_name ? <Link className="text-xs text-accent hover:underline"
              to={`/matrices/${detail.content_context.matrix}?tab=columns`}>栏目：{detail.content_context.column_name}</Link> : null}
            {!localVideo && detail.approved_version ? (
              <span className="text-xs text-text-muted">{detail.approved_version === detail.current_version ? "定稿" : "旧版定稿"} v{detail.approved_version}</span>
            ) : null}
          </div>
          {editing ? (
            <input aria-label="文稿标题" className="input text-lg font-semibold" value={draftTitle} onChange={(e) => setDraftTitle(e.target.value)} />
          ) : (
            <h1 className="text-lg font-semibold">{shown?.title?.trim() || detail.title?.trim() || detail.topic.title?.trim() || "视频文稿"}</h1>
          )}
          {!localVideo && detail.credibility?.reasons.length && detail.credibility.tier !== "trusted" ? (
            <p className="text-xs text-text-muted mt-1">可信度提示：{detail.credibility.reasons.join("；")}</p>
          ) : null}
          <p className="text-xs text-text-muted mt-1">
            <TimeStamp value={detail.created_at} label="创建" className="mr-3" />
            {!localVideo && <>选题：{detail.topic.title}</>}
            <Link className="ml-3 text-accent hover:underline" to={`/ask?video=${encodeURIComponent(detail.id)}`}>
              在助手中处理
            </Link>
            {canEdit && !["RESEARCHING", "WRITING"].includes(detail.status) ? (
              <button className="ml-3 text-accent hover:underline" disabled={editing || busy !== null} onClick={archive}>
                {detail.archived ? "取消归档" : "归档"}
              </button>
            ) : null}
          </p>
        </div>
      </div>

      {shown && !localVideo ? <div className="sticky top-0 z-20 card bg-bg-card p-3 flex flex-wrap items-center gap-2 shadow-lg" aria-label="文稿操作">
        <span className="text-xs flex-1" role="status">{editing ? editor.dirty ? `v${shown.version} · 有未保存修改${editor.storageError ? "（本地恢复不可用，请先保存）" : " · 已暂存到本机"}` : "尚无修改，不会创建新版本" : isCurrent ? `当前文稿 v${shown.version}${editor.recovery ? " · 本机有未保存修改" : ""}` : `历史 v${shown.version} · 只读`}</span>
        {editing ? <>
          <button className="btn btn-primary text-xs" onClick={save} disabled={!editor.dirty || busy !== null}>{busy === "save" ? "保存中..." : "保存为新版本"}</button>
          <button className="btn text-xs" onClick={editor.stop} disabled={busy !== null}>退出编辑</button>
        </> : writable && ["DRAFT", "APPROVED"].includes(detail.status) ? <>
          <button className={`btn text-xs ${editor.recovery ? "btn-primary" : ""}`} onClick={startEdit} disabled={busy !== null}>{editor.recovery ? "恢复编辑" : "编辑"}</button>
          {detail.status !== "APPROVED" || detail.approved_version !== shown.version ? <button className="btn btn-primary text-xs" onClick={approve} disabled={busy !== null || Boolean(editor.recovery)} title={editor.recovery ? "先恢复并保存，或放弃本地修改，再定稿保存版" : undefined}>{busy === "approve" ? "定稿中..." : `定稿 v${shown.version}`}</button>
            : tab !== "video" ? <button className="btn btn-primary text-xs" onClick={() => goTab("video")}>{detail.renders.some(r => r.version === shown.version && RENDER_ACTIVE.includes(r.status)) ? "查看进度" : renderForVersion(detail, shown.version)?.status === "COMPLETED" ? "查看成片" : "准备出片"}</button> : <span className="text-xs text-text-muted">已定稿 v{shown.version} · 出片设置与进度见下方</span>}
        </> : null}
      </div> : null}
      {operationError ? <p role="alert" className="card p-3 text-sm text-danger">{operationError}</p> : null}
      {editor.recovery && !editing ? <section className="card p-3 text-xs space-y-2" aria-label="本地未保存草稿">
        <p>本机保留了一份基于 v{shown?.version} 的未保存修改。{writable ? "点击「恢复编辑」可继续，保存后才会生成新版本。" : "当前为只读状态，可展开文字与当前稿对照。"}</p>
        <details><summary className="cursor-pointer text-accent">查看本地暂存文字</summary><div className="mt-2 whitespace-pre-wrap max-h-72 overflow-auto">{editor.recovery.title}{"\n\n"}{editor.recovery.sections.map(s => s.title + "\n" + s.paragraphs.map(p => p.text).join("\n")).join("\n\n")}</div></details>
        {canEdit ? <button className="text-text-muted hover:underline" onClick={editor.discard}>放弃本地修改</button> : null}
      </section> : null}

      {localVideo ? <section className="card p-4 space-y-2" aria-label="本地导入视频"><h2 className="font-medium">本地导入视频</h2>
        <p className="text-sm">{localVideo.file_name}</p><p className="text-xs text-text-muted">{localVideo.width} × {localVideo.height} · {Math.round(localVideo.duration)} 秒 · 已保存成片副本，可直接准备发布信息和投稿。</p>
      </section> : shown ? <ScriptIteration detail={detail} canEdit={canEdit} canManageModels={user?.role === "admin"} isCurrent={isCurrent} editing={editing} busy={busy}
        onRewrite={rewrite} onVideo={(renderId) => setSearch(prev => { const next = new URLSearchParams(prev); next.set("tab", "video"); if (renderId) next.set("render", renderId); else next.delete("render"); return next; })} onCompare={() => goTab('compare')} /> : null}

      <section
        aria-label="下一步"
        className={cn(
          "card p-4 text-sm flex flex-wrap items-center gap-x-3 gap-y-2",
          step.tone === "bad" && "border-danger/40",
          step.tone === "todo" && "border-accent",
        )}
      >
        <span className="text-text-muted">下一步</span>
        <span className="flex-1 min-w-0">{editing ? "正在编辑；本机暂存不等于已保存，保存后再定稿。" : step.text}</span>
        {!editing && staged && step.tab !== tab ? (
          <button className="btn text-xs" onClick={() => goTab(step.tab)}>
            去「{TAB_LABEL[step.tab]}」
          </button>
        ) : null}
      </section>

      {shown ? <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-text-muted">{isCurrent ? "当前版本" : "历史版本（只读）"}</span><b>v{shown.version}</b>
        {detail.versions.length > 1 ? <select aria-label="查看文稿版本" className="input w-auto text-xs py-1" value={shown.version} disabled={editing} onChange={(e) => setSearch(e.target.value === String(detail.current_version) ? {} : { version: e.target.value })}>{detail.versions.map((v) => <option key={v.version} value={v.version}>v{v.version}{v.version === detail.current_version ? " · 当前" : ""}{v.version === detail.approved_version ? " · 已定稿" : ""}</option>)}</select> : null}
        {!isCurrent ? <button className="text-accent hover:underline" onClick={() => setSearch({})}>返回当前 v{detail.current_version}</button> : null}
        {reportPath ? <Link className="ml-auto text-accent hover:underline" to={`/reports/view?path=${encodeURIComponent(reportPath)}`}>源研究报告 ↗</Link> : null}
      </div> : null}

      {!localVideo && detail.steps?.length ? <details className="text-xs" open={tab === "review" && !editing}>
        <summary className="cursor-pointer text-text-muted">生产步骤与按需审校</summary>
        <div className="mt-2"><ScriptSteps detail={detail} canEdit={writable && !editing && busy === null} onChanged={setDetail} showRewrite={false} onBusyChange={working => { actionLock.current = working; setBusy(working ? "audit" : null); }} /></div>
      </details> : null}

      {detail.pending_plan ? (
        <PlanReview key={detail.pending_plan.id} detail={detail} canEdit={canEdit} onChanged={setDetail} />
      ) : null}

      {detail.topic.content_mode === "episode" && shown && tab === "draft" && !editing ? <EpisodeOutline sections={shown.sections} /> : null}
      {tabs.length ? (
        <div role="tablist" className="flex gap-1 border-b border-border">
          {tabs.map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={t === tab}
              disabled={editing}
              onClick={() => goTab(t)}
              className={cn(
                "px-3 py-1.5 text-sm -mb-px border-b-2",
                t === tab ? "border-accent text-text font-medium" : "border-transparent text-text-muted hover:text-text",
              )}
            >
              {TAB_LABEL[t]}
            </button>
          ))}
        </div>
      ) : null}

      {["RESEARCHING", "WRITING"].includes(detail.status) ? (
        <section className="card p-4 text-sm space-y-1">
          <p>
            {detail.status === "RESEARCHING"
              ? "研究 agent 正在检索并撰写带引用的报告，通常需要 3–10 分钟。"
              : detail.current_version
                ? "正在用新风格重写，之前的版本都还在。"
                : "报告已完成，正在写成口播文稿。"}
            {detail.status === "WRITING" && detail.stage ? (
              <span className="text-text-muted">（{detail.stage}：拆事实 → 定方案 → 写稿 → 审校 → 修改）</span>
            ) : null}
          </p>
          {detail.research_job ? (
            <Link to={`/jobs/${encodeURIComponent(detail.research_job)}`} className="text-xs text-accent hover:underline">
              查看研究任务的实时日志 →
            </Link>
          ) : null}
        </section>
      ) : null}

      {detail.selection_skip ? <section className="card p-4" aria-label="选题跳过说明">
        <SelectionSkipLinks skip={detail.selection_skip} back={backToTopics} />
      </section> : null}

      {detail.error && detail.status !== "FAILED" && !detail.selection_skip ? (
        <section className="card border-warning/40 text-sm text-warning">{detail.error}</section>
      ) : null}

      {detail.status === "FAILED" && !detail.selection_skip ? (
        <section className="card border-danger/40 text-sm space-y-1">
          <p className="text-danger font-medium">没有完成</p>
          <p className="text-text-muted">{detail.error}</p>
        </section>
      ) : null}

      {check && tab === "draft" ? (
        <section className="card p-4 text-sm flex flex-wrap items-center gap-x-6 gap-y-2">
          <span>
            约 <b>{minutes(check.estimated_seconds)}</b>（{check.spoken_length} 字）
          </span>
          {check.target_minutes && shown?.checks?.mode !== "on_request" ? (
            <span className="text-text-muted">
              配方参考 {check.target_minutes} 分钟（约 {check.budget} 朗读单位；质量优先，可浮动）
            </span>
          ) : null}
          {check.status && shown?.checks?.mode !== "on_request" ? (
            <span className={LENGTH[check.status].cls}>
              {LENGTH[check.status].label}
              {check.ratio ? `（${deviation(check.ratio)}）` : ""}
            </span>
          ) : null}
          {check.dropped_citations ? (
            <span className="text-warning">去掉了 {check.dropped_citations} 个不可信引用</span>
          ) : null}
          {reportPath ? (
            <Link to={`/reports/view?path=${encodeURIComponent(reportPath)}`} className="text-xs text-accent hover:underline">
              源研究报告 →
            </Link>
          ) : null}
          {problems.map((p) => (
            <span key={p} className="w-full text-warning text-xs">
              {p}
            </span>
          ))}
        </section>
      ) : null}

      {!localVideo && shown && !editing && (tab === "draft" || tab === "video") ? <ScriptScreenshots key={`${detail.id}-${shown.version}-${renderForVersion(detail, shown.version)?.id ?? ""}`} id={detail.id} version={shown.version} canEdit={writable && ["DRAFT", "APPROVED"].includes(detail.status)} /> : null}
      {detail.topic.article && tab === "draft" ? <ArticleSource key={detail.id} id={detail.id} mode={detail.topic.article.mode} /> : null}
      {detail.report_id && tab === "draft" ? (
        <details className="card" onToggle={(e) => setReuseOpen(e.currentTarget.open)}>
          <summary className="p-3 cursor-pointer text-xs text-text-muted">沿用这份研究，查看其他文稿或另写一篇</summary>
          {reuseOpen ? <div className="px-3 pb-3"><ReportReuse scriptId={detail.id} reportId={detail.report_id} canEdit={writable && !editing && busy === null} /></div> : null}
        </details>
      ) : null}

      {shown && !detail.topic.article && tab === "draft" ? (
        <StylePanel
          key={`${shown.version}-${catalog ? "ready" : "loading"}`}
          detail={detail}
          catalog={catalog}
          canRestyle={writable && !editing && ["DRAFT", "APPROVED"].includes(detail.status) && Boolean(detail.report_path) && detail.column?.writer === "adapt"}
          busy={busy === "restyle"}
          onRestyle={restyle}
        />
      ) : null}
      {shown?.checks && !editing && tab === "review" ? (
        detail.status === "APPROVED" ? (
          <details className="text-sm">
            <summary className="cursor-pointer text-text-muted">
              审校记录（已定稿，{shown.checks.mode === "on_request" ? "未进行自动审校" : shown.checks.summary.passed ? "当时通过" : `当时有 ${shown.checks.summary.serious} 处必须改，已由你决定`}）
            </summary>
            <div className="mt-2">
              <ChecksPanel checks={shown.checks} onLocate={locateIssue} />
            </div>
          </details>
        ) : (
          <ChecksPanel checks={shown.checks} onLocate={locateIssue} />
        )
      ) : null}
      {shown?.meta?.plan && !editing && tab === "review" ? <PlanPanel plan={shown.meta.plan} /> : null}
      {shown?.meta?.description && !editing && tab === "review" ? <DescriptionPanel description={shown.meta.description} /> : null}

      {tab === "draft" ? <div className={editing && shown?.checks?.issues.length ? "grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]" : ""}>
        <div className="space-y-5">
      {sections.map((section, si) => (
        <section key={si} id={`script-section-${si}`} className="card p-5 space-y-3 scroll-mt-24">
          <div className="flex items-baseline gap-3">
            <span className="font-mono text-xs text-accent">{String(si + 1).padStart(2, "0")}</span>
            {editing ? (
              <input
                aria-label={`第 ${si + 1} 节标题`} className="input flex-1"
                value={section.title}
                onChange={(e) =>
                  setDraftSections((secs) => secs.map((s, i) => (i === si ? { ...s, title: e.target.value } : s)))
                }
              />
            ) : (
              <h2 className="font-medium flex-1">{section.title}</h2>
            )}
            {check?.sections[si] ? (
              <span className="text-xs text-text-muted">
                {check.sections[si].spoken_length}
                {check.sections[si].budget && shown?.checks?.mode !== "on_request" ? ` / ${check.sections[si].budget}` : ""} 字
              </span>
            ) : null}
          </div>
          {!editing ? <IssueList checks={shown?.checks} si={si} pi={null} /> : null}
          {section.paragraphs.map((p, pi) => (
            <div key={pi} id={`script-section-${si}-paragraph-${pi}`} className="space-y-1 scroll-mt-24">
              {editing ? (
                <textarea
                  aria-label={`第 ${si + 1} 节第 ${pi + 1} 段正文`}
                  className="input w-full leading-relaxed"
                  rows={Math.max(3, Math.ceil(p.text.length / 60))}
                  value={p.text}
                  onChange={(e) => setParagraph(si, pi, e.target.value)}
                />
              ) : (
                <p className="text-sm leading-relaxed">{p.text}</p>
              )}
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                {!detail.topic.article || p.evidence !== "UNCITED" ? <span className={cn("badge border", EVIDENCE[p.evidence]?.cls)}>{EVIDENCE[p.evidence]?.label ?? p.evidence}</span> : null}
                {p.citations.map((url) => (
                  <a key={url} href={url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                    {host(url)}
                  </a>
                ))}
                <FactChips ids={p.facts} facts={facts} />
                {p.recap ? <span className="badge border border-border">回顾</span> : null}
                {p.note ? <span className="text-warning">{p.note}</span> : null}
              </div>
              {!editing ? <IssueList checks={shown?.checks} si={si} pi={pi} /> : null}
            </div>
          ))}
        </section>
      ))}

        </div>
        {editing && shown?.checks?.issues.length ? <ReviewNotes checks={shown.checks} version={shown.version} onLocate={locateIssue} /> : null}
      </div> : null}

      {tab === "publish" ? (
        <section className="card p-4 space-y-2" aria-label="发布版本">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="font-medium">发布版本</h2>
            {publishVersions.length ? <select className="input w-auto text-sm" aria-label="选择发布版本" value={publishingVersion} onChange={(e) => { setPublishVersion(Number(e.target.value)); setSearch(prev => { const next = new URLSearchParams(prev); next.set("publish_version", e.target.value); next.delete("render"); return next; }); }}>
              {publishVersions.map((v) => <option key={v} value={v}>文稿 v{v} · {publishedPlatformCount(detail.distributions?.filter(d => d.version === v), (detail.publications ?? []).filter(row => row.version === v)) ? `已发布至 ${publishedPlatformCount(detail.distributions?.filter(d => d.version === v), (detail.publications ?? []).filter(row => row.version === v))} 个平台` : UPLOAD_LABEL[detail.platform_uploads?.find(task => task.version === v)?.status ?? ""] || "成片完成"}</option>)}
            </select> : <span className="text-sm">文稿 v{publishingVersion} · 尚未出片</span>}
          </div>
          {requestedRender && !publishingRender ? <p role="alert" className="text-warning text-sm">所选成片与此版本不匹配或已不可用，请明确选择另一条成片。</p> : null}
          {completed.filter(r => r.version === publishingVersion).length ? <select aria-label="选择具体成片" className="input w-full text-xs" value={publishingRender?.id ?? ""} onChange={e => setSearch(prev => { const next = new URLSearchParams(prev); next.set("render", e.target.value); return next; })}><option value="" disabled>选择成片</option>{completed.filter(r => r.version === publishingVersion).map(r => <option key={r.id} value={r.id}>v{r.version} · {r.id} · {r.completed_at || r.created_at}</option>)}</select> : null}
          {publishingRender?.files.find(f => f.name === "video.mp4") ? <video key={publishingRender.id} controls preload="metadata" className="w-full max-h-[60vh] bg-black rounded" src={publishingRender.files.find(f => f.name === "video.mp4")!.url} aria-label="本次发布成片" /> : null}
          <p className="text-sm">{publishingRender?.title ?? shown?.title}</p>
          <p className="text-xs text-text-muted">标题、简介和发布记录对应文稿 v{publishingVersion}。平台投稿公开后会自动回填，也可补录手动发布的视频。</p>
          {publishingVersion !== detail.current_version ? <p className="text-xs text-warning">这是旧版 v{publishingVersion} 的成片和发布包；当前文稿 v{detail.current_version} 尚未用于此视频。</p> : null}
        </section>
      ) : null}

      {tab === "publish" && publishingVersion && (!requestedRender || publishingRender) ? <PublicationFlow scriptId={detail.id} version={publishingVersion} render={publishingRender} publications={publishingVersion === detail.current_version ? currentPublications(detail).map(row => ({ ...row, version: publishingVersion })) : detail.publications ?? []} upload={publishingUpload} distributions={detail.distributions} onChanged={load} canEdit={canEdit} /> : null}

      {tab === "publish" && publishingRender ? (
        <RenderFolder render={publishingRender} canEdit={canEdit} />
      ) : null}

      {tab === "publish" && (!requestedRender || publishingRender) ? (
        <PublishPanel
          key={`${detail.id}:${publishingVersion}:${publishingRender?.id ?? ""}`}
          scriptId={detail.id}
          version={publishingVersion}
          renderId={publishingRender?.id}
          refreshKey={publishingRender?.status}
          canEdit={canEdit}
          recheckEnabled={Boolean(detail.column?.recheck) && detail.status === "APPROVED" && !detail.stage && publishingVersion === detail.approved_version}
          publications={detail.publications}
          distributions={detail.distributions}
          rechecks={detail.rechecks}
          onChanged={load}
        />
      ) : null}

      {tab === 'compare' && <VideoComparisonPanel detail={detail} />}
      {(detail.status === "APPROVED" || detail.renders.length) && tab === "video" ? (
        <RenderPanel
          key={`${shown?.version ?? detail.current_version}:${search.get('render') || ''}`}
          initialRenderId={search.get('render') || ''}
          detail={detail}
          autovideo={autovideo}
          voice={voice}
          setVoice={setVoice}
          productionMode={productionMode}
          setProductionMode={setProductionMode}
          statusError={renderStatusError}
          onStatusRetry={() => setStatusTick(n => n + 1)}
          canRender={writable && !editor.recovery && detail.status === "APPROVED"}
          busy={busy !== null}
          onRender={render}
          onPublish={(version, renderId) => { setPublishVersion(version); setSearch(prev => { const next = new URLSearchParams(prev); next.set("tab", "publish"); next.set("publish_version", String(version)); next.set("render", renderId); return next; }); }}
        />
      ) : null}
    </div>
  );
}

function RenderPanel({
  initialRenderId,
  detail,
  autovideo,
  voice,
  setVoice,
  productionMode,
  setProductionMode,
  statusError,
  onStatusRetry,
  canRender,
  busy,
  onRender,
  onPublish,
}: {
  initialRenderId: string;
  detail: VideoScriptDetail;
  autovideo: AutoVideoStatus | null;
  voice: string;
  setVoice: (v: string) => void;
  productionMode: "" | "standard" | "fast";
  setProductionMode: (mode: "" | "standard" | "fast") => void;
  statusError: boolean;
  onStatusRetry: () => void;
  canRender: boolean;
  busy: boolean;
  onRender: () => void;
  onPublish: (version: number, renderId: string) => void;
}) {
  const [selectedId, setSelectedId] = useState(initialRenderId);
  const current = renderForVersion(detail, detail.version_shown?.version ?? detail.current_version);
  const preparingCurrent = !current && detail.status === "APPROVED" && detail.approved_version === detail.current_version;
  const latest: VideoRender | null = detail.renders.find((r) => r.id === selectedId) ?? current ?? (preparingCurrent ? null : detail.renders[0]) ?? null;
  const historical = latest != null && latest.version !== detail.current_version;
  const active = latest != null && RENDER_ACTIVE.includes(latest.status);
  const video = latest?.status === "COMPLETED" ? latest.files.find((f) => f.name === "video.mp4") : undefined;
  const notReady = !detail.topic.local_video && autovideo != null && (!autovideo.reachable || !autovideo.ready);
  const anyActive = detail.renders.some(r => RENDER_ACTIVE.includes(r.status));
  const voiceLabel = voice ? autovideo?.voices.find(v => v.id === voice)?.label || voice : "任务与栏目默认配音";
  const settingsLinks = detail.configuration?.links.filter(l => ["出片形式与配音", "片头片尾与包装", "任务的视频生成设置", "矩阵的共用视频设置"].includes(l.label)) ?? [];

  return (
    <section className="card p-5 space-y-3 text-sm" aria-label="成片与制作记录">
      <div className="flex items-center gap-2">
        <h2 className="font-medium flex-1">成片</h2>
        {latest ? <span className="badge border border-border">{RENDER_LABEL[latest.status]}</span> : null}
        {latest ? <TimeStamp value={latest.completed_at ?? latest.created_at} label={latest.completed_at ? "完成" : "提交"} /> : null}
      </div>
      {detail.renders.length ? <div className="flex flex-wrap items-center gap-2 text-xs"><select aria-label="查看成片记录" className="input w-auto max-w-full text-xs" value={latest?.id ?? ""} onChange={(e) => setSelectedId(e.target.value)}>{preparingCurrent ? <option value="">当前定稿 v{detail.current_version} · 准备出片</option> : null}{detail.renders.map((r) => <option key={r.id} value={r.id}>v{r.version} · {RENDER_LABEL[r.status]} · {r.created_at.replace("T", " ").slice(0,16)}</option>)}</select>{historical ? <span className="text-warning">旧版 v{latest.version} 成片，当前文稿为 v{detail.current_version}</span> : <span className="text-text-muted">当前文稿 v{detail.current_version}</span>}</div> : null}
      {notReady ? (
        <p className="text-warning text-xs">
          {autovideo?.reachable
            ? `AutoVideo 生成依赖未就绪：${autovideo.blockers.join("、")}`
            : `连不上 AutoVideo（${autovideo?.base_url}），请先启动它。`}
        </p>
      ) : null}

      {video && latest ? (
        <div className="space-y-2">
          <EpisodePlayer render={latest} episode={detail.topic.content_mode === "episode"} />
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <a href={video.url} download={video.name} className="text-accent hover:underline">
              下载成片
            </a>
            <button className="text-accent hover:underline" onClick={() => onPublish(latest.version, latest.id)}>发布 v{latest.version}：查看发布包与记录链接</button>
            {latest.files.some((f) => f.name === "captions.srt") ? (
              <a href={latest.files.find((f) => f.name === "captions.srt")!.url} download="captions.srt" className="text-accent hover:underline">
                字幕
              </a>
            ) : null}
          </div>
          <details className="text-xs">
            <summary className="cursor-pointer text-text-muted">全部文件（{latest.files.length}）：规划、QA、日志等</summary>
            <div className="flex flex-wrap gap-3 mt-2">
              {latest.files.map((f) => (
                <a key={f.name} href={f.url} download={f.name} className="text-accent hover:underline">
                  {f.name}
                </a>
              ))}
            </div>
          </details>
        </div>
      ) : null}

      {!detail.topic.local_video && latest?.status === "COMPLETED" && latest.timings?.elapsed_seconds != null ? (
        <p className="text-xs text-text-muted">
          总耗时 {Math.round(latest.timings.elapsed_seconds)} 秒
          {latest.timings.download_seconds != null ? ` · 文件回传 ${latest.timings.download_seconds.toFixed(2)} 秒` : ""}
        </p>
      ) : null}

      {latest && active ? (
        <p className="text-text-muted">
          {RENDER_LABEL[latest.status]}
          {latest.active_stage ? ` · ${renderPhase(latest.active_stage)}` : ""} · 定稿 v{latest.version}，{latest.source_chars} 字。
          正在后台制作，完成后会自动回传，可以离开此页。
          {latest.updated_at ? <TimeStamp value={latest.updated_at} label="最近同步" className="block mt-1 text-xs" /> : null}
        </p>
      ) : null}
      {latest?.visuals ? <p className="text-xs text-text-muted">配图：{latest.visuals.reason}</p> : null}
      {latest && (latest.status === "FAILED" || latest.status === "CANCELLED") ? (
        <p className="text-danger">
          {latest.error_code ? <span className="font-mono text-xs mr-2">{latest.error_code}</span> : null}
          {latest.error_message}
        </p>
      ) : null}

      {canRender ? <details className="border-t border-border pt-3" open={preparingCurrent || (!video && !active)}>
        <summary className="cursor-pointer text-accent text-sm">本次出片设置（仅影响新尝试）</summary>
        <div className="space-y-3 mt-3" aria-label="本次出片设置">
          <p className="text-xs">使用定稿 <b>v{detail.approved_version}</b> · {voiceLabel} · {productionMode === "fast" ? "快速卡片" : productionMode === "standard" ? "标准制作" : "沿用默认制作档位"}</p>
          <div className="flex flex-wrap gap-3">
          <label className="text-xs">配音声音<select className="input text-xs py-1 w-auto block mt-1" value={voice} disabled={busy || anyActive} onChange={(e) => setVoice(e.target.value)} aria-label="配音声音">
            <option value="">沿用任务与栏目默认</option>
            {(autovideo?.voices ?? []).map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select></label>
          <label className="text-xs">制作档位<select className="input text-xs block mt-1" aria-label="本次制作档位" value={productionMode} disabled={busy || anyActive} onChange={e => setProductionMode(e.target.value as "" | "standard" | "fast")}>
            <option value="">沿用任务与栏目默认</option><option value="standard">标准制作</option><option value="fast">快速卡片</option>
          </select></label>
          </div>
          <p className="text-xs text-text-muted">快速卡片使用浅色信息卡片；标准制作按制作配方组织画面。包装沿用任务与栏目设置。此处选择不会修改共用配置，也不会更新已有成片。</p>
          {settingsLinks.length || detail.column?.production_recipe ? <details className="text-xs"><summary className="cursor-pointer text-text-muted">查看默认配置来源</summary>
            {detail.column?.production_recipe ? <p className="mt-2">栏目制作配方：{detail.column.production_recipe}（任务可覆盖）</p> : null}
            {settingsLinks.map(l => <Link key={l.href} className="block text-accent mt-2" to={`${l.href}${l.href.includes("?") ? "&" : "?"}from=${detail.id}`}>{l.label}：{l.name}</Link>)}
          </details> : null}
          {!autovideo && !statusError ? <p role="status" className="text-xs text-text-muted">正在读取出片服务状态…</p> : null}
          {statusError ? <p role="alert" className="text-xs text-warning">出片服务状态读取失败，尚未提交任务。</p> : null}
          {statusError || notReady ? <button className="btn text-xs" onClick={onStatusRetry}>重新读取服务状态</button> : null}
          {anyActive ? <p className="text-xs text-text-muted">已有出片任务进行中，完成后可提交新的尝试。</p> : null}
          <button className="btn btn-primary text-xs" onClick={onRender} disabled={busy || anyActive || !autovideo || notReady || statusError}>
            {busy ? "提交中..." : current ? `用定稿 v${detail.approved_version} 重新出片` : `用定稿 v${detail.approved_version} 出片`}
          </button>
        </div>
      </details> : null}
      <div className="flex flex-wrap items-center gap-2">
        {latest?.autovideo_url ? (
          <a href={latest.autovideo_url} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline">
            在 AutoVideo 中查看 →
          </a>
        ) : null}
      </div>
    </section>
  );
}
