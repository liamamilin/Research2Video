import { usePublishDraft } from "@/lib/use-publish-draft";
import { PublishDraftStatus } from "./PublishDraftStatus";
import { useCallback, useState } from "react";
import { api } from "@/api/client";
import { DOUYIN_API, douyinAction, type DouyinPreview } from "@/api/douyin";
import { EMPTY_FIRST_COMMENT } from "@/api/platforms";
import { FirstCommentOptions } from "./FirstCommentOptions";
import { useAuthStore } from "@/lib/auth-store";
import { ErrorState } from "@/components/ErrorState";
import { usePageResource } from "@/lib/use-page-resource";
import { PublishAdaptation } from "./PublishAdaptation";
import { publishCheck, textCount, validFirstComment } from "@/lib/publish-text";
type Run = (action: () => Promise<unknown>) => Promise<void>;
export function UploadEditor({ renderId, run, busy, onDone }: { renderId: string; run: Run; busy: boolean; onDone: () => void }) {
  const load = useCallback((signal: AbortSignal) => api<DouyinPreview>(`${DOUYIN_API}/preview/${encodeURIComponent(renderId)}`, { signal }), [renderId]);
  const { data, error, reload } = usePageResource(load);
  if (error) return <ErrorState error={error} onRetry={reload} compact />;
  if (!data) return <p className="text-sm text-text-muted">正在读取所选版本的发布包…</p>;
  return <PublishForm key={renderId} initial={data} run={run} busy={busy} onDone={onDone} />;
}

function PublishForm({ initial, run, busy, onDone }: { initial: DouyinPreview; run: Run; busy: boolean; onDone: () => void }) {
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
  const validComment = validFirstComment("douyin",firstComment);
  const list = tags.split(/[,，]/).map(t => t.trim().replace(/^#/, "")).filter(Boolean);
  const payload = {title,description,tags:list,first_comment:firstComment};
  const textCheck = publishCheck("douyin",payload,initial.adaptation?.check.limits);
  const valid = textCheck.valid;
  return <form className="card p-4 space-y-3" aria-label="抖音发布包" onSubmit={event => { event.preventDefault(); if (valid && validComment) void run(async () => {
    await douyinAction("/uploads", { render_id: initial.render_id, account_uid: initial.account_uid, title, description, tags: list, first_comment: firstComment }); onDone();
  }); }}>
    <fieldset disabled={busy} className="space-y-3 min-w-0">
    <p className="text-xs text-text-muted break-all">本次成片 {initial.render_id} · 账号 {initial.account_uid || "待连接"}</p>
    {initial.draft?.render_id && initial.draft.render_id !== initial.render_id ? <p className="text-xs text-warning">文案复用自本版另一条成片 {initial.draft.render_id}；本次保存和提交使用上方所选成片，原草稿版本仍保留。</p> : null}
    <h3 className="text-sm font-medium">抖音发布包 · 文稿 v{initial.version}</h3>
    <label className="block text-xs space-y-1"><span>抖音标题（{textCount(title.trim())}/30）</span><input aria-label="抖音标题" className="input w-full" value={title} onChange={e => {setTitle(e.target.value);}} required /></label>
    <label className="block text-xs space-y-1"><span>抖音简介（{textCount(description)}/1000）</span><textarea aria-label="抖音简介" className="input w-full" rows={5} value={description} onChange={e => {setDescription(e.target.value);}} /></label>
    <label className="block text-xs space-y-1"><span>抖音话题（最多 5 个，逗号分隔）</span><input aria-label="抖音话题" className="input w-full" value={tags} onChange={e => setTags(e.target.value)} /></label>
    <FirstCommentOptions platform="douyin" value={firstComment} onChange={v => { setFirstComment(v); setTemplateSaved(false); }} />
    <PublishAdaptation platform="douyin" renderId={initial.render_id} accountUid={initial.account_uid} value={payload} initial={initial.adaptation} run={run} busy={busy} onApply={next => draft.applySaved({ title: next.title, description: next.description, tags: next.tags.join("，"), firstComment: next.first_comment ?? firstComment })} />
    {admin ? <details><summary className="text-xs cursor-pointer">后续投稿默认评论设置</summary><div className="flex flex-wrap items-center gap-2"><button type="button" className="btn text-xs" disabled={busy || !validComment} onClick={() => void run(async () => { await douyinAction("/first-comment-template", firstComment, "PUT"); setTemplateSaved(true); })}>保存为常用评论</button>{templateSaved ? <span className="text-xs text-text-muted">已保存，后续投稿默认使用；不补发到已有作品。</span> : null}</div></details> : null}
    <p className="text-xs text-text-muted">含 AI 生成内容的成片将使用「内容由AI生成」声明。提交后会打开独立浏览器；如需身份验证，请在该窗口完成。作品确认公开后自动回写本版文稿的发布记录。</p>
    {!valid ? <p className="text-xs text-warning">请检查标题、简介与话题长度；简介和话题合计最多 1000 字。</p> : null}
    <div className="flex flex-wrap items-center gap-3"><button type="button" className="btn text-xs" disabled={busy} onClick={() => void run(async () => { await api("/api/video/distributions/douyin/draft", { method: "PUT", body: { render_id: initial.render_id, account_uid: initial.account_uid, payload: { title, description, tags: list, first_comment: firstComment } } }); draft.markSaved(); })}>保存草稿</button><PublishDraftStatus draft={draft} /></div>
    <button className="btn btn-primary text-xs" disabled={busy || !valid || !validComment}>提交到抖音</button>
    </fieldset>
  </form>;
}
