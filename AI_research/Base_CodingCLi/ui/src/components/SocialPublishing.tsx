import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { socialAction, socialBase, type SocialDefinition, type SocialJob, type SocialOverview, type SocialPreview } from "@/api/social-publishing";
import { ErrorState } from "@/components/ErrorState";
import { useAuthStore } from "@/lib/auth-store";
import { documentPublishLink } from "@/lib/platform-links";
import { errorMessage } from "@/lib/toast";
import { usePageResource } from "@/lib/use-page-resource";
import { EMPTY_FIRST_COMMENT, type FirstComment } from "@/api/platforms";
import { FirstCommentOptions } from "./FirstCommentOptions";
import { PublishAdaptation } from "./PublishAdaptation";
import { combinedText, publishCheck, publishLimits, validFirstComment } from "@/lib/publish-text";

type Run = (action: () => Promise<unknown>) => Promise<void>;
const STATES: Record<string,string> = { QUEUED:"排队中", RUNNING:"处理中", SUBMITTED:"已投稿，待确认公开", UNKNOWN:"结果待核对", PUBLISHED:"已发布", FAILED:"未完成", CANCELLED:"已取消", DONE:"连接已验证", RECORDED:"已核对" };
const REVIEW_STATES: Record<string,string> = { reviewing:"平台审核中", processing:"平台处理中", scheduled:"等待定时发布", private:"仅自己可见", rejected:"审核未通过" };
const length = (value: string) => Array.from(value).length;
const commentPlatform = (platform:string) => platform === "xiaohongshu" ? "xiaohongshu" : "kuaishou";
function connectionLabel(account: SocialOverview["account"], spec: SocialDefinition): string {
  if (!account.connected) return "待账号连接验收";
  if (account.session?.state === "expired") return "登录核实失败 · 自动核对已暂停";
  if (account.session?.state === "unknown") return "账号已连接 · 登录状态待核实";
  return spec.capabilities.public_check ? "账号已核验 · 自动核对已开启" : "账号已核验 · 投稿待验收";
}
export function combinedCount(platform: string, title: string, description: string, tags: string[]) {
  return length(combinedText(platform,title.trim(),description,tags));
}

export function SocialUploadEditor({ platform, renderId, run, busy, onDone }: { platform: string; renderId: string; run: Run; busy: boolean; onDone: () => void }) {
  const load = useCallback((signal: AbortSignal) => api<SocialPreview>(`${socialBase(platform)}/preview/${encodeURIComponent(renderId)}`,{signal}),[platform,renderId]);
  const { data,error,reload } = usePageResource(load);
  return error ? <ErrorState error={error} onRetry={reload} compact /> : data ? <SocialPublishForm key={`${platform}:${renderId}`} initial={data} run={run} busy={busy} onDone={onDone} /> : <p className="text-xs text-text-muted">正在读取平台发布包…</p>;
}

function SocialPublishForm({ initial, run, busy, onDone }: { initial: SocialPreview; run: Run; busy: boolean; onDone: () => void }) {
  const spec = initial.definition;
  const [title,setTitle] = useState(initial.draft?.payload.title ?? initial.title);
  const [description,setDescription] = useState(initial.draft?.payload.description ?? initial.description);
  const [tags,setTags] = useState((initial.draft?.payload.tags ?? initial.tags).join("，"));
  const [saved,setSaved] = useState(Boolean(initial.draft));
  const [firstComment,setFirstComment] = useState(initial.draft?.payload.first_comment ?? initial.first_comment ?? EMPTY_FIRST_COMMENT);
  const admin=useAuthStore(s => s.user?.role)==="admin";
  const [templateSaved,setTemplateSaved]=useState(false);
  const list = tags.split(/[,，]/).map(tag => tag.trim().replace(/^#/,"")).filter(Boolean);
  const titleInvalid = length(title.trim()) < spec.title_min || length(title.trim()) > spec.title_limit;
  const descriptionInvalid = length(description) > spec.description_limit;
  const tagsOverLimit = list.length > spec.tag_limit;
  const tagLengthLimit = spec.tag_length_limit ?? 100;
  const tagTooLong = list.some(tag => length(tag) > tagLengthLimit);
  const tagHasNewline = list.some(tag => /[\r\n]/.test(tag));
  const textCheck = publishCheck(spec.id,{title,description,tags:list},initial.adaptation?.check.limits ?? {...publishLimits(spec.id),...spec});
  const errors = [
    titleInvalid ? `标题需为 ${spec.title_min}–${spec.title_limit} 字，当前 ${length(title.trim())} 字。` : "",
    descriptionInvalid ? `简介最多 ${spec.description_limit} 字，当前 ${length(description)} 字。` : "",
    tagsOverLimit ? `话题超限：当前 ${list.length} 个，最多 ${spec.tag_limit} 个，请删除 ${list.length - spec.tag_limit} 个。` : "",
    tagTooLong ? `每个话题最多 ${tagLengthLimit} 字，请缩短过长的话题。` : "",
    tagHasNewline ? "话题不能包含换行，请使用逗号分隔。" : "",
    ...textCheck.issues.filter(issue => issue.includes("合计超限")),
    spec.capabilities.first_comment && (firstComment.text.length > 250 || firstComment.enabled && !firstComment.text.trim()) ? "首条评论需填写内容，最多 250 字。" : "",
  ].filter(Boolean);
  const valid = errors.length === 0 && textCheck.valid;
  const payload = { title,description,tags:list,...(spec.capabilities.first_comment ? {first_comment:firstComment} : {}) };
  const changed = () => setSaved(false);
  return <form aria-label={`${spec.name}发布包`} className="card p-4 space-y-3" onSubmit={event => { event.preventDefault(); if (valid && spec.capabilities.upload) void run(async () => { await socialAction(spec.id,"/uploads",{render_id:initial.render_id,account_uid:initial.account_uid,...payload}); onDone(); }); }}>
    <h3 className="font-medium text-sm">{spec.name}发布包 · 文稿 v{initial.version}</h3>
    {initial.warnings.map((warning,index) => <p key={index} className="text-xs text-warning">{warning}</p>)}
    <label className="block text-xs">{spec.name}标题（{length(title.trim())}/{spec.title_limit}，至少 {spec.title_min} 字）<input aria-label={`${spec.name}标题`} aria-invalid={titleInvalid} className="input w-full mt-1" value={title} onChange={e => { setTitle(e.target.value); changed(); }} /></label>
    <label className="block text-xs">{spec.name}简介（{length(description)}/{spec.description_limit}）<textarea aria-label={`${spec.name}简介`} aria-invalid={descriptionInvalid} rows={5} className="input w-full mt-1" value={description} onChange={e => { setDescription(e.target.value); changed(); }} /></label>
    <label className="block text-xs">{spec.name}话题（{list.length}/{spec.tag_limit} 个，逗号分隔）<input aria-label={`${spec.name}话题`} aria-invalid={tagsOverLimit || tagTooLong || tagHasNewline} className="input w-full mt-1" value={tags} onChange={e => { setTags(e.target.value); changed(); }} /></label>
    {errors.length ? <div role="alert" className="text-xs text-warning space-y-1"><p>请先修改发布包：</p>{errors.map(error => <p key={error}>{error}</p>)}{tagsOverLimit ? <button type="button" className="btn text-xs" disabled={busy} onClick={() => { setTags(list.slice(0,spec.tag_limit).join("，")); changed(); }}>保留前 {spec.tag_limit} 个话题</button> : null}</div> : null}
    <PublishAdaptation platform={spec.id} renderId={initial.render_id} accountUid={initial.account_uid} value={payload} initial={initial.adaptation} run={run} busy={busy} onApply={next => {setTitle(next.title);setDescription(next.description);setTags(next.tags.join("，"));if(next.first_comment)setFirstComment(next.first_comment);setSaved(true);}} />
    <p className="text-xs text-text-muted">{spec.upload_note || "确认后将在本机独立窗口投稿。"}</p>
    {!spec.capabilities.public_check ? <p className="text-xs text-warning">{spec.public_check_gap} 投稿没有可靠回执时会保留待核对，不自动重投。</p> : null}
    {spec.capabilities.first_comment ? <><FirstCommentOptions platform={commentPlatform(spec.id)} value={firstComment} onChange={value => {setFirstComment(value);setTemplateSaved(false);changed();}} />{admin ? <div className="flex items-center gap-2"><button type="button" className="btn text-xs" disabled={busy || !validFirstComment(spec.id,firstComment)} onClick={() => void run(async () => {await socialAction(spec.id,"/first-comment-template",firstComment,"PUT");setTemplateSaved(true);})}>保存为{spec.name}常用评论</button>{templateSaved ? <span className="text-xs text-success">已保存，仅影响后续投稿。</span> : null}</div> : null}</> : <p className="text-xs text-text-muted">本平台的首评、置顶和视频数据仍待分别接入。</p>}
    <div className="flex gap-3 items-center flex-wrap"><button type="button" className="btn text-xs" disabled={busy} onClick={() => void run(async () => { await api(`/api/video/distributions/${spec.id}/draft`,{method:"PUT",body:{render_id:initial.render_id,account_uid:initial.account_uid,payload}}); setSaved(true); })}>保存{spec.name}发布包</button>{saved ? <span className="text-xs text-success">已保存本平台发布包</span> : null}<button className="btn btn-primary text-xs" disabled={busy || !valid || !spec.capabilities.upload}>确认发布到{spec.name}</button></div>
  </form>;
}

function LoginStatus({ platform, id, run, busy, onConnected, onRenew }: { platform: string; id: string; run: Run; busy: boolean; onConnected: () => void; onRenew: (id: string) => void }) {
  const load = useCallback((signal: AbortSignal) => api<SocialJob & { connected: boolean; image?: string }>(`${socialBase(platform)}/login/${id}`,{signal}),[platform,id]);
  const {data,error,reload}=usePageResource(load,2000);
  useEffect(() => { if (data?.connected) onConnected(); },[data?.connected,onConnected]);
  return <section aria-label="平台扫码连接" className="card p-4 space-y-3">
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    <p className="text-sm">{data?.phase || "正在准备独立扫码页面…"}</p>
    {data?.image ? <img src={data.image} alt="使用平台 App 扫码连接账号" width={220} height={220} className="bg-white p-2 rounded" /> : null}
    <p className="text-xs text-text-muted">在手机确认账号。平台需要短信或身份验证时，请在本机独立浏览器中完成。</p>
    {data?.error ? <p className="text-xs text-warning">{data.error}</p> : null}
    {!data?.connected ? <button className="btn text-xs" disabled={busy} onClick={() => void run(async () => {const result=await socialAction(platform,data?.login_purpose === "comments" ? "/comments/login/renew" : "/login/renew");onRenew(result.id);})}>重新获取二维码</button> : null}
  </section>;
}

function UploadRow({row,spec,accountUid,canEdit,run,busy}:{row:SocialJob;spec:SocialDefinition;accountUid?:string;canEdit:boolean;run:Run;busy:boolean}) {
  const [video,setVideo]=useState("");
  const [confirmedFor,setConfirmedFor]=useState<number | null>(null);
  const [addingComment,setAddingComment]=useState(false);
  const [comment,setComment]=useState<FirstComment>({...EMPTY_FIRST_COMMENT,enabled:true});
  const first=row.first_comment;
  const commentLabels:Record<string,string>={WAITING:"等待视频公开",QUEUED:"等待发送",RUNNING:"正在处理",SENT:"已取得发送回执，待回读",UNKNOWN:"结果待核对，不会重发",FAILED:"未完成",CANCELLED:"已取消"};
  const verification=row.verification;
  const pending=["UNKNOWN","SUBMITTED"].includes(row.status);
  const statusLabel=pending && verification?.paused ? (row.status==="UNKNOWN" ? "结果待核对 · 自动回查暂停" : "已投稿 · 自动核对暂停") : pending && verification?.automatic ? REVIEW_STATES[verification.state || ""] || (row.status==="UNKNOWN" ? "结果待核对 · 自动回查中" : "已投稿 · 自动核对中") : row.status==="SUBMITTED" && !row.work_id ? "已投稿 · 待补作品链接" : STATES[row.status] || row.status;
  return <li className="card p-4 space-y-2">
    <div className="flex gap-3 justify-between"><h3 className="text-sm font-medium">{row.payload.title}</h3><span className="badge border border-border">{row.progress?.label || statusLabel}</span></div>
    <p className="text-xs text-text-muted">文稿 v{row.version} · {row.phase}</p>
    {row.progress?.recovery && <div className="text-xs text-warning space-y-2"><p>{row.progress.recovery.message}</p>
      {row.progress.recovery.kind === 'connection' && <a className="btn text-xs inline-block" href="#platform-connection">检查平台连接</a>}
      {row.progress.recovery.resumable && row.progress.recovery.kind !== 'retry' && <p>处理后点击「核对发布结果」，继续检查这条原投稿。</p>}</div>}
    <div className="flex gap-3 flex-wrap text-xs"><Link className="text-accent" to={documentPublishLink(row.script_id,row.version)}>查看文稿发布记录 →</Link>{row.url ? <a className="text-accent" href={row.url} target="_blank" rel="noreferrer">查看平台作品</a> : null}<a className="text-accent" href={spec.creator_url} target="_blank" rel="noreferrer">打开创作中心 ↗</a></div>
    {row.work_id ? <p className="text-xs text-text-muted break-all">作品 ID：{row.work_id}</p> : null}
    {pending && verification?.paused ? !row.progress?.recovery && <p className="text-xs text-warning">{verification.pause_reason || "自动核对已暂停，请先恢复平台连接；原投稿保留，不会重新投稿。"}</p> : row.status==="SUBMITTED" && !row.work_id ? <p className="text-xs text-text-muted">{verification?.automatic ? "平台已接受投稿，正在自动关联作品并核对审核、公开状态；不会重复投稿。" : "平台已接受投稿，回执未提供公开作品 ID。请补充作品链接，继续核对审核与公开状态；不会重复投稿。"}</p> : null}
    {pending && verification?.automatic ? <div className="text-xs text-text-muted space-y-1"><p>自动核对已开启，确认公开后将自动回写文稿发布记录。回查只读取结果，不会重新投稿。</p>{verification.checked_at ? <p>最近核对：{new Date(verification.checked_at * 1000).toLocaleString()} · {verification.reason || "等待平台状态"}{verification.next_check_at ? ` · 下次核对：${new Date(verification.next_check_at * 1000).toLocaleTimeString()}` : ""}</p> : <p>投稿后将自动开始核对。</p>}</div> : null}
    {row.error ? <p className="text-xs text-warning">{row.error}</p> : null}
    {first ? <section aria-label={`${spec.name}首评进度`} className="rounded border border-border p-3 space-y-2 text-xs"><p className="font-medium">首条评论 · {first.read_back ? "已发送并回读" : commentLabels[first.status] || first.status}</p><p className="text-text-muted">{first.phase}</p><p className="whitespace-pre-wrap break-words">{first.text}</p>{first.comment_id ? <p className="text-text-muted">评论 ID：{first.comment_id}</p> : null}{first.error ? <p className="text-warning">{first.error}</p> : null}{canEdit && ["WAITING","QUEUED"].includes(first.status) ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => socialAction(spec.id,`/uploads/${row.id}/first-comment/cancel`))}>取消首评</button> : null}{canEdit && first.can_retry ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => socialAction(spec.id,`/uploads/${row.id}/first-comment/retry`))}>重新检查并发送首评</button> : null}{canEdit && ["UNKNOWN","SENT"].includes(first.status) ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => socialAction(spec.id,`/uploads/${row.id}/first-comment/check`))}>只读核对评论</button> : null}</section> : canEdit && spec.capabilities.first_comment && ["SUBMITTED","PUBLISHED"].includes(row.status) && row.work_id ? <button className="btn text-xs" onClick={() => setAddingComment(!addingComment)}>{addingComment ? "收起首评" : "添加首条评论"}</button> : null}
    {addingComment && !first ? <form aria-label={`为已有${spec.name}作品添加首评`} className="space-y-2" onSubmit={e => {e.preventDefault();void run(async () => {await socialAction(spec.id,`/uploads/${row.id}/first-comment`,comment);setAddingComment(false);});}}><FirstCommentOptions platform={commentPlatform(spec.id)} value={comment} onChange={setComment} /><button className="btn btn-primary text-xs" disabled={busy || !comment.enabled || !comment.text.trim() || comment.text.length>250}>确认安排首评</button></form> : null}
    {["UNKNOWN","SUBMITTED"].includes(row.status) && !spec.capabilities.public_check ? <p className="text-xs text-text-muted">请在创作中心核对实际结果，公开后可在文稿发布页手动补录。系统会保留本次任务，避免重复投稿。</p> : null}
    {canEdit && pending && (spec.capabilities.public_check || spec.ownership_check) ? <form className="flex flex-wrap gap-2" onSubmit={e => {e.preventDefault();void run(() => socialAction(spec.id,`/uploads/${row.id}/reconcile`,{video}));}}><input aria-label={`${spec.name}核对作品ID`} className="input flex-1 min-w-0 text-xs" value={video} placeholder={row.work_id || "可补充准确作品 ID 或完整链接，帮助关联作品"} onChange={e => setVideo(e.target.value)} /><button disabled={busy || (!spec.capabilities.public_check && !row.work_id && !video.trim())} className="btn text-xs">{spec.capabilities.public_check ? "核对发布结果" : "只读核对作品归属"}</button></form> : null}
    {canEdit && row.status==="UNKNOWN" && !row.work_id && row.can_confirm_absent ? <div className="border-t border-border pt-3 space-y-2">
      <label className="flex items-start gap-2 text-xs"><input type="checkbox" disabled={busy} checked={confirmedFor===row.updated_at} onChange={e => setConfirmedFor(e.target.checked ? row.updated_at : null)} /><span>我已核对该账号的作品管理（含审核中、定时作品），确认没有这条作品</span></label>
      <p className="text-xs text-text-muted">确认后恢复手动重试入口，保留此前尝试记录；不会立即发布。</p>
      <button className="btn text-xs" disabled={busy || !accountUid || confirmedFor!==row.updated_at} onClick={() => void run(() => socialAction(spec.id,`/uploads/${row.id}/confirm-absent`,{confirmed_absent:true,account_uid:accountUid,expected_updated_at:row.updated_at}))}>确认未发布，恢复重试</button>
    </div> : null}
    {canEdit && row.status==="QUEUED" ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => socialAction(spec.id,`/uploads/${row.id}/cancel`))}>取消排队</button> : null}
    {canEdit && row.can_retry && ["FAILED","CANCELLED"].includes(row.status) ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => socialAction(spec.id,`/uploads/${row.id}/retry`))}>重新检查并投稿</button> : null}
  </li>;
}

function CommentTemplate({initial,platform,name,run,busy}:{initial:FirstComment;platform:string;name:string;run:Run;busy:boolean}) {
  const [value,setValue]=useState(initial),[saved,setSaved]=useState(false);
  return <details className="card p-4"><summary className="text-sm cursor-pointer">{name}常用首评设置</summary><form className="mt-3 space-y-3" onSubmit={e => {e.preventDefault();void run(async () => {await socialAction(platform,"/first-comment-template",value,"PUT");setSaved(true);});}}><FirstCommentOptions template platform={commentPlatform(platform)} value={value} onChange={next => {setValue(next);setSaved(false);}} /><button className="btn text-xs" disabled={busy || value.text.length>250 || value.enabled && !value.text.trim()}>保存{name}常用评论</button>{saved ? <span className="text-xs text-success">已保存，仅影响后续投稿。</span> : null}</form></details>;
}

function FocusedTask({platform,id,spec,accountUid,canEdit,run,busy}:{platform:string;id:string;spec:SocialDefinition;accountUid?:string;canEdit:boolean;run:Run;busy:boolean}) {
  const load=useCallback((signal:AbortSignal) => api<SocialJob>(`${socialBase(platform)}/uploads/${encodeURIComponent(id)}`,{signal}),[platform,id]);
  const {data,error,reload}=usePageResource(load,4000);
  return <section aria-label="当前平台投稿任务" className="space-y-2"><h2 className="text-sm font-medium">当前投稿任务</h2>{error ? <ErrorState error={error} onRetry={reload} compact /> : data ? <ul><UploadRow row={data} spec={spec} accountUid={accountUid} canEdit={canEdit} run={async action => {await run(action);await reload();}} busy={busy} /></ul> : <p className="text-xs text-text-muted">正在读取投稿任务…</p>}</section>;
}

export function SocialPlatform({platform}:{platform:string}) {
  const [params,setParams]=useSearchParams();
  const load=useCallback((signal:AbortSignal) => api<SocialOverview>(socialBase(platform),{signal}),[platform]);
  const {data,error,loading,reload}=usePageResource(load,4000);
  const role=useAuthStore(s => s.user?.role), admin=role==="admin", canEdit=admin || role==="editor";
  const [busy,setBusy]=useState(false), [actionError,setActionError]=useState("");
  const [query,setQuery]=useState(""),[scope,setScope]=useState("current");
  const [loginId,setLoginId]=useState("");
  const selected=params.get("render") || "", task=params.get("task") || "";
  const source=params.get("from_script") || "", version=Number(params.get("version"));
  const run=useCallback<Run>(async action => {setBusy(true);setActionError("");try {await action();await reload();} catch(e){setActionError(errorMessage(e));}finally{setBusy(false);}},[reload]);
  const onConnected=useCallback(() => {setLoginId("");void reload();},[reload]);
  const spec=data?.definition;
  const candidates=(data?.candidates || []).filter(row => (!query || `${row.title} v${row.version}`.toLowerCase().includes(query.toLowerCase())) && (scope==="all" || (!row.archived && row.script_status==="APPROVED" && row.version===row.current_version && row.version===row.approved_version)));
  return <div className="max-w-6xl space-y-4">
    <header className="flex justify-between gap-3"><div><h1 className="text-xl font-semibold">平台管理{spec ? ` · ${spec.name}` : ""}</h1><p className="text-xs text-text-muted mt-1">账号连接、独立发布包和投稿回执。</p></div><button className="btn text-xs" disabled={loading} onClick={() => void reload()}>刷新平台</button></header>
    {source && version>0 ? <Link className="text-accent text-sm block" to={documentPublishLink(source,version)}>← 返回文稿发布页（v{version}）</Link> : null}
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    {actionError ? <p role="alert" className="card p-3 text-sm text-danger">{actionError}</p> : null}
    {!data && !error ? <p className="text-sm text-text-muted">正在读取平台…</p> : null}
    {data && spec ? <>
      <section className="card p-4 flex gap-4 flex-wrap items-center"><div className="flex-1"><h2 className="text-sm font-medium">{spec.name}</h2><p className="text-xs text-text-muted mt-1">{data.account.connected ? `${data.account.name} · UID ${data.account.uid}` : "尚未连接账号"}</p></div><span className="text-xs text-text-muted">{connectionLabel(data.account, spec)}</span><a className="btn text-xs" href={spec.creator_url} target="_blank" rel="noreferrer">创作中心 ↗</a></section>
      {data.account.connected && data.account.session?.state === "expired" ? <section role="alert" className="card p-4 border border-danger text-sm"><h2 className="font-medium">{spec.name}登录已失效</h2><p className="text-xs text-text-muted mt-1">连续 {data.account.session.failures} 次账号核实失败，作品回查与评论核对已暂停，每 30 分钟自动检查一次。请在下方重新扫码连接同一账号。</p></section> : null}
      <section id="platform-connection" className="card p-4 space-y-3"><h2 className="text-sm font-medium">{spec.name}账号连接</h2><p className="text-xs text-text-muted">使用本机独立浏览器扫码，核实实际账号身份后保存登录；各平台的凭证与队列独立。</p>{admin ? <div className="flex gap-2 flex-wrap"><button className="btn btn-primary text-xs" disabled={busy || !data.runtime_ready} onClick={() => void run(async () => {const result=await socialAction(platform,"/login");setLoginId(result.id);})}>{data.account.connected ? `重新扫码连接${spec.name}` : `扫码连接${spec.name}`}</button>{data.account.connected ? <><button className="btn text-xs" disabled={busy} onClick={() => void run(() => socialAction(platform,"/verify"))}>验证平台登录</button><button className="btn text-xs" disabled={busy} onClick={() => void run(() => socialAction(platform,"/account",undefined,"DELETE"))}>断开平台账号</button></> : null}</div> : <p className="text-xs text-text-muted">账号连接由管理员配置。</p>}{!data.runtime_ready ? <p className="text-xs text-warning">本机上传组件尚未准备好。</p> : null}</section>
      {admin && platform==="xiaohongshu" && data.account.connected && spec.capabilities.first_comment ? <section className="card p-4 space-y-2" aria-label="小红书评论登录"><h2 className="text-sm font-medium">评论登录</h2><p className="text-xs text-text-muted">{data.account.comments_verified_at ? "已核验主站评论账号；发送前仍会检查登录和作品归属。" : "首评需要主站登录，请用当前创作中心的同一小红书账号扫码。现有上传登录保留。"}</p><button className="btn text-xs" disabled={busy || !data.runtime_ready} onClick={() => void run(async () => {const result=await socialAction(platform,"/comments/login");setLoginId(result.id);})}>{data.account.comments_verified_at ? "重新连接评论登录" : "连接评论登录"}</button></section> : null}
      {admin && (loginId || data.login_id) ? <LoginStatus key={loginId || data.login_id} platform={platform} id={loginId || data.login_id!} run={run} busy={busy} onConnected={onConnected} onRenew={setLoginId} /> : null}
      {data.checks.filter(row => row.kind==="VERIFY").slice(0,1).map(row => <p key={row.id} className="text-xs text-warning">{row.phase}{row.error ? `：${row.error}` : ""}</p>)}
      {admin && data.account.connected && spec.capabilities.first_comment ? <CommentTemplate key={`${data.account.uid}:${JSON.stringify(data.settings?.first_comment)}`} initial={data.settings?.first_comment ?? EMPTY_FIRST_COMMENT} platform={platform} name={spec.name} run={run} busy={busy} /> : null}
      {canEdit && data.account.connected ? <><section className="card p-4 space-y-3"><h2 className="text-sm font-medium">选择{spec.name}投稿成片</h2><div className="flex gap-2"><input aria-label={`${spec.name}搜索成片`} className="input flex-1 min-w-0 text-xs" placeholder="搜索视频标题或版本" value={query} onChange={e => setQuery(e.target.value)} /><select aria-label={`${spec.name}成片范围`} className="input text-xs w-auto" value={scope} onChange={e => setScope(e.target.value)}><option value="current">当前已定稿</option><option value="all">全部可投稿</option></select></div><select aria-label={`${spec.name}投稿成片`} className="input w-full" value={selected} onChange={e => setParams(previous => {const next=new URLSearchParams(previous);next.delete("task");if(e.target.value) next.set("render",e.target.value);else next.delete("render");return next;})}><option value="">选择成片，预览本平台发布包</option>{selected && !candidates.some(row => row.render_id===selected) ? <option value={selected}>当前已选成片</option> : null}{candidates.map(row => <option key={row.render_id} value={row.render_id}>{row.title} · v{row.version}{row.archived ? " · 已归档" : ""}</option>)}</select><p className="text-xs text-text-muted">显示 {candidates.length} 个版本。其他平台的发布记录不会隐藏这里的成片。</p></section>{selected && !task ? <SocialUploadEditor key={`${platform}:${selected}`} platform={platform} renderId={selected} run={run} busy={busy || !data.runtime_ready} onDone={() => setParams(previous => {const next=new URLSearchParams(previous);next.delete("render");return next;})} /> : null}</> : null}
      {task && data.account.connected ? <FocusedTask platform={platform} id={task} spec={spec} accountUid={data.account.uid} canEdit={canEdit} run={run} busy={busy} /> : null}
      <h2 className="text-sm font-medium">{spec.name}投稿队列与记录 · {data.uploads.length}</h2>{data.uploads.length ? <ul className="space-y-3">{data.uploads.filter(row => row.id!==task).map(row => <UploadRow key={row.id} row={row} spec={spec} accountUid={data.account.uid} canEdit={canEdit} run={run} busy={busy} />)}</ul> : <p className="card p-8 text-center text-sm text-text-muted">尚无投稿任务。连接账号后可选择已有成片。</p>}
    </> : null}
  </div>;
}
