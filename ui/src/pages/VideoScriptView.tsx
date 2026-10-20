import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  approveVideoScript,
  archiveVideoScript,
  getAutoVideoStatus,
  getVideoFacts,
  getVideoRender,
  getVideoScript,
  listVideoStyles,
  renderVideoScript,
  restyleVideoScript,
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
import { TimeStamp } from "@/components/TimeStamp";
import { ErrorState } from "@/components/ErrorState";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage, useToast } from "@/lib/toast";
import { cn, timeAgo } from "@/lib/utils";
import { nextStep, TAB_LABEL, type ScriptTab } from "./videoNext";
import { ChecksPanel, DescriptionPanel, FactChips, IssueList, PlanPanel, StylePanel } from "./VideoAdaptation";
import { PublishPanel } from "./VideoPublish";
import { STATUS_CLASS, STATUS_LABEL, minutes } from "./VideoScripts";

const EVIDENCE: Record<Evidence, { label: string; cls: string }> = {
  CITED: { label: "有出处", cls: "bg-success/15 text-success border-success/30" },
  UNCITED: { label: "无引用", cls: "bg-bg-card text-text-muted border-border" },
  NEEDS_REVIEW: { label: "需核实", cls: "bg-warning/15 text-warning border-warning/30" },
};

const LENGTH: Record<string, { label: string; cls: string }> = {
  OK: { label: "时长达标", cls: "text-success" },
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
  const [draftTitle, setDraftTitle] = useState("");
  const [draftSections, setDraftSections] = useState<ScriptSection[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [autovideo, setAutovideo] = useState<AutoVideoStatus | null>(null);
  const [voice, setVoice] = useState("");
  const [catalog, setCatalog] = useState<StyleCatalog | null>(null);
  const [facts, setFacts] = useState<Record<string, Fact>>({});

  const load = useCallback(async () => {
    try {
      setDetail(await getVideoScript(id, versionParam));
      setError(null);
    } catch (e) {
      setError(errorMessage(e, "加载文稿失败"));
    }
  }, [id, versionParam]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while research/writing or a render is in flight.
  const latestRender = detail?.renders[0] ?? null;
  const busyServer =
    detail != null &&
    (["RESEARCHING", "WRITING"].includes(detail.status) ||
      Boolean(detail.stage) || // a rerun audit: DRAFT while the audits run
      (latestRender != null && RENDER_ACTIVE.includes(latestRender.status)));
  useEffect(() => {
    if (!busyServer || editing) return;
    const timer = setInterval(async () => {
      if (latestRender && RENDER_ACTIVE.includes(latestRender.status)) {
        try {
          const fresh = await getVideoRender(latestRender.id);
          setDetail((d) => (d ? { ...d, renders: d.renders.map((r) => (r.id === fresh.id ? fresh : r)) } : d));
          return;
        } catch {
          // fall through to a full reload
        }
      }
      load();
    }, 5000);
    return () => clearInterval(timer);
  }, [busyServer, editing, latestRender, load]);

  useEffect(() => {
    if (canEdit && catalog === null) listVideoStyles().then(setCatalog).catch(() => undefined);
  }, [canEdit, catalog]);

  // The fact sheet behind the version on screen, for the fact labels' tooltips.
  const sheetId = detail?.version_shown?.meta?.fact_sheet_id;
  const shownVersion = detail?.version_shown?.version;
  useEffect(() => {
    if (!sheetId) {
      setFacts({});
      return;
    }
    getVideoFacts(id, shownVersion)
      .then((sheet) => setFacts(Object.fromEntries(sheet.facts.map((f) => [f.id, f]))))
      .catch(() => setFacts({}));
  }, [id, sheetId, shownVersion]);

  useEffect(() => {
    if (detail?.status === "APPROVED" && autovideo === null) {
      getAutoVideoStatus().then(setAutovideo).catch(() => undefined);
    }
  }, [detail?.status, autovideo]);

  if (error) return <ErrorState error={error} onRetry={load} />;
  if (!detail) return <ErrorState empty="加载中..." />;

  const shown = detail.version_shown;
  const isCurrent = shown?.version === detail.current_version;
  const check = shown?.length_check;
  const problems = shown?.meta?.problems ?? [];
  const step = nextStep(detail);
  const staged = ["DRAFT", "APPROVED"].includes(detail.status);
  const tabs: ScriptTab[] = !staged
    ? []
    : detail.status === "APPROVED"
      ? ["draft", "review", "video", "publish"]
      : detail.renders.length
        ? ["draft", "review", "video"]
        : ["draft", "review"];
  const asked = search.get("tab") as ScriptTab | null;
  const tab: ScriptTab = editing || !staged ? "draft" : asked && tabs.includes(asked) ? asked : step.tab;
  const goTab = (t: ScriptTab) =>
    setSearch((prev) => {
      const next = new URLSearchParams(prev);
      next.set("tab", t);
      return next;
    });

  function startEdit() {
    if (!shown) return;
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

  async function render() {
    setBusy("render");
    try {
      const made = await renderVideoScript(id, voice || undefined);
      setDetail((d) => (d ? { ...d, renders: [made, ...d.renders] } : d));
      toast.success("已交给 AutoVideo", "成片通常需要 15–30 分钟");
    } catch (e) {
      toast.error("出片没有开始", errorMessage(e));
    } finally {
      setBusy(null);
    }
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
    <div className="space-y-4">
      <Link to="/video" className="text-xs text-accent hover:underline">
        ← 视频文稿
      </Link>

      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1">
            <span className={cn("badge border", STATUS_CLASS[detail.status])}>{STATUS_LABEL[detail.status]}</span>
            <CredibilityBadge value={detail.credibility} />
            {detail.auto ? <span className="badge border border-border text-text-muted">自动流水线</span> : null}
            {detail.column ? <span className="text-xs text-text-muted">{detail.column.name}</span> : null}
            {detail.approved_version ? (
              <span className="text-xs text-success">定稿 v{detail.approved_version}</span>
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
        {canEdit && shown && ["DRAFT", "APPROVED"].includes(detail.status) ? (
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

      <section
        aria-label="下一步"
        className={cn(
          "card text-sm flex flex-wrap items-center gap-x-3 gap-y-2",
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

      <ScriptSteps detail={detail} canEdit={canEdit} onChanged={setDetail} />

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
        <section className="card text-sm space-y-1">
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
        <section className="card text-sm flex flex-wrap items-center gap-x-6 gap-y-2">
          <span>
            约 <b>{minutes(check.estimated_seconds)}</b>（{check.spoken_length} 字）
          </span>
          {check.target_minutes ? (
            <span className="text-text-muted">
              目标 {check.target_minutes} 分钟（{check.budget} 字）
            </span>
          ) : null}
          {check.status ? (
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
        <ReportReuse scriptId={detail.id} reportId={detail.report_id} canEdit={canEdit} />
      ) : null}

      {shown && tab === "draft" ? (
        <StylePanel
          key={`${shown.version}-${catalog ? "ready" : "loading"}`}
          detail={detail}
          catalog={catalog}
          canRestyle={canEdit && !editing && ["DRAFT", "APPROVED"].includes(detail.status) && Boolean(detail.report_path) && detail.column?.writer === "adapt"}
          busy={busy === "restyle"}
          onRestyle={restyle}
        />
      ) : null}
      {shown?.checks && !editing && tab === "review" ? (
        detail.status === "APPROVED" ? (
          <details className="text-sm">
            <summary className="cursor-pointer text-text-muted">
              审校记录（已定稿，{shown.checks.summary.passed ? "当时通过" : `当时有 ${shown.checks.summary.serious} 处必须改，已由你决定`}）
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

      {detail.versions.length > 1 && tab === "draft" ? (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-text-muted">版本</span>
          {detail.versions.map((v) => (
            <button
              key={v.version}
              onClick={() => setSearch(v.version === detail.current_version ? {} : { version: String(v.version) })}
              disabled={editing}
              className={cn(
                "badge border cursor-pointer",
                v.version === shown?.version ? "bg-accent-solid text-white border-accent" : "bg-bg-card border-border hover:bg-bg-hover",
              )}
              title={`${v.edit_type === "EDITED" ? "编辑" : "生成"}${v.style ? ` · ${v.style}` : ""} · ${v.created_by || ""} · ${timeAgo(v.created_at)}`}
            >
              v{v.version}
              {detail.approved_version === v.version ? " ✓" : ""}
            </button>
          ))}
          {!isCurrent ? <span className="text-text-muted">（正在看旧版本）</span> : null}
        </div>
      ) : null}

      {(tab === "draft" ? sections : []).map((section, si) => (
        <section key={si} className="card space-y-3">
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
                {check.sections[si].budget ? ` / ${check.sections[si].budget}` : ""} 字
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

      {detail.status === "APPROVED" && tab === "publish" ? (
        <PublishPanel
          scriptId={detail.id}
          version={detail.approved_version ?? undefined}
          refreshKey={latestRender?.status}
          canEdit={canEdit}
          recheckEnabled={Boolean(detail.column?.recheck)}
          publications={detail.publications}
          rechecks={detail.rechecks}
          onChanged={load}
        />
      ) : null}

      {(detail.status === "APPROVED" || detail.renders.length) && tab === "video" ? (
        <RenderPanel
          detail={detail}
          autovideo={autovideo}
          voice={voice}
          setVoice={setVoice}
          canRender={canEdit && detail.status === "APPROVED"}
          busy={busy === "render"}
          onRender={render}
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
}: {
  detail: VideoScriptDetail;
  autovideo: AutoVideoStatus | null;
  voice: string;
  setVoice: (v: string) => void;
  canRender: boolean;
  busy: boolean;
  onRender: () => void;
}) {
  const latest: VideoRender | null = detail.renders[0] ?? null;
  const active = latest != null && RENDER_ACTIVE.includes(latest.status);
  const video = latest?.status === "COMPLETED" ? latest.files.find((f) => f.name === "video.mp4") : undefined;
  const notReady = autovideo != null && (!autovideo.reachable || !autovideo.ready);

  return (
    <section className="card space-y-3 text-sm">
      <div className="flex items-center gap-2">
        <h2 className="font-medium flex-1">成片</h2>
        {latest ? <span className="badge border border-border">{RENDER_LABEL[latest.status]}</span> : null}
        {latest ? <TimeStamp value={latest.completed_at ?? latest.created_at} label={latest.completed_at ? "完成" : "提交"} /> : null}
      </div>
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

      {latest && active ? (
        <p className="text-text-muted">
          {RENDER_LABEL[latest.status]}
          {latest.active_stage ? ` · ${latest.active_stage}` : ""} · 定稿 v{latest.version}，{latest.source_chars} 字。
          成片通常需要 15–30 分钟，可以离开此页。
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
          <button className="btn btn-primary text-xs" onClick={onRender} disabled={busy || active || notReady}>
            {busy ? "提交中..." : latest ? `用定稿 v${detail.approved_version} 重新出片` : `用定稿 v${detail.approved_version} 出片`}
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
