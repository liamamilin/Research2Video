import { usePublishDraft } from "@/lib/use-publish-draft";
import { PublishDraftStatus } from "./PublishDraftStatus";
import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { Send } from "lucide-react";
import { api } from "@/api/client";
import { DOUYIN_API, douyinAction, type DouyinJob, type DouyinPreview } from "@/api/douyin";
import { EMPTY_FIRST_COMMENT } from "@/api/platforms";
import { FirstCommentOptions } from "./FirstCommentOptions";
import { useAuthStore } from "@/lib/auth-store";
import { ErrorState } from "@/components/ErrorState";
import { usePageResource } from "@/lib/use-page-resource";
import { PublishAdaptation } from "./PublishAdaptation";
import { publishCheck, textCount, validFirstComment } from "@/lib/publish-text";
import { documentPublishLink } from "@/lib/platform-links";

type Run = (action: () => Promise<unknown>) => Promise<void>;

export function UploadEditor({ renderId, run, busy, onDone }: { renderId: string; run: Run; busy: boolean; onDone: (task?: DouyinJob) => void }) {
  const load = useCallback((signal: AbortSignal) => api<DouyinPreview>(`${DOUYIN_API}/preview/${encodeURIComponent(renderId)}`, { signal }), [renderId]);
  const { data, error, reload } = usePageResource(load);
  if (error) return <ErrorState error={error} onRetry={reload} compact />;
  if (!data) return <p className="text-sm text-text-muted">正在读取所选版本的发布包…</p>;
  if (data.existing_upload) {
    const existing = data.existing_upload;
    return <section className="card p-4 space-y-2 text-xs" aria-label="已有抖音投稿任务">
      <p className="text-sm font-medium">这个版本已有抖音投稿任务（{existing.status}）</p>
      <p className="text-text-muted">{existing.payload.title} · 文稿 v{existing.version} · {existing.phase}</p>
      <div className="flex flex-wrap gap-3">
        <Link className="text-accent hover:underline" to={documentPublishLink(existing.script_id, existing.version, existing.render_id)}>查看文稿发布记录 →</Link>
        {existing.url ? <a className="text-accent hover:underline" href={existing.url} target="_blank" rel="noreferrer">打开抖音作品</a> : null}
      </div>
    </section>;
  }
  return <PublishForm key={renderId} initial={data} run={run} busy={busy} onDone={onDone} />;
}

function DouyinCoverPreview({ covers }: { covers?: { portrait_url: string; landscape_url: string } }) {
  const [portraitFailed, setPortraitFailed] = useState(false);
  const [landscapeFailed, setLandscapeFailed] = useState(false);
  if (!covers) return null;
  return <div className="space-y-2">
    <p className="text-xs font-medium">抖音双比例封面预览（随投稿自动上传）</p>
    <div className="grid grid-cols-2 sm:flex sm:flex-wrap gap-3 items-start">
      <div className="space-y-1">
        <p className="text-xs text-text-muted">竖封面 3:4</p>
        {portraitFailed ? <div className="w-28 aspect-[3/4] rounded border border-border flex items-center justify-center text-[11px] text-text-muted p-2 text-center">暂无竖封面</div>
          : <img src={covers.portrait_url} alt="抖音竖封面 3:4 预览" className="w-28 aspect-[3/4] object-cover rounded border border-border bg-bg" onError={() => setPortraitFailed(true)} />}
      </div>
      <div className="space-y-1">
        <p className="text-xs text-text-muted">横封面 4:3</p>
        {landscapeFailed ? <div className="w-40 aspect-[4/3] rounded border border-border flex items-center justify-center text-[11px] text-text-muted p-2 text-center">暂无横封面</div>
          : <img src={covers.landscape_url} alt="抖音横封面 4:3 预览" className="w-40 aspect-[4/3] object-cover rounded border border-border bg-bg" onError={() => setLandscapeFailed(true)} />}
      </div>
    </div>
  </div>;
}

function PublishForm({ initial, run, busy, onDone }: { initial: DouyinPreview; run: Run; busy: boolean; onDone: (task?: DouyinJob) => void }) {
  const draft = usePublishDraft("douyin", initial.render_id, initial.account_uid ?? "", {
    title: initial.draft?.payload.title ?? initial.title,
    description: initial.draft?.payload.description ?? initial.description,
    tags: (initial.draft?.payload.tags ?? initial.tags).join("，"),
    firstComment: initial.draft?.payload.first_comment ?? initial.first_comment ?? EMPTY_FIRST_COMMENT,
  }, Boolean(initial.draft));
  const { title, description, tags, firstComment } = draft.value;
  const setTitle = (value: string) => draft.field("title", value);
  const setDescription = (value: string) => draft.field("description", value);
  const setTags = (value: string) => draft.field("tags", value);
  const setFirstComment = (value: typeof firstComment) => draft.field("firstComment", value);

  const [templateSaved, setTemplateSaved] = useState(false);
  const admin = useAuthStore(s => s.user?.role === "admin");
  const validComment = validFirstComment("douyin", firstComment);
  const list = tags.split(/[,，]/).map(t => t.trim().replace(/^#/, "")).filter(Boolean);
  const payload = { title, description, tags: list, first_comment: firstComment };
  const textCheck = publishCheck("douyin", payload, initial.adaptation?.check.limits);
  const valid = textCheck.valid;
  return <form className="card p-4 space-y-3" aria-label="抖音发布包" onSubmit={event => { event.preventDefault(); if (valid && validComment) void run(async () => {
    const task = await douyinAction<DouyinJob>("/uploads", { render_id: initial.render_id, account_uid: initial.account_uid, title, description, tags: list, first_comment: firstComment });
    onDone(task);
  }); }}>
    <fieldset disabled={busy} className="space-y-3 min-w-0">
    <p className="text-xs text-text-muted break-all">本次成片 {initial.render_id} · 账号 {initial.account_uid || "待连接"}</p>
    {initial.draft?.render_id && initial.draft.render_id !== initial.render_id ? <p className="text-xs text-warning">文案复用自本版另一条成片 {initial.draft.render_id}；本次保存和提交使用上方所选成片，原草稿版本仍保留。</p> : null}
    <div className="flex flex-wrap justify-between gap-2">
      <h3 className="text-sm font-medium">抖音发布包 · 文稿 v{initial.version}</h3>
      {initial.credibility?.text ? <span className="text-xs text-text-muted">{initial.credibility.text} · 内容由AI生成</span> : null}
    </div>
    {initial.warnings.length ? <div className="rounded border border-warning/30 bg-warning/5 p-2.5 text-xs text-warning space-y-1">{initial.warnings.map(w => <p key={w}>{w}</p>)}</div> : null}
    <label className="block text-xs space-y-1"><span>抖音标题（{textCount(title.trim())}/30）</span><input aria-label="抖音标题" className="input w-full" value={title} onChange={e => { setTitle(e.target.value); }} required /></label>
    <label className="block text-xs space-y-1"><span>抖音简介（{textCount(description)}/1000）</span><textarea aria-label="抖音简介" className="input w-full" rows={5} value={description} onChange={e => { setDescription(e.target.value); }} /></label>
    <label className="block text-xs space-y-1"><span>抖音话题（最多 5 个，逗号分隔）</span><input aria-label="抖音话题" className="input w-full" value={tags} onChange={e => setTags(e.target.value)} /></label>
    <DouyinCoverPreview covers={initial.covers} />
    <FirstCommentOptions platform="douyin" value={firstComment} onChange={v => { setFirstComment(v); setTemplateSaved(false); }} />
    <PublishAdaptation platform="douyin" renderId={initial.render_id} accountUid={initial.account_uid} value={payload} initial={initial.adaptation} run={run} busy={busy} onApply={next => draft.applySaved({ title: next.title, description: next.description, tags: next.tags.join("，"), firstComment: next.first_comment ?? firstComment })} />
    {admin ? <details><summary className="text-xs cursor-pointer">后续投稿默认评论设置</summary><div className="flex flex-wrap items-center gap-2 mt-2"><button type="button" className="btn text-xs" disabled={busy || !validComment} onClick={() => void run(async () => { await douyinAction("/first-comment-template", firstComment, "PUT"); setTemplateSaved(true); })}>保存为常用评论</button>{templateSaved ? <span className="text-xs text-text-muted">已保存，后续投稿默认使用；不补发到已有作品。</span> : null}</div></details> : null}
    <p className="text-xs text-text-muted">含 AI 生成内容的成片将自动勾选「内容由AI生成」声明；默认按策略页设置（无头/有头）提交。作品确认公开后自动回写本版文稿的发布记录。</p>
    {!valid ? <p className="text-xs text-warning">请检查标题、简介与话题长度；简介和话题合计最多 1000 字。</p> : null}
    <div className="flex flex-wrap items-center gap-3"><button type="button" className="btn text-xs" disabled={busy} onClick={() => void run(async () => { await api("/api/video/distributions/douyin/draft", { method: "PUT", body: { render_id: initial.render_id, account_uid: initial.account_uid, payload: { title, description, tags: list, first_comment: firstComment } } }); draft.markSaved(); })}>保存草稿</button><PublishDraftStatus draft={draft} /></div>
    <button className="btn btn-primary text-xs" disabled={busy || !valid || !validComment}><Send className="w-3.5 h-3.5" />提交到抖音</button>
    </fieldset>
  </form>;
}
