import { Link } from "react-router-dom";
import { ArrowUpRight, RefreshCw, Settings2, FileText, Film } from "lucide-react";
import type { VideoScriptDetail } from "@/api/video";
import { renderForVersion } from "@/pages/videoNext";

const ACTIVE = ["SUBMITTING", "QUEUED", "RUNNING", "DOWNLOADING"];
const STATE: Record<string, string> = { COMPLETED: "成片完成", FAILED: "制作失败", CANCELLED: "已取消",
  RUNNING: "制作中", QUEUED: "排队中", SUBMITTING: "提交中", DOWNLOADING: "回传中" };

export function ScriptIteration({ detail, canEdit, canManageModels = false, isCurrent, editing, busy, onRewrite, onRender, onVideo }: {
  detail: VideoScriptDetail; canEdit: boolean; isCurrent: boolean; editing: boolean; busy: string | null;
  canManageModels?: boolean;
  onRewrite: () => void; onRender: () => void; onVideo: () => void;
}) {
  const version = detail.current_version;
  const render = renderForVersion(detail, version) ?? detail.renders[0];
  const running = Boolean(detail.stage) || ["RESEARCHING", "WRITING", "PLAN_READY"].includes(detail.status)
    || detail.renders.some(r => ACTIVE.includes(r.status));
  const approved = detail.status === "APPROVED" && detail.approved_version === version;
  const mismatch = render && render.version !== version;
  const config = detail.configuration;
  const recipe = config?.links.find(l => l.label === "文稿配方与提示词");
  const configHref = (href: string) => `${href}${href.includes("?") ? "&" : "?"}from=${encodeURIComponent(detail.id)}`;
  return <section className="card overflow-hidden" aria-label="版本与重新生成">
    <div className="grid sm:grid-cols-2 divide-y sm:divide-y-0 sm:divide-x divide-border">
      <div className="p-4 flex items-start gap-3"><FileText className="w-4 h-4 text-text-muted mt-1" />
        <div><p className="text-xs text-text-muted">当前文稿</p><p className="font-medium mt-1">v{version} <span className="text-xs font-normal text-text-muted">{approved ? "已定稿" : "尚未定稿"}</span></p>
          {detail.approved_version && !approved ? <p className="text-xs text-text-muted mt-1">上次定稿 v{detail.approved_version}，当前新稿需要重新定稿</p> : null}
        </div>
      </div>
      <div className="p-4 flex items-start gap-3"><Film className="w-4 h-4 text-text-muted mt-1" />
        <div className="min-w-0 flex-1"><p className="text-xs text-text-muted">视频对应版本</p>
          <p className="font-medium mt-1">{render ? `文稿 v${render.version}` : "还没有视频"} {render ? <span className="text-xs font-normal text-text-muted">{STATE[render.status]}</span> : null}</p>
          {mismatch ? <p className="text-xs text-warning mt-1">当前文稿已到 v{version}，这个视频仍使用旧稿；重写文稿不会自动更新视频。</p> : null}
        </div>
        {render ? <button className="text-xs text-accent hover:underline shrink-0" onClick={onVideo}>查看成片</button> : null}
      </div>
    </div>
    {!editing ? <div className="border-t border-border p-4 space-y-3">
      {canEdit ? <div className="flex flex-wrap gap-2">
        <button className="btn btn-primary text-sm" disabled={!isCurrent || running || busy !== null || !detail.report_path} onClick={onRewrite}>
          <RefreshCw className="h-4 w-4" />{busy === "rewrite" ? "提交中…" : "重新生成文稿"}
        </button>
        <button className="btn text-sm" disabled={!isCurrent || running || busy !== null || !approved} onClick={onRender}>
          <Film className="h-4 w-4" />{busy === "render" ? "提交中…" : render ? "重新生成视频" : "生成视频"}
        </button>
        {recipe ? <Link className="btn text-sm" to={configHref(recipe.href)}><Settings2 className="h-4 w-4" />调整配方与提示词<ArrowUpRight className="h-3.5 w-3.5" /></Link> : null}
      </div> : null}
      <p className="text-xs text-text-muted">{!isCurrent ? "历史版本只读，返回当前版本后可重新生成。" : running ? "任务进行中，完成后可继续迭代。" : !detail.report_path ? "研究报告完成后，可用同一份报告重新生成文稿。" :
        `文稿：沿用研究报告，按当前配置生成新版本。视频：使用定稿${detail.approved_version ? ` v${detail.approved_version}` : "文稿"}重新制作。旧稿与旧片均保留。`}
        {isCurrent && !running && !approved ? ` 先审看并定稿 v${version}，再生成对应视频。` : ""}</p>
      {config?.changed_since_generation ? <p className="text-xs text-warning">生成配置已有变化；已有稿件和视频不会自动更新，请按调整内容选择重新写稿或重新出片。</p> : null}
      <details className="group">
        <summary className="cursor-pointer text-sm text-accent w-fit">调整生成方式与相关设置</summary>
        <div className="mt-3 space-y-3">
          <p className="text-xs text-text-muted">只修改这一篇，用上方「编辑」。想改善后续自动生成，修改下面对应的配置，再回到这里重跑。</p>
          <div className="grid md:grid-cols-2 gap-2">{config?.links.map(l => <Link key={l.href} className="rounded border border-border p-3 hover:bg-bg-hover space-y-1" to={configHref(l.href)}>
            <p className="text-sm text-accent flex items-center gap-1">{l.label}<ArrowUpRight className="h-3.5 w-3.5" /></p>
            <p className="text-xs text-text-muted break-words">{l.name} · 影响{l.scope}</p><p className="text-xs text-text-muted">{l.next}</p>
          </Link>)}</div>
          {!config?.recipe_available ? <Link className="text-xs text-accent hover:underline" to={configHref("/video-recipes")}>查看与修复文稿配方 →</Link> : null}
          {canManageModels ? <Link className="text-xs text-accent hover:underline block" to={configHref("/settings")}>模型与服务设置（全局生效） →</Link> : null}
          <p className="text-xs text-text-muted">这里跳转到当前配置；保存不会改写已有文稿、视频或历史记录。共用部件会影响引用它的其他配方。</p>
        </div>
      </details>
    </div> : null}
  </section>;
}
