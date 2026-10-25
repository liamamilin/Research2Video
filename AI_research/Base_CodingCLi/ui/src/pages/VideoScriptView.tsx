import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
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
  type ScriptSection,
  type StyleCatalog,
  type VideoRender,
  type VideoScriptDetail,
} from "@/api/video";
import { CredibilityBadge } from "@/components/CredibilityBadge";
import { ReportReuse } from "@/components/ReportReuse";
import { PlanReview } from "@/components/PlanReview";
import { ScriptSteps } from "@/components/ScriptSteps";
import { ScriptIteration } from "@/components/ScriptIteration";
import { TimeStamp } from "@/components/TimeStamp";
import { ErrorState } from "@/components/ErrorState";
import { DistributionSummary } from "@/components/DistributionSummary";
import { publishedPlatformCount } from "@/lib/distributions";
import { PublicationFlow } from "@/components/PublicationFlow";
import { UPLOAD_LABEL } from "@/lib/platform-links";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage, useToast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { currentPublications, nextStep, renderForVersion, TAB_LABEL, type ScriptTab } from "./videoNext";
import { ChecksPanel, DescriptionPanel, FactChips, IssueList, PlanPanel, StylePanel } from "./VideoAdaptation";
import { PublishPanel, RenderFolder } from "./VideoPublish";
import { STATUS_CLASS, STATUS_LABEL, minutes } from "./VideoScripts";

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
  const [search, setSearch] = useSearchParams();
  const versionParam = Number(search.get("version")) || undefined;
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";
  const toast = useToast();

  const [detail, setDetail] = useState<VideoScriptDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [reuseOpen, setReuseOpen] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftSections, setDraftSections] = useState<ScriptSection[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [autovideo, setAutovideo] = useState<AutoVideoStatus | null>(null);
  const [voice, setVoice] = useState("");
  const [catalog, setCatalog] = useState<StyleCatalog | null>(null);
  const [facts, setFacts] = useState<Record<string, Fact>>({});
  const [publishVersion, setPublishVersion] = useState<number | null>(null);

  const flight = useRef<AbortController | null>(null);
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
    setDetail(null); setError(null); setEditing(false); setPublishVersion(null); void load();
    return () => { flight.current?.abort(); flight.current = null; };
  }, [load]);

  const busyServer = detail && (["RESEARCHING", "WRITING"].includes(detail.status) || Boolean(detail.stage) || detail.renders.some((r) => RENDER_ACTIVE.includes(r.status)) || detail.distributions?.some(row => ["QUEUED", "UPLOADING", "RUNNING", "SUBMITTED"].includes(row.task?.status ?? "") || (["kuaishou","xiaohongshu"].includes(row.platform) && row.task?.status === "UNKNOWN")));
  useEffect(() => {
    if (editing) return;
    const refresh = () => { if (!document.hidden) void load(); };
    const timer = busyServer ? window.setInterval(refresh, 5000) : undefined;
    document.addEventListener("visibilitychange", refresh);
    return () => { if (timer) clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [busyServer, editing, load]);

  useEffect(() => {
    if (!canEdit || detail?.column?.writer !== "adapt" || catalog) return;
    const controller = new AbortController();
    listVideoStyles(controller.signal).then((value) => { if (!controller.signal.aborted) setCatalog(value); }).catch(() => undefined);
    return () => controller.abort();
  }, [canEdit, detail?.column?.writer, catalog]);

  // The fact sheet behind the version on screen, for the fact labels' tooltips.
  const sheetId = detail?.version_shown?.meta?.fact_sheet_id;
  const shownVersion = detail?.version_shown?.version;
  useEffect(() => {
    if (!sheetId) {
      setFacts({});
      return;
    }
    const controller = new AbortController(); setFacts({});
    getVideoFacts(id, shownVersion, controller.signal)
      .then((sheet) => { if (!controller.signal.aborted) setFacts(Object.fromEntries(sheet.facts.map((f) => [f.id, f]))); })
      .catch(() => { if (!controller.signal.aborted) setFacts({}); });
    return () => controller.abort();
  }, [id, sheetId, shownVersion]);

  const needsRenderStatus = detail?.status === "APPROVED" && (search.get("tab") === "video" || (!search.get("tab") && nextStep(detail).tab === "video"));
  useEffect(() => {
    if (!canEdit || !needsRenderStatus || autovideo) return;
    const controller = new AbortController();
    getAutoVideoStatus(controller.signal).then((value) => { if (!controller.signal.aborted) setAutovideo(value); }).catch(() => undefined);
    return () => controller.abort();
  }, [canEdit, needsRenderStatus, autovideo]);

  if (!detail) return <ErrorState error={error ?? undefined} empty="加载中..." onRetry={load} />;

  const shown = detail.version_shown;
  const isCurrent = shown?.version === detail.current_version;
  const writable = canEdit && isCurrent && !detail.stage;
  const check = shown?.length_check;
  const problems = shown?.meta?.problems ?? [];
  const step = isCurrent || !shown ? nextStep(detail) : { tab: "draft" as const, text: `正在查看历史 v${shown.version}。返回当前 v${detail.current_version} 后可继续审校、编辑与定稿。`, tone: "wait" };
  const staged = ["DRAFT", "APPROVED"].includes(detail.status);
  const completed = detail.renders.filter((r) => r.status === "COMPLETED");
  const publishVersions = [...new Set(completed.map((r) => r.version))];
  const publishingVersion = publishVersions.find((v) => v === (publishVersion ?? Number(search.get("publish_version"))))
    ?? publishVersions.find((v) => v === shown?.version) ?? publishVersions[0] ?? shown?.version;
  const publishingRender = completed.find((r) => r.version === publishingVersion && r.files?.some(file => file.name === "video.mp4")) ?? completed.find((r) => r.version === publishingVersion);
  const publishingUpload = detail.platform_uploads?.find(task => task.version === publishingVersion);

  const tabs: ScriptTab[] = !staged && !detail.renders.length && !detail.publications?.length
    ? []
    : detail.status === "APPROVED" || completed.length || detail.publications?.length
      ? ["draft", "review", "video", "publish"]
      : detail.renders.length
        ? ["draft", "review", "video"]
        : ["draft", "review"];
  const asked = search.get("tab") as ScriptTab | null;
  const tab: ScriptTab = editing || !tabs.length ? "draft" : asked && tabs.includes(asked) ? asked : step.tab;
  const goTab = (t: ScriptTab) =>
    setSearch((prev) => {
      const next = new URLSearchParams(prev);
      next.set("tab", t);
      return next;
    });

  function startEdit() {
    if (!shown || !writable) return;
    setDraftTitle(shown.title);
    setDraftSections(structuredClone(shown.sections));
    setEditing(true);
  }

  async function save() {
    setBusy("save");
    try {
      const saved = await saveVideoScript(id, { title: draftTitle, sections: draftSections, base_version: shown?.version });
      setDetail(saved);
      setEditing(false);
      setSearch({});
      toast.success(`已保存为 v${saved.version_shown?.version}`);
    } catch (e) {
      toast.error("保存失败", errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function archive() {
    setBusy("archive");
    try {
      setDetail(await archiveVideoScript(id, !detail?.archived));
      toast.success(detail?.archived ? "已取消归档" : "已归档，列表和看板里不再显示");
    } catch (e) {
      toast.error("归档失败", errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function approve() {
    setBusy("approve");
    try {
      setDetail(await approveVideoScript(id, shown?.version));
      toast.success(`已定稿 v${shown?.version}`);
    } catch (e) {
      toast.error("定稿失败", errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function render(mode?: "fast") {
    setBusy("render");
    try {
      const made = mode ? await renderVideoScript(id, voice || undefined, mode) : await renderVideoScript(id, voice || undefined);
      setDetail((d) => (d ? { ...d, renders: [made, ...d.renders] } : d));
      goTab("video");
      toast.success("已交给 AutoVideo", "正在后台制作，完成后可在这里查看");
    } catch (e) {
      toast.error("出片没有开始", errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function rewrite() {
    if (!window.confirm("用同一份研究报告，按当前配方重新生成文稿并审校？将保存为新版本，旧稿和旧视频保留。新稿需要重新定稿后再出片。")) return;
    setBusy("rewrite");
    try {
      setDetail(await rerunVideoStep(id, "write"));
      setSearch({ tab: "draft" });
      toast.success("已开始重新生成文稿", "沿用原报告，完成后可查看新版本");
    } catch (e) { toast.error("没有开始重新生成", errorMessage(e)); }
    finally { setBusy(null); }
  }

  async function restyle(depth: string, tone: string) {
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
      <Link to="/video" className="text-xs text-accent hover:underline">
        ← 视频文稿
      </Link>

      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className={cn("badge border", STATUS_CLASS[detail.status])}>{STATUS_LABEL[detail.status]}</span>
            <DistributionSummary rows={detail.distributions?.filter(d => d.version === detail.current_version)} publications={currentPublications(detail)} />
            <CredibilityBadge value={detail.credibility} />
            {detail.auto ? <span className="badge border border-border text-text-muted">自动流水线</span> : null}
            {detail.column ? <span className="text-xs text-text-muted">{detail.column.name}</span> : null}
            {detail.approved_version ? (
              <span className="text-xs text-text-muted">{detail.approved_version === detail.current_version ? "定稿" : "旧版定稿"} v{detail.approved_version}</span>
            ) : null}
          </div>
          {editing ? (
            <input className="input text-lg font-semibold" value={draftTitle} onChange={(e) => setDraftTitle(e.target.value)} />
          ) : (
            <h1 className="text-lg font-semibold">{shown?.title ?? detail.title}</h1>
          )}
          {detail.credibility && detail.credibility.tier !== "trusted" ? (
            <p className="text-xs text-text-muted mt-1">可信度提示：{detail.credibility.reasons.join("；")}</p>
          ) : null}
          <p className="text-xs text-text-muted mt-1">
            <TimeStamp value={detail.created_at} label="创建" className="mr-3" />
            选题：{detail.topic.title}
            <Link className="ml-3 text-accent hover:underline" to={`/ask?video=${encodeURIComponent(detail.id)}`}>
              在助手中处理
            </Link>
            {canEdit && !["RESEARCHING", "WRITING"].includes(detail.status) ? (
              <button className="ml-3 text-accent hover:underline" disabled={busy !== null} onClick={archive}>
                {detail.archived ? "取消归档" : "归档"}
              </button>
            ) : null}
          </p>
        </div>
        {writable && shown && ["DRAFT", "APPROVED"].includes(detail.status) ? (
          <div className="flex gap-2">
            {editing ? (
              <>
                <button className="btn btn-primary text-xs" onClick={save} disabled={busy !== null}>
                  {busy === "save" ? "保存中..." : "保存为新版本"}
                </button>
                <button className="btn text-xs" onClick={() => setEditing(false)} disabled={busy !== null}>
                  取消
                </button>
              </>
            ) : (
              <>
                <button className="btn text-xs" onClick={startEdit} disabled={busy !== null}>
                  编辑
                </button>
                {detail.approved_version !== shown.version || detail.status !== "APPROVED" ? (
                  <button className="btn btn-primary text-xs" onClick={approve} disabled={busy !== null}>
                    {busy === "approve" ? "定稿中..." : `定稿 v${shown.version}`}
                  </button>
                ) : null}
              </>
            )}
          </div>
        ) : null}
      </div>

      {shown ? <ScriptIteration detail={detail} canEdit={canEdit} canManageModels={user?.role === "admin"} isCurrent={isCurrent} editing={editing} busy={busy}
        onRewrite={rewrite} onRender={() => render()} onVideo={() => goTab("video")} /> : null}

      <section
        aria-label="下一步"
        className={cn(
          "card p-4 text-sm flex flex-wrap items-center gap-x-3 gap-y-2",
          step.tone === "bad" && "border-danger/40",
          step.tone === "todo" && "border-accent",
        )}
      >
        <span className="text-text-muted">下一步</span>
        <span className="flex-1 min-w-0">{step.text}</span>
        {staged && step.tab !== tab ? (
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

      <ScriptSteps detail={detail} canEdit={writable} onChanged={setDetail} showRewrite={false} />

      {detail.pending_plan ? (
        <PlanReview key={detail.pending_plan.id} detail={detail} canEdit={canEdit} onChanged={setDetail} />
      ) : null}

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

      {detail.error && detail.status !== "FAILED" ? (
        <section className="card border-warning/40 text-sm text-warning">{detail.error}</section>
      ) : null}

      {detail.status === "FAILED" ? (
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

      {detail.report_id && tab === "draft" ? (
        <details className="card" onToggle={(e) => setReuseOpen(e.currentTarget.open)}>
          <summary className="p-3 cursor-pointer text-xs text-text-muted">沿用这份研究，查看其他文稿或另写一篇</summary>
          {reuseOpen ? <div className="px-3 pb-3"><ReportReuse scriptId={detail.id} reportId={detail.report_id} canEdit={writable} /></div> : null}
        </details>
      ) : null}

      {shown && tab === "draft" ? (
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
              <ChecksPanel checks={shown.checks} />
            </div>
          </details>
        ) : (
          <ChecksPanel checks={shown.checks} />
        )
      ) : null}
      {shown?.meta?.plan && !editing && tab === "review" ? <PlanPanel plan={shown.meta.plan} /> : null}
      {shown?.meta?.description && !editing && tab === "review" ? <DescriptionPanel description={shown.meta.description} /> : null}

      {(tab === "draft" ? sections : []).map((section, si) => (
        <section key={si} className="card p-5 space-y-3">
          <div className="flex items-baseline gap-3">
            <span className="font-mono text-xs text-accent">{String(si + 1).padStart(2, "0")}</span>
            {editing ? (
              <input
                className="input flex-1"
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
            <div key={pi} className="space-y-1">
              {editing ? (
                <textarea
                  className="input w-full leading-relaxed"
                  rows={Math.max(3, Math.ceil(p.text.length / 60))}
                  value={p.text}
                  onChange={(e) => setParagraph(si, pi, e.target.value)}
                />
              ) : (
                <p className="text-sm leading-relaxed">{p.text}</p>
              )}
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                <span className={cn("badge border", EVIDENCE[p.evidence]?.cls)}>{EVIDENCE[p.evidence]?.label ?? p.evidence}</span>
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

      {tab === "publish" ? (
        <section className="card p-4 space-y-2" aria-label="发布版本">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="font-medium">发布版本</h2>
            {publishVersions.length ? <select className="input w-auto text-sm" aria-label="选择发布版本" value={publishingVersion} onChange={(e) => setPublishVersion(Number(e.target.value))}>
              {publishVersions.map((v) => <option key={v} value={v}>文稿 v{v} · {publishedPlatformCount(detail.distributions?.filter(d => d.version === v), (detail.publications ?? []).filter(row => row.version === v)) ? `已发布至 ${publishedPlatformCount(detail.distributions?.filter(d => d.version === v), (detail.publications ?? []).filter(row => row.version === v))} 个平台` : UPLOAD_LABEL[detail.platform_uploads?.find(task => task.version === v)?.status ?? ""] || "成片完成"}</option>)}
            </select> : <span className="text-sm">文稿 v{publishingVersion} · 尚未出片</span>}
          </div>
          <p className="text-sm">{publishingRender?.title ?? shown?.title}</p>
          <p className="text-xs text-text-muted">标题、简介和发布记录对应文稿 v{publishingVersion}。平台投稿公开后会自动回填，也可补录手动发布的视频。</p>
          {publishingVersion !== detail.current_version ? <p className="text-xs text-warning">这是旧版 v{publishingVersion} 的成片和发布包；当前文稿 v{detail.current_version} 尚未用于此视频。</p> : null}
        </section>
      ) : null}

      {tab === "publish" && publishingVersion ? <PublicationFlow scriptId={detail.id} version={publishingVersion} render={publishingRender} publications={publishingVersion === detail.current_version ? currentPublications(detail).map(row => ({ ...row, version: publishingVersion })) : detail.publications ?? []} upload={publishingUpload} distributions={detail.distributions} onChanged={load} canEdit={canEdit} /> : null}

      {tab === "publish" && publishingRender ? (
        <RenderFolder render={publishingRender} canEdit={canEdit} />
      ) : null}

      {tab === "publish" ? (
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

      {(detail.status === "APPROVED" || detail.renders.length) && tab === "video" ? (
        <RenderPanel
          key={shown?.version ?? detail.current_version}
          detail={detail}
          autovideo={autovideo}
          voice={voice}
          setVoice={setVoice}
          canRender={writable && detail.status === "APPROVED"}
          busy={busy === "render"}
          onRender={render}
          onPublish={(version) => { setPublishVersion(version); goTab("publish"); }}
        />
      ) : null}
    </div>
  );
}

function RenderPanel({
  detail,
  autovideo,
  voice,
  setVoice,
  canRender,
  busy,
  onRender,
  onPublish,
}: {
  detail: VideoScriptDetail;
  autovideo: AutoVideoStatus | null;
  voice: string;
  setVoice: (v: string) => void;
  canRender: boolean;
  busy: boolean;
  onRender: (mode?: "fast") => void;
  onPublish: (version: number) => void;
}) {
  const [selectedId, setSelectedId] = useState("");
  const current = renderForVersion(detail, detail.version_shown?.version ?? detail.current_version);
  const latest: VideoRender | null = detail.renders.find((r) => r.id === selectedId) ?? current ?? detail.renders[0] ?? null;
  const historical = latest != null && latest.version !== detail.current_version;
  const active = latest != null && RENDER_ACTIVE.includes(latest.status);
  const video = latest?.status === "COMPLETED" ? latest.files.find((f) => f.name === "video.mp4") : undefined;
  const notReady = autovideo != null && (!autovideo.reachable || !autovideo.ready);

  return (
    <section className="card p-5 space-y-3 text-sm">
      <div className="flex items-center gap-2">
        <h2 className="font-medium flex-1">成片</h2>
        {latest ? <span className="badge border border-border">{RENDER_LABEL[latest.status]}</span> : null}
        {latest ? <TimeStamp value={latest.completed_at ?? latest.created_at} label={latest.completed_at ? "完成" : "提交"} /> : null}
      </div>
      {detail.renders.length ? <div className="flex flex-wrap items-center gap-2 text-xs"><select aria-label="查看成片记录" className="input w-auto max-w-full text-xs" value={latest?.id ?? ""} onChange={(e) => setSelectedId(e.target.value)}>{detail.renders.map((r) => <option key={r.id} value={r.id}>v{r.version} · {RENDER_LABEL[r.status]} · {r.created_at.replace("T", " ").slice(0,16)}</option>)}</select>{historical ? <span className="text-warning">旧版 v{latest.version} 成片，当前文稿为 v{detail.current_version}</span> : <span className="text-text-muted">当前文稿 v{detail.current_version}</span>}</div> : null}
      {notReady ? (
        <p className="text-warning text-xs">
          {autovideo?.reachable
            ? `AutoVideo 生成依赖未就绪：${autovideo.blockers.join("、")}`
            : `连不上 AutoVideo（${autovideo?.base_url}），请先启动它。`}
        </p>
      ) : null}

      {video && latest ? (
        <div className="space-y-2">
          <video key={latest.id} controls preload="metadata" src={video.url} className="w-full rounded bg-black aspect-video" />
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <a href={video.url} download={video.name} className="text-accent hover:underline">
              下载成片
            </a>
            <button className="text-accent hover:underline" onClick={() => onPublish(latest.version)}>发布 v{latest.version}：查看发布包与记录链接</button>
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

      {latest?.status === "COMPLETED" && latest.timings?.elapsed_seconds != null ? (
        <p className="text-xs text-text-muted">
          总耗时 {Math.round(latest.timings.elapsed_seconds)} 秒
          {latest.timings.download_seconds != null ? ` · 文件回传 ${latest.timings.download_seconds.toFixed(2)} 秒` : ""}
        </p>
      ) : null}

      {latest && active ? (
        <p className="text-text-muted">
          {RENDER_LABEL[latest.status]}
          {latest.active_stage ? ` · ${latest.active_stage}` : ""} · 定稿 v{latest.version}，{latest.source_chars} 字。
          正在后台制作，完成后会自动回传，可以离开此页。
        </p>
      ) : null}
      {latest && (latest.status === "FAILED" || latest.status === "CANCELLED") ? (
        <p className="text-danger">
          {latest.error_code ? <span className="font-mono text-xs mr-2">{latest.error_code}</span> : null}
          {latest.error_message}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {autovideo?.voices.length ? (
          <select className="input text-xs py-1 w-auto" value={voice} onChange={(e) => setVoice(e.target.value)} aria-label="配音声音">
            <option value="">默认声音</option>
            {autovideo.voices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select>
        ) : null}
        {canRender ? (
          <button className="btn btn-primary text-xs" onClick={() => onRender()} disabled={busy || (current != null && RENDER_ACTIVE.includes(current.status)) || notReady}>
            {busy ? "提交中..." : latest ? `用定稿 v${detail.approved_version} 重新出片` : `用定稿 v${detail.approved_version} 出片`}
          </button>
        ) : null}
        {canRender ? (
          <button className="btn btn-secondary text-xs" onClick={() => onRender("fast")} disabled={busy || (current != null && RENDER_ACTIVE.includes(current.status)) || notReady}>
            快速卡片出片
          </button>
        ) : null}
        {latest?.autovideo_url ? (
          <a href={latest.autovideo_url} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline">
            在 AutoVideo 中查看 →
          </a>
        ) : null}
      </div>
    </section>
  );
}
