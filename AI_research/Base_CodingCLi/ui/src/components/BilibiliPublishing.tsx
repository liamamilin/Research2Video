import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLink, Send } from "lucide-react";
import { api } from "@/api/client";
import { EMPTY_FIRST_COMMENT, PLATFORM_API, loadBilibiliCategories, platformAction, type UploadPreview, type UploadTask } from "@/api/platforms";
import { useAuthStore } from "@/lib/auth-store";
import { usePageResource } from "@/lib/use-page-resource";
import { ErrorState } from "@/components/ErrorState";
import { BilibiliCategorySelect } from "@/components/BilibiliCategorySelect";
import { FirstCommentOptions } from "@/components/FirstCommentOptions";
import { cn } from "@/lib/utils";
import { documentPublishLink } from "@/lib/platform-links";
import { PublishAdaptation } from "./PublishAdaptation";
import { publishCheck, textCount, validFirstComment } from "@/lib/publish-text";
const UPLOAD_STATES: Record<string, string> = { QUEUED: "排队中", UPLOADING: "上传中", SUBMITTED: "已提交，待公开确认", PUBLISHED: "已公开", UNKNOWN: "结果待核对", FAILED: "投稿失败，可重试", CANCELLED: "已取消" };
type Run = (action: () => Promise<unknown>) => Promise<void>;
function PlatformLink({ bvid, rpid, children }: { bvid: string; rpid?: string; children: React.ReactNode }) {
  return <a className="text-accent hover:underline inline-flex items-center gap-1 text-xs" target="_blank" rel="noreferrer" href={`https://www.bilibili.com/video/${bvid}${rpid ? `/#reply${rpid}` : ""}`}>{children}<ExternalLink className="w-3 h-3" /></a>;
}
export function UploadForm({ renderId, onDone, run, busy }: { renderId: string; onDone: (task: UploadTask) => void; run: Run; busy: boolean }) {
  const load = useCallback((signal: AbortSignal) => api<UploadPreview>(`${PLATFORM_API}/preview/${encodeURIComponent(renderId)}`, { signal }), [renderId]);
  const { data, error, reload } = usePageResource(load);
  if (error) return <ErrorState error={error} onRetry={reload} compact />;
  if (!data) return <p className="text-xs text-text-muted">正在读取发布包…</p>;
  if (data.existing_upload) return <section className="space-y-2"><p className="text-xs text-text-muted">这个版本已有投稿任务，请继续查看进度。</p><FocusedUpload taskId={data.existing_upload.id} canEdit run={run} busy={busy} /></section>;
  return <UploadEditor key={renderId} initial={data} onDone={onDone} run={run} busy={busy} />;
}

function UploadEditor({ initial, onDone, run, busy }: { initial: UploadPreview; onDone: (task: UploadTask) => void; run: Run; busy: boolean }) {
  const [title, setTitle] = useState(initial.draft?.payload.title ?? initial.title);
  const [description, setDescription] = useState(initial.draft?.payload.description ?? initial.description);
  const [tags, setTags] = useState((initial.draft?.payload.tags ?? initial.tags).join("，"));
  const [tid, setTid] = useState(initial.draft?.payload.tid ?? initial.tid ?? 0);
  const [firstComment, setFirstComment] = useState(initial.draft?.payload.first_comment ?? initial.first_comment ?? EMPTY_FIRST_COMMENT);
  const [saved, setSaved] = useState(Boolean(initial.draft));
  const content = { title, description, tid: Number(tid), tags: tags.split(/[,，]/).map(t => t.trim()).filter(Boolean), first_comment: firstComment };
  const textCheck = publishCheck("bilibili",content,initial.adaptation?.check.limits);
  const validComment = validFirstComment("bilibili",firstComment);
  const [templateSaved, setTemplateSaved] = useState(false);
  const role = useAuthStore(s => s.user?.role);
  const categoryResource = usePageResource(loadBilibiliCategories);
  const validCategory = categoryResource.data?.categories.some(row => row.tid === tid);
  return <form className="card p-4 space-y-3" onSubmit={(event) => { event.preventDefault(); void run(async () => {
    if (!validCategory) throw new Error("请选择投稿分区。");
    if (!textCheck.valid || !validComment) return;
    const task = await platformAction<UploadTask>("/uploads", { render_id: initial.render_id, account_uid: initial.account_uid, ...content }); onDone(task);
  }); }}>
    <div className="flex justify-between gap-3"><h3 className="text-sm font-medium">投稿信息</h3><span className="text-xs text-text-muted">{initial.credibility.text} · 自制视频</span></div>
    <label className="block text-xs">标题（{textCount(title.trim())}/80）<input aria-label="标题" required className="input w-full mt-1" value={title} onChange={e => {setTitle(e.target.value);setSaved(false);}} /></label>
    <label className="block text-xs">简介（{textCount(description)}/2000）<textarea aria-label="简介" rows={5} className="input w-full mt-1" value={description} onChange={e => {setDescription(e.target.value);setSaved(false);}} /></label>
    <div className="grid sm:grid-cols-[minmax(0,1fr)_minmax(280px,0.65fr)] gap-3"><label className="block text-xs">标签（用逗号分隔）<input required className="input w-full mt-1" value={tags} onChange={e => setTags(e.target.value)} /></label><BilibiliCategorySelect label="投稿分区" required value={tid} onChange={setTid} resource={categoryResource} content={{ title, description, tags: tags.split(/[,，]/).map(tag => tag.trim()).filter(Boolean) }} /></div>
    <FirstCommentOptions value={firstComment} onChange={value => { setFirstComment(value); setTemplateSaved(false); }} />
    <PublishAdaptation platform="bilibili" renderId={initial.render_id} accountUid={initial.account_uid} value={content} initial={initial.adaptation} run={run} busy={busy} onApply={next => {setTitle(next.title);setDescription(next.description);setTags(next.tags.join("，"));if(next.first_comment)setFirstComment(next.first_comment);if(next.tid!==undefined)setTid(next.tid);setSaved(true);}} />
    {textCheck.issues.length ? <div role="alert" className="text-xs text-warning">{textCheck.issues.map(issue => <p key={issue}>{issue}</p>)}</div> : null}
    {role === "admin" ? <div className="flex flex-wrap items-center gap-3"><button type="button" className="btn text-xs" disabled={busy || (firstComment.enabled && !firstComment.text.trim())} onClick={() => void run(async () => { await platformAction("/first-comment-template", firstComment, "PUT"); setTemplateSaved(true); })}>保存为常用评论</button><span className="text-xs text-text-muted">{templateSaved ? "已保存，后续投稿默认使用；当前还未发送。" : "可将内容和置顶选项保存为后续投稿的默认设置。"}</span></div> : null}
    {initial.warnings.map(w => <p className="text-xs text-warning" key={w}>{w}</p>)}
    <p className="text-xs text-text-muted">使用这次成片及已有封面；没有封面时自动截取视频画面。加入队列后会自动开始上传，B 站审核通过并公开后回写发布记录。</p>
    <div className="flex flex-wrap gap-3 items-center"><button type="button" className="btn text-xs" disabled={busy} onClick={() => void run(async () => { await api("/api/video/distributions/bilibili/draft", { method: "PUT", body: { render_id: initial.render_id, account_uid: initial.account_uid, payload: content } }); setSaved(true); })}>保存 B 站发布包</button>{saved ? <span className="text-xs text-text-muted">已保存本平台发布包；修改后请再次保存。</span> : null}</div>
    <button className="btn btn-primary text-xs" disabled={busy || !validCategory || !textCheck.valid || !validComment}><Send className="w-3.5 h-3.5" />加入投稿队列</button>
  </form>;
}

export function UploadRow({ task, canEdit, run, busy }: { task: UploadTask; canEdit: boolean; run: Run; busy: boolean }) {
  const [bvid, setBvid] = useState(task.bvid);
  return <li className="card p-4 space-y-2">
    <div className="flex flex-wrap justify-between gap-2"><Link className="text-sm font-medium hover:underline" to={documentPublishLink(task.script_id, task.version)}>{task.payload.title}</Link><span className={cn("badge border border-border", task.status === "UNKNOWN" && "text-warning")}>{UPLOAD_STATES[task.status] || task.status}</span></div>
    <div className="flex flex-wrap gap-3 text-xs text-text-muted"><span>文稿 v{task.version}</span><span>{new Date(task.created_at * 1000).toLocaleString("zh-CN")}</span>{task.bvid ? <PlatformLink bvid={task.bvid}>{task.bvid}</PlatformLink> : null}<Link className="text-accent hover:underline" to={documentPublishLink(task.script_id, task.version)}>查看文稿发布记录 →</Link></div>
    {task.status === "PUBLISHED" ? <p className="text-xs text-success">已自动回填文稿的发布链接、日期和已发布状态。</p> : ["QUEUED", "UPLOADING", "SUBMITTED"].includes(task.status) ? <p className="text-xs text-text-muted">确认视频公开后自动回填文稿发布记录。</p> : null}
    {task.error ? <p className="text-xs text-warning">{task.error}</p> : null}
    {task.recovery?.note ? <p className="text-xs text-text-muted" role="status">{task.recovery.note}</p> : task.status === "UNKNOWN" ? <p className="text-xs text-text-muted">系统会自动核对 B 站稿件，确认结果前保留原任务。</p> : null}
    <details className="rounded border border-border p-3 text-xs"><summary className="cursor-pointer text-text-muted">投稿时发布包</summary><div className="mt-2 space-y-2"><p className="whitespace-pre-wrap break-words">{task.payload.description}</p><p className="text-text-muted">{task.payload.tags?.join(" · ")}</p></div></details>
    <FirstCommentProgress task={task} canEdit={canEdit} run={run} busy={busy} />
    {canEdit && task.status === "QUEUED" ? <button disabled={busy} className="btn text-xs" onClick={() => void run(() => platformAction(`/uploads/${task.id}/cancel`))}>取消排队</button> : null}
    {canEdit && ["FAILED", "CANCELLED"].includes(task.status) ? <button disabled={busy} className="btn text-xs" onClick={() => void run(() => platformAction(`/uploads/${task.id}/retry`))}>重新检查并排队</button> : null}
    {canEdit && ["UNKNOWN", "SUBMITTED"].includes(task.status) ? <button disabled={busy} className="btn text-xs" onClick={() => void run(() => platformAction(`/uploads/${task.id}/lookup`))}>立即核对投稿结果</button> : null}
    {canEdit && ["UNKNOWN", "SUBMITTED"].includes(task.status) ? <form className="flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); void run(() => platformAction(`/uploads/${task.id}/reconcile`, { bvid })); }}><input aria-label="已投稿的 BV 号" required pattern="BV[0-9A-Za-z]{10}" className="input text-xs" value={bvid} onChange={e => setBvid(e.target.value)} placeholder="到创作中心核对后填写 BV 号" /><button disabled={busy} className="btn text-xs">核对并回写</button></form> : null}
  </li>;
}

function FirstCommentProgress({ task, canEdit, run, busy }: { task: UploadTask; canEdit: boolean; run: Run; busy: boolean }) {
  if (!task.payload.first_comment?.enabled) return null;
  const state = task.first_comment_status || "WAITING";
  const pinState = task.first_comment_pin_status;
  const labels: Record<string, string> = { WAITING: task.status === "PUBLISHED" ? "等待发送" : "待视频公开后发送", SENDING: "正在发送", SENT: "已发送", FAILED: "发送失败", UNKNOWN: "发送结果待核对" };
  const pinLabels: Record<string, string> = { WAITING: "待置顶", PINNING: "正在置顶", PINNED: "已置顶", FAILED: "置顶失败", UNKNOWN: "置顶结果待核对" };
  return <div className="rounded-lg border border-border p-3 space-y-2 text-xs" aria-label="首条评论进度">
    <div className="flex flex-wrap items-center gap-3"><span className="font-medium">首条评论 · {labels[state] || state}</span>{task.payload.first_comment.pin ? <span>{pinLabels[pinState || "WAITING"] || pinState}</span> : null}{task.first_comment_rpid && task.bvid ? <PlatformLink bvid={task.bvid} rpid={task.first_comment_rpid}>查看首条评论</PlatformLink> : null}</div>
    <p className="text-text-muted whitespace-pre-wrap break-words">{task.payload.first_comment.text}</p>
    {task.first_comment_error ? <p className="text-warning">{task.first_comment_error}</p> : null}
    {task.first_comment_pin_error ? <p className="text-warning">{task.first_comment_pin_error} 评论已发送，重试置顶不会重复评论。</p> : null}
    {canEdit && state === "FAILED" ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => platformAction(`/uploads/${task.id}/first-comment/retry`))}>重试首条评论</button> : null}
    {canEdit && state === "SENT" && ["FAILED", "UNKNOWN"].includes(pinState || "") ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => platformAction(`/uploads/${task.id}/first-comment/pin/retry`))}>仅重试置顶</button> : null}
    {state === "UNKNOWN" && task.bvid ? <PlatformLink bvid={task.bvid}>到视频下核对评论</PlatformLink> : null}
  </div>;
}

export function FocusedUpload({ taskId, canEdit, run, busy }: { taskId: string; canEdit: boolean; run: Run; busy: boolean }) {
  const load = useCallback((signal: AbortSignal) => api<UploadTask>(`${PLATFORM_API}/uploads/${encodeURIComponent(taskId)}`, { signal }), [taskId]);
  const { data, error, reload } = usePageResource(load, 6000);
  return <section className="space-y-2" aria-label="当前文稿投稿任务"><h2 className="text-sm font-medium">当前投稿任务</h2>{error ? <ErrorState error={error} onRetry={reload} compact /> : null}{data ? <ul><UploadRow task={data} canEdit={canEdit} run={async action => { await run(action); await reload(); }} busy={busy} /></ul> : !error ? <p className="text-xs text-text-muted">正在读取投稿进度…</p> : null}</section>;
}
