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
  const [title, setTitle] = useState(initial.draft?.payload.title ?? initial.title);
  const [description, setDescription] = useState(initial.draft?.payload.description ?? initial.description);
  const [tags, setTags] = useState((initial.draft?.payload.tags ?? initial.tags).join("，"));
  const [firstComment, setFirstComment] = useState(initial.draft?.payload.first_comment ?? initial.first_comment ?? EMPTY_FIRST_COMMENT);
  const [templateSaved, setTemplateSaved] = useState(false);
  const admin = useAuthStore(s => s.user?.role === "admin");
  const validComment = validFirstComment("douyin",firstComment);
  const [saved, setSaved] = useState(Boolean(initial.draft));
  const list = tags.split(/[,，]/).map(t => t.trim().replace(/^#/, "")).filter(Boolean);
  const payload = {title,description,tags:list,first_comment:firstComment};
  const textCheck = publishCheck("douyin",payload,initial.adaptation?.check.limits);
  const valid = textCheck.valid;
  return <form className="card p-4 space-y-3" aria-label="抖音发布包" onSubmit={event => { event.preventDefault(); if (valid && validComment) void run(async () => {
    await douyinAction("/uploads", { render_id: initial.render_id, account_uid: initial.account_uid, title, description, tags: list, first_comment: firstComment }); onDone();
  }); }}>
    <h3 className="text-sm font-medium">抖音发布包 · 文稿 v{initial.version}</h3>
    {initial.warnings.map((text, i) => <p key={i} className="text-xs text-warning">{text}</p>)}
    <label className="block text-xs space-y-1"><span>抖音标题（{textCount(title.trim())}/30）</span><input aria-label="抖音标题" className="input w-full" value={title} onChange={e => {setTitle(e.target.value);setSaved(false);}} required /></label>
    <label className="block text-xs space-y-1"><span>抖音简介（{textCount(description)}/1000）</span><textarea aria-label="抖音简介" className="input w-full" rows={5} value={description} onChange={e => {setDescription(e.target.value);setSaved(false);}} /></label>
    <label className="block text-xs space-y-1"><span>抖音话题（最多 5 个，逗号分隔）</span><input aria-label="抖音话题" className="input w-full" value={tags} onChange={e => setTags(e.target.value)} /></label>
    <FirstCommentOptions platform="douyin" value={firstComment} onChange={v => { setFirstComment(v); setTemplateSaved(false); }} />
    <PublishAdaptation platform="douyin" renderId={initial.render_id} accountUid={initial.account_uid} value={payload} initial={initial.adaptation} run={run} busy={busy} onApply={next => {setTitle(next.title);setDescription(next.description);setTags(next.tags.join("，"));if(next.first_comment)setFirstComment(next.first_comment);setSaved(true);}} />
    {admin ? <div className="flex flex-wrap items-center gap-2"><button type="button" className="btn text-xs" disabled={busy || !validComment} onClick={() => void run(async () => { await douyinAction("/first-comment-template", firstComment, "PUT"); setTemplateSaved(true); })}>保存为常用评论</button>{templateSaved ? <span className="text-xs text-text-muted">已保存，后续投稿默认使用；不补发到已有作品。</span> : null}</div> : null}
    <p className="text-xs text-text-muted">含 AI 生成内容的成片将使用「内容由AI生成」声明。提交后会打开独立浏览器；如需身份验证，请在该窗口完成。作品确认公开后自动回写本版文稿的发布记录。</p>
    {!valid ? <p className="text-xs text-warning">请检查标题、简介与话题长度；简介和话题合计最多 1000 字。</p> : null}
    <div className="flex flex-wrap items-center gap-3"><button type="button" className="btn text-xs" disabled={busy} onClick={() => void run(async () => { await api("/api/video/distributions/douyin/draft", { method: "PUT", body: { render_id: initial.render_id, account_uid: initial.account_uid, payload: { title, description, tags: list, first_comment: firstComment } } }); setSaved(true); })}>保存抖音发布包</button>{saved ? <span className="text-xs text-text-muted">已保存本平台发布包；修改后请再次保存。</span> : null}</div>
    <button className="btn btn-primary text-xs" disabled={busy || !valid || !validComment}>确认发布到抖音</button>
  </form>;
}
