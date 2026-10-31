import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import type { VideoScriptDetail } from "@/api/video";
import { renderForVersion } from "@/pages/videoNext";

const ACTIVE = ["SUBMITTING", "QUEUED", "RUNNING", "DOWNLOADING"];
const STATE: Record<string, string> = { COMPLETED: "成片完成", FAILED: "制作失败", CANCELLED: "已取消",
  RUNNING: "制作中", QUEUED: "排队中", SUBMITTING: "提交中", DOWNLOADING: "回传中" };

export function ScriptIteration({ detail, canEdit, canManageModels = false, isCurrent, editing, busy, onRewrite, onVideo, onCompare }: {
  detail: VideoScriptDetail; canEdit: boolean; isCurrent: boolean; editing: boolean; busy: string | null;
  canManageModels?: boolean;
  onRewrite: () => void; onVideo: (renderId?: string) => void; onCompare?: () => void;
}) {
  const version = detail.current_version;
  const usable = detail.renders.find(r => r.version === version && r.status === "COMPLETED" && r.files?.some(f => f.name === "video.mp4"))
    ?? detail.renders.find(r => r.status === "COMPLETED" && r.files?.some(f => f.name === "video.mp4"));
  const latest = detail.renders[0];
  const current = renderForVersion(detail, version);
  const running = Boolean(detail.stage) || ["RESEARCHING", "WRITING", "PLAN_READY"].includes(detail.status)
    || detail.renders.some(r => ACTIVE.includes(r.status));
  const approved = detail.status === "APPROVED" && detail.approved_version === version;
  const shown = detail.version_shown;
  const checks = shown?.checks;
  const audit = detail.stage ? "审校进行中" : checks?.model_checks_stale ? "修改后尚未重审" : checks?.mode === "on_request" ? "未发起（按需）"
    : !checks?.model_ran ? "未进行模型审校" : checks.summary.passed ? "审校通过" : `有 ${checks.issues.length} 条意见`;
  const config = detail.configuration;
  const article = detail.topic?.article;
  const links = config?.links.filter(l => !article || ["文稿配方与提示词", "出片形式与配音", "片头片尾与包装", "矩阵的共用视频设置", "任务的视频生成设置"].includes(l.label));
  const recipe = config?.links.find(l => l.label === "文稿配方与提示词");
  const configHref = (href: string) => `${href}${href.includes("?") ? "&" : "?"}from=${encodeURIComponent(detail.id)}`;
  return <section className="card overflow-hidden" aria-label="文稿与成片状态摘要">
    <div className="grid sm:grid-cols-3 divide-y sm:divide-y-0 sm:divide-x divide-border text-xs">
      <div className="p-3 space-y-1"><p className="text-text-muted">当前文稿</p><p><b>v{version}</b> · {approved ? "已定稿" : "尚未定稿"}</p>
        {detail.approved_version && !approved ? <p className="text-text-muted">旧版定稿 v{detail.approved_version}，当前稿尚未定稿</p> : null}
      </div>
      <div className="p-3 space-y-1"><p className="text-text-muted">本页审校 · v{shown?.version ?? version}</p><p>{audit}</p>{editing && <p className="text-warning">未保存修改尚未参与审校</p>}</div>
      <div className="p-3 space-y-1"><p className="text-text-muted">当前版出片</p><p>{current ? `v${version} · ${STATE[current.status]}` : `v${version} 尚未出片`}</p>
        {usable ? <button className="text-accent hover:underline" onClick={() => onVideo(usable.id)}>{usable.version === version ? `查看 v${version} 可用成片` : `查看旧版 v${usable.version} 成片`}</button> : null}
        {usable && usable.version !== version ? <p className="text-warning">这个视频仍使用旧稿；当前文稿 v{version} 不会自动更新旧视频。</p> : null}
      </div>
    </div>
    {latest ? <div className="border-t border-border px-3 py-2 text-xs flex flex-wrap items-center gap-2" aria-label="最近出片尝试">
      <span className={latest.status === "FAILED" ? "text-danger" : "text-text-muted"}>最近尝试：v{latest.version} · {STATE[latest.status]}</span>
      <span className="text-text-muted">{latest.created_at?.replace("T", " ").slice(0, 16)}</span>
      <button className="text-accent hover:underline" onClick={() => onVideo(latest.id)}>查看此次记录</button>
      {latest.status === "FAILED" && usable ? <span className="text-text-muted">已有可用成片仍保留</span> : null}
    </div> : null}
    {!editing ? <details className="border-t border-border p-3">
      <summary className="cursor-pointer text-xs text-text-muted">重新生成与共用设置</summary>
      <div className="mt-3 space-y-3">
        {canEdit ? <div className="flex flex-wrap gap-2">
          <button className="btn text-xs" disabled={!isCurrent || running || busy !== null || (!detail.report_path && !article)} onClick={onRewrite}>{busy === "rewrite" ? "提交中…" : "重新生成文稿"}</button>
          {recipe ? <Link className="btn text-xs" to={configHref(recipe.href)}>调整配方与提示词<ArrowUpRight className="h-3 w-3" /></Link> : null}
        </div> : null}
        {onCompare && (detail.versions.length > 1 || detail.renders.length > 1) && <button className="btn text-xs" onClick={onCompare}>对比前后文稿与视频</button>}
        <p className="text-xs text-text-muted">{!isCurrent ? "历史版本只读，返回当前版本后可重新生成。" : running ? "任务进行中，完成后可继续迭代。" : article ? "沿用保存的文章与本次处理方式生成新版本，旧稿、旧片均保留。" : "沿用研究报告生成新版本，旧稿、旧片及所有制作尝试均保留。只修改当前文字，用上方「编辑」。"}</p>
        {config?.changed_since_generation ? <p className="text-xs text-warning">生成配置已有变化；已有稿件和视频不会自动更新。</p> : null}
        <div className="grid md:grid-cols-2 gap-2">{links?.map(l => <Link key={l.href} className="rounded border border-border p-3 hover:bg-bg-hover space-y-1" to={configHref(l.href)}>
          <p className="text-sm text-accent flex items-center gap-1">{l.label}<ArrowUpRight className="h-3 w-3" /></p>
          <p className="text-xs text-text-muted">{l.name} · 影响{l.scope}</p><p className="text-xs text-text-muted">{l.next}</p>
        </Link>)}</div>
        {!config?.recipe_available ? <Link className="text-xs text-accent hover:underline" to={configHref("/video-recipes")}>查看与修复文稿配方 →</Link> : null}
        {canManageModels ? <Link className="text-xs text-accent hover:underline block" to={configHref("/settings")}>模型与服务设置（全局生效） →</Link> : null}
        <p className="text-xs text-text-muted">共用设置影响引用它的后续任务；本次配音与制作档位在「成片」页选择，不修改共用配置。</p>
      </div>
    </details> : null}
  </section>;
}
