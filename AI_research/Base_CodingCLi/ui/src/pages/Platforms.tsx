import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { BarChart3, CheckCircle2, ExternalLink, MessageSquareText, RefreshCw, Send, Settings, UploadCloud } from "lucide-react";
import { api } from "@/api/client";
import { EMPTY_FIRST_COMMENT, PLATFORM_API, loadPlatforms, loadBilibiliCategories, platformAction, type PlatformOverview, type PlatformComment, type PlatformSettings, type UploadPreview, type UploadTask, type UploadCandidate } from "@/api/platforms";
import { useAuthStore } from "@/lib/auth-store";
import { usePageResource } from "@/lib/use-page-resource";
import { errorMessage } from "@/lib/toast";
import { ErrorState } from "@/components/ErrorState";
import { BilibiliCategorySelect } from "@/components/BilibiliCategorySelect";
import { FirstCommentOptions } from "@/components/FirstCommentOptions";
import { VideoAnalytics } from "@/components/VideoAnalytics";
import { cn } from "@/lib/utils";
import { documentPublishLink } from "@/lib/platform-links";

const UPLOAD_STATES: Record<string, string> = { QUEUED: "排队中", UPLOADING: "上传中", SUBMITTED: "已投稿 · 待平台审核", PUBLISHED: "已公开", UNKNOWN: "结果待核对", FAILED: "投稿前检查失败", CANCELLED: "已取消" };
const COMMENT_STATES: Record<string, string> = { NEW: "待处理", HANDLED: "已处理", SENDING: "发送中", REPLIED: "已回复", UNKNOWN: "发送结果待核对" };
type Run = (action: () => Promise<unknown>) => Promise<void>;

function candidateLabel(row: UploadCandidate): string {
  const notes = [row.archived ? "已归档" : "", row.current_version && row.current_version !== row.version ? `历史版本，当前 v${row.current_version}` : ""].filter(Boolean);
  return `${row.title} · v${row.version}${notes.length ? ` · ${notes.join(" · ")}` : ""}`;
}

function CandidatePicker({ data, selected, onSelect }: { data: PlatformOverview; selected: string; onSelect: (value: string) => void }) {
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("current");
  const rows = data.candidates.filter(row => (!query.trim() || candidateLabel(row).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) &&
    (scope === "all" || (scope === "archived" && row.archived) || (scope === "history" && row.current_version !== undefined && row.version !== row.current_version) ||
      (scope === "current" && !row.archived && row.script_status === "APPROVED" && row.version === row.current_version && row.version === row.approved_version)));
  const chosen = data.candidates.find(row => row.render_id === selected);
  const summary = data.candidate_summary;
  return <section className="card p-4 space-y-3">
    <div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-medium">全部可投稿成片</h2><span className="text-xs text-text-muted">{data.candidates.length} 个文稿版本</span></div>
    {summary ? <p className="text-xs text-text-muted">已完成 {summary.completed_renders} 次出片，文件可用 {summary.available_versions} 个版本；已记录发布 {summary.published_versions} 个，已有投稿任务 {summary.queued_versions} 个{summary.unavailable_versions ? `，文件暂不可用 ${summary.unavailable_versions} 个` : ""}。</p> : <p className="text-xs text-warning">当前后端尚未加载完整成片列表；请在生成任务完成后重启后端，让最新筛选逻辑生效。</p>}
    <div className="flex flex-wrap gap-2"><input aria-label="搜索投稿成片" className="input text-xs flex-1 min-w-[180px]" placeholder="搜索视频标题或版本" value={query} onChange={e => setQuery(e.target.value)} /><select aria-label="成片范围" className="input text-xs w-auto" value={scope} onChange={e => setScope(e.target.value)}><option value="all">全部可投稿</option><option value="current">当前定稿</option><option value="archived">已归档成片</option><option value="history">历史版本</option></select></div>
    <label className="block text-sm font-medium">选择成片<select aria-label="选择投稿成片" className="input w-full mt-2" value={selected} onChange={e => onSelect(e.target.value)}><option value="">选择已有成片，包含归档和历史版本</option>{chosen && !rows.some(row => row.render_id === selected) ? <option value={chosen.render_id}>{candidateLabel(chosen)}</option> : null}{rows.map(row => <option key={row.render_id} value={row.render_id}>{candidateLabel(row)}</option>)}</select></label>
    <p className="text-xs text-text-muted">显示 {rows.length} / {data.candidates.length} 个版本。同一文稿同一版本保留最近一次可用成片；历史版发布信息按该版生成，文稿后续修改不会隐藏已有成片。</p>
    {!data.biliup_ready ? <p className="text-xs text-warning">需要先安装 biliup 原生 CLI。可先预览和编辑发布包。</p> : null}
    {data.account.upload_authorized === false ? <p className="text-xs text-warning">当前账号缺少上传授权，请到<Link className="text-accent underline" to="/platforms?tab=settings">账号与策略</Link>重新扫码连接。</p> : null}
    {!data.candidates.length ? <Link to="/publish" className="text-xs text-accent">暂无可投稿成片，前往成片与发布 →</Link> : !rows.length ? <p className="text-xs text-text-muted">没有符合搜索或范围筛选的成片。</p> : null}
  </section>;
}

function PlatformLink({ bvid, rpid, children }: { bvid: string; rpid?: string; children: React.ReactNode }) {
  return <a className="text-accent hover:underline inline-flex items-center gap-1 text-xs" target="_blank" rel="noreferrer" href={`https://www.bilibili.com/video/${bvid}${rpid ? `/#reply${rpid}` : ""}`}>{children}<ExternalLink className="w-3 h-3" /></a>;
}

function UploadForm({ renderId, onDone, run, busy }: { renderId: string; onDone: (task: UploadTask) => void; run: Run; busy: boolean }) {
  const load = useCallback((signal: AbortSignal) => api<UploadPreview>(`${PLATFORM_API}/preview/${encodeURIComponent(renderId)}`, { signal }), [renderId]);
  const { data, error, reload } = usePageResource(load);
  if (error) return <ErrorState error={error} onRetry={reload} compact />;
  if (!data) return <p className="text-xs text-text-muted">正在读取发布包…</p>;
  if (data.existing_upload) return <section className="space-y-2"><p className="text-xs text-text-muted">这个版本已有投稿任务，请继续查看进度。</p><FocusedUpload taskId={data.existing_upload.id} canEdit run={run} busy={busy} /></section>;
  return <UploadEditor key={renderId} initial={data} onDone={onDone} run={run} busy={busy} />;
}

function UploadEditor({ initial, onDone, run, busy }: { initial: UploadPreview; onDone: (task: UploadTask) => void; run: Run; busy: boolean }) {
  const [title, setTitle] = useState(initial.title);
  const [description, setDescription] = useState(initial.description);
  const [tags, setTags] = useState(initial.tags.join("，"));
  const [tid, setTid] = useState(initial.tid || 0);
  const [firstComment, setFirstComment] = useState(initial.first_comment || EMPTY_FIRST_COMMENT);
  const [templateSaved, setTemplateSaved] = useState(false);
  const role = useAuthStore(s => s.user?.role);
  const categoryResource = usePageResource(loadBilibiliCategories);
  const validCategory = categoryResource.data?.categories.some(row => row.tid === tid);
  return <form className="card p-4 space-y-3" onSubmit={(event) => { event.preventDefault(); void run(async () => {
    if (!validCategory) throw new Error("请选择投稿分区。");
    const task = await platformAction<UploadTask>("/uploads", { render_id: initial.render_id, title, description, tid: Number(tid), tags: tags.split(/[,，]/).map(t => t.trim()).filter(Boolean), first_comment: firstComment }); onDone(task);
  }); }}>
    <div className="flex justify-between gap-3"><h3 className="text-sm font-medium">投稿信息</h3><span className="text-xs text-text-muted">{initial.credibility.text} · 自制视频</span></div>
    <label className="block text-xs">标题<input required maxLength={80} className="input w-full mt-1" value={title} onChange={e => setTitle(e.target.value)} /></label>
    <label className="block text-xs">简介<textarea required maxLength={2000} rows={5} className="input w-full mt-1" value={description} onChange={e => setDescription(e.target.value)} /></label>
    <div className="grid sm:grid-cols-[minmax(0,1fr)_minmax(280px,0.65fr)] gap-3"><label className="block text-xs">标签（用逗号分隔）<input required className="input w-full mt-1" value={tags} onChange={e => setTags(e.target.value)} /></label><BilibiliCategorySelect label="投稿分区" required value={tid} onChange={setTid} resource={categoryResource} content={{ title, description, tags: tags.split(/[,，]/).map(tag => tag.trim()).filter(Boolean) }} /></div>
    <FirstCommentOptions value={firstComment} onChange={value => { setFirstComment(value); setTemplateSaved(false); }} />
    {role === "admin" ? <div className="flex flex-wrap items-center gap-3"><button type="button" className="btn text-xs" disabled={busy || (firstComment.enabled && !firstComment.text.trim())} onClick={() => void run(async () => { await platformAction("/first-comment-template", firstComment, "PUT"); setTemplateSaved(true); })}>保存为常用评论</button><span className="text-xs text-text-muted">{templateSaved ? "已保存，后续投稿默认使用；当前还未发送。" : "可将内容和置顶选项保存为后续投稿的默认设置。"}</span></div> : null}
    {initial.warnings.map(w => <p className="text-xs text-warning" key={w}>{w}</p>)}
    <p className="text-xs text-text-muted">使用这次成片及已有封面；没有封面时自动截取视频画面。加入队列后会自动开始上传，B 站审核通过并公开后回写发布记录。</p>
    <button className="btn btn-primary text-xs" disabled={busy || !validCategory || (firstComment.enabled && !firstComment.text.trim())}><Send className="w-3.5 h-3.5" />加入投稿队列</button>
  </form>;
}

function UploadRow({ task, canEdit, run, busy }: { task: UploadTask; canEdit: boolean; run: Run; busy: boolean }) {
  const [bvid, setBvid] = useState(task.bvid);
  return <li className="card p-4 space-y-2">
    <div className="flex flex-wrap justify-between gap-2"><Link className="text-sm font-medium hover:underline" to={documentPublishLink(task.script_id, task.version)}>{task.payload.title}</Link><span className={cn("badge border border-border", task.status === "UNKNOWN" && "text-warning")}>{UPLOAD_STATES[task.status] || task.status}</span></div>
    <div className="flex flex-wrap gap-3 text-xs text-text-muted"><span>文稿 v{task.version}</span><span>{new Date(task.created_at * 1000).toLocaleString("zh-CN")}</span>{task.bvid ? <PlatformLink bvid={task.bvid}>{task.bvid}</PlatformLink> : null}<Link className="text-accent hover:underline" to={documentPublishLink(task.script_id, task.version)}>查看文稿发布记录 →</Link></div>
    {task.status === "PUBLISHED" ? <p className="text-xs text-success">已自动回填文稿的发布链接、日期和已发布状态。</p> : ["QUEUED", "UPLOADING", "SUBMITTED"].includes(task.status) ? <p className="text-xs text-text-muted">确认视频公开后自动回填文稿发布记录。</p> : null}
    {task.error ? <p className="text-xs text-warning">{task.error}</p> : null}
    <FirstCommentProgress task={task} canEdit={canEdit} run={run} busy={busy} />
    {canEdit && task.status === "QUEUED" ? <button disabled={busy} className="btn text-xs" onClick={() => void run(() => platformAction(`/uploads/${task.id}/cancel`))}>取消排队</button> : null}
    {canEdit && ["FAILED", "CANCELLED"].includes(task.status) ? <button disabled={busy} className="btn text-xs" onClick={() => void run(() => platformAction(`/uploads/${task.id}/retry`))}>重新检查并排队</button> : null}
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

function FocusedUpload({ taskId, canEdit, run, busy }: { taskId: string; canEdit: boolean; run: Run; busy: boolean }) {
  const load = useCallback((signal: AbortSignal) => api<UploadTask>(`${PLATFORM_API}/uploads/${encodeURIComponent(taskId)}`, { signal }), [taskId]);
  const { data, error, reload } = usePageResource(load, 6000);
  return <section className="space-y-2" aria-label="当前文稿投稿任务"><h2 className="text-sm font-medium">当前投稿任务</h2>{error ? <ErrorState error={error} onRetry={reload} compact /> : null}{data ? <ul><UploadRow task={data} canEdit={canEdit} run={async action => { await run(action); await reload(); }} busy={busy} /></ul> : !error ? <p className="text-xs text-text-muted">正在读取投稿进度…</p> : null}</section>;
}

function CommentRow({ row, canEdit, run, busy }: { row: PlatformComment; canEdit: boolean; run: Run; busy: boolean }) {
  const [draft, setDraft] = useState(row.draft);
  const [threadPage, setThreadPage] = useState(1);
  const [more, setMore] = useState(false);
  const writable = ["NEW", "HANDLED"].includes(row.status);
  return <li className="card p-4 space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><div className="text-xs"><b>{row.author}</b><span className="text-text-muted ml-2">{new Date(row.ctime * 1000).toLocaleString("zh-CN")} · {row.likes} 赞</span></div><span className="badge border border-border">{COMMENT_STATES[row.status] || row.status}</span></div>
    <p className="text-sm whitespace-pre-wrap break-words">{row.message}</p>
    <div className="flex flex-wrap gap-3 items-center"><PlatformLink bvid={row.bvid} rpid={row.rpid}>{row.root !== row.rpid ? "查看楼中楼" : "查看原评论"}</PlatformLink>{canEdit && row.root === row.rpid ? <button disabled={busy} className="text-xs text-accent" onClick={() => void run(async () => { const result = await platformAction<{ has_more: boolean }>("/sync", { bvid: row.bvid, root: row.rpid, page: more ? threadPage + 1 : 1 }); setThreadPage(more ? threadPage + 1 : 1); setMore(result.has_more); })}>{more ? "继续读取楼中楼" : "读取完整楼中楼"}</button> : null}</div>
    {row.reply ? <p className="text-xs text-text-muted whitespace-pre-wrap">{row.status === "REPLIED" ? "已发送：" : "发送内容："}{row.reply}</p> : null}
    {row.error ? <p className="text-xs text-warning">{row.error}</p> : null}
    {canEdit && writable ? <div className="space-y-2"><label className="block text-xs">回复草稿<textarea aria-label={`回复 ${row.author}`} rows={2} maxLength={1000} className="input w-full mt-1" value={draft} onChange={e => setDraft(e.target.value)} /></label><div className="flex flex-wrap gap-2"><button disabled={busy} className="btn text-xs" onClick={() => void run(() => platformAction(`/comments/${row.bvid}/${row.rpid}`, { draft, handled: row.status === "HANDLED" }, "PUT"))}>保存草稿</button><button disabled={busy || !draft.trim()} className="btn btn-primary text-xs" onClick={() => void run(() => platformAction(`/comments/${row.bvid}/${row.rpid}/reply`, { text: draft }))}>发送回复</button><button disabled={busy} className="btn text-xs" onClick={() => void run(() => platformAction(`/comments/${row.bvid}/${row.rpid}`, { draft, handled: row.status !== "HANDLED" }, "PUT"))}>{row.status === "HANDLED" ? "恢复待处理" : "标记已处理"}</button></div></div> : null}
  </li>;
}

function Connection({ data, run, busy }: { data: PlatformOverview; run: Run; busy: boolean }) {
  const [qr, setQr] = useState("");
  const [qrStatus, setQrStatus] = useState("");
  const [credential, setCredential] = useState("");
  useEffect(() => {
    if (!qr) return;
    let cancelled = false;
    let timer: number;
    const poll = async () => {
      try {
        const result = await platformAction<{ status: string }>("/login/poll");
        if (cancelled) return;
        if (result.status === "done") { setQr(""); await run(async () => {}); }
        setQrStatus(result.status);
        if (["waiting", "scanned"].includes(result.status)) timer = window.setTimeout(poll, 3000);
      } catch (error) { if (!cancelled) setQrStatus(errorMessage(error, "扫码状态读取失败")); }
    };
    timer = window.setTimeout(poll, 3000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [qr, run]);
  return <section className="card p-4 space-y-3">
    <h2 className="text-sm font-medium">账号连接</h2>
    {data.account.connected ? <p className="text-sm">{data.account.name || "已连接"}<span className="text-xs text-text-muted ml-2">UID {data.account.uid}</span></p> : <p className="text-sm text-text-muted">连接你的哔哩哔哩账号后，可以投稿并管理自己视频的评论。</p>}
    {data.account.upload_authorized === false ? <p className="text-xs text-warning">当前登录可管理评论，但缺少视频上传授权。请重新扫码连接以启用投稿。</p> : null}
    <div className="flex flex-wrap gap-2"><button disabled={busy} className="btn btn-primary text-xs" onClick={() => void run(async () => { const result = await platformAction<{ image: string }>("/login"); setQr(result.image); setQrStatus("waiting"); })}>{data.account.connected ? "重新扫码连接" : "扫码连接"}</button>{data.account.connected ? <><button disabled={busy} className="btn text-xs" onClick={() => void run(() => platformAction("/verify"))}>验证登录</button><button disabled={busy} className="btn text-xs" onClick={() => void run(() => platformAction("/account", undefined, "DELETE"))}>断开连接</button></> : null}</div>
    {qr ? <div className="space-y-2"><img src={qr} width={200} height={200} alt="请使用哔哩哔哩 App 扫码登录" className="rounded bg-white p-2" /><p className="text-xs text-text-muted">{qrStatus === "waiting" ? "请使用哔哩哔哩 App 扫码，二维码 180 秒内有效。" : qrStatus === "scanned" ? "已扫码，请在手机上确认。" : qrStatus === "expired" ? "二维码已过期，请重新扫码连接。" : qrStatus}</p></div> : null}
    <details className="text-xs text-text-muted"><summary className="cursor-pointer">导入已有登录凭证</summary><form className="space-y-2 mt-2" onSubmit={e => { e.preventDefault(); void run(async () => { let parsed: unknown; try { parsed = JSON.parse(credential); } catch { throw new Error("凭证需要是 JSON 格式。"); } await platformAction("/credentials", { credentials: parsed }); setCredential(""); }); }}><p>支持 biliup 的完整 cookies.json；bilibili-mcp 的网页凭证可用于评论，投稿需重新扫码取得上传授权。凭证验证后保存在本机。</p><textarea aria-label="登录凭证 JSON" className="input w-full" autoComplete="off" spellCheck={false} rows={3} value={credential} onChange={e => setCredential(e.target.value)} /><button disabled={busy || !credential.trim()} className="btn text-xs">验证并导入</button></form></details>
  </section>;
}

function Policy({ initial, run, busy, ready }: { initial: PlatformSettings; run: Run; busy: boolean; ready: boolean }) {
  const [config, setConfig] = useState(initial);
  const categoryResource = usePageResource(loadBilibiliCategories);
  const validCategory = categoryResource.data?.categories.some(row => row.tid === config.tid);
  const firstComment = config.first_comment || EMPTY_FIRST_COMMENT;
  return <form className="card p-4 space-y-4" onSubmit={e => { e.preventDefault(); void run(() => platformAction("/settings", { auto_upload: config.auto_upload, sync_comments: config.sync_comments, tid: config.tid, first_comment: firstComment }, "PUT")); }}>
    <h2 className="text-sm font-medium">自动化策略</h2>
    <BilibiliCategorySelect label="默认投稿分区" required={config.auto_upload} value={config.tid} onChange={tid => setConfig({ ...config, tid })} resource={categoryResource} />
    <label className="flex gap-2 text-sm items-center"><input type="checkbox" checked={config.auto_upload} disabled={!ready && !config.auto_upload} onChange={e => setConfig({ ...config, auto_upload: e.target.checked })} />出片后自动投稿</label>
    <p className="text-xs text-text-muted">保存启用后，只处理此后完成的当前定稿成片。沿用发布包和封面，使用自制投稿；未审计、有待核实、严重事实问题或时效复核提醒会保留在成片列表供人工处理。旧成片可在投稿页单独选择。</p>
    <label className="flex gap-2 text-sm items-center"><input type="checkbox" checked={config.sync_comments} onChange={e => setConfig({ ...config, sync_comments: e.target.checked })} />每 5 分钟同步已管理视频的最新评论</label>
    <p className="text-xs text-text-muted">每轮读取每个视频的最新一页和楼中楼预览；更早评论和完整楼中楼可继续读取。回复由你编辑并点击发送。</p>
    <FirstCommentOptions template value={firstComment} onChange={value => setConfig({ ...config, first_comment: value })} />
    <button disabled={busy || (config.auto_upload && !validCategory) || (firstComment.enabled && !firstComment.text.trim())} className="btn btn-primary text-xs">保存策略</button>
  </form>;
}

export function PlatformsPage() {
  const { data, error, loading, reload } = usePageResource(loadPlatforms, 6000);
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "uploads";
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [selected, setSelected] = useState(params.get("render") || "");
  const renderParam = params.get("render") || "";
  const taskId = params.get("task") || "";
  const sourceScript = params.get("from_script") || "";
  const sourceVersion = Number(params.get("version"));
  useEffect(() => setSelected(renderParam), [renderParam]);
  const [bvid, setBvid] = useState(params.get("bvid") || "");
  useEffect(() => { setBvid(params.get("bvid") || ""); }, [params]);
  const [filter, setFilter] = useState("NEW");
  const [page, setPage] = useState(1);
  const [more, setMore] = useState(false);
  const role = useAuthStore(s => s.user?.role);
  const canEdit = role === "admin" || role === "editor";
  const run = useCallback<Run>(async action => {
    setBusy(true); setActionError("");
    try { await action(); await reload(); } catch (error) { setActionError(errorMessage(error, "操作失败")); }
    finally { setBusy(false); }
  }, [reload]);
  const comments = data?.comments.filter(c => (!bvid || c.bvid === bvid) && (!filter || c.status === filter)) || [];
  const pending = data?.comments.filter(c => c.status === "NEW").length || 0;
  const changeTab = (value: string) => setParams(previous => { const next = new URLSearchParams(previous); next.set("tab", value); return next; });
  return <div className="space-y-5 max-w-6xl">
    <header className="flex flex-wrap justify-between items-start gap-3"><div><h1 className="text-xl font-semibold tracking-tight">平台管理</h1><p className="text-xs text-text-muted mt-1">从成片投稿到评论处理，集中管理哔哩哔哩账号。</p></div><button className="btn text-xs" disabled={loading} onClick={() => void reload()}><RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />刷新平台</button></header>
    {sourceScript && Number.isInteger(sourceVersion) && sourceVersion > 0 ? <Link className="text-sm text-accent hover:underline block" to={documentPublishLink(sourceScript, sourceVersion)}>← 返回文稿发布页（v{sourceVersion}）</Link> : null}
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    {actionError ? <div role="alert" className="card p-3 text-sm text-danger">{actionError}</div> : null}
    {!data && !error ? <p className="text-sm text-text-muted">正在读取平台…</p> : null}
    {data ? <>
      <section className="card p-4 flex flex-wrap items-center gap-4"><div className="w-11 h-11 rounded-xl bg-accent/10 text-accent flex items-center justify-center font-bold text-xl">哔</div><div className="flex-1"><h2 className="font-medium text-sm">哔哩哔哩</h2><p className="text-xs text-text-muted mt-1">{data.account.connected ? `${data.account.name || "已连接"} · UID ${data.account.uid}` : "尚未连接账号"}</p></div><span className={cn("text-xs", data.biliup_ready ? "text-success" : "text-warning")}>{data.biliup_ready ? "投稿组件可用" : "投稿组件待安装"}</span><a href="https://member.bilibili.com/platform/upload-manager/article" target="_blank" rel="noreferrer" className="btn text-xs">创作中心<ExternalLink className="w-3 h-3" /></a></section>
      <div role="tablist" className="flex gap-1 border-b border-border">{[{ id: "uploads", label: "视频投稿", icon: UploadCloud }, { id: "data", label: "视频数据", icon: BarChart3 }, { id: "comments", label: `评论收件箱${pending ? ` · ${pending}` : ""}`, icon: MessageSquareText }, { id: "settings", label: "账号与策略", icon: Settings }].map(item => <button key={item.id} role="tab" aria-selected={tab === item.id} className={cn("px-3 py-2 text-sm flex gap-2 items-center border-b-2", tab === item.id ? "text-accent border-accent" : "text-text-muted border-transparent")} onClick={() => changeTab(item.id)}><item.icon className="w-4 h-4" />{item.label}</button>)}</div>
      {tab === "data" && data.account.connected ? <VideoAnalytics key={data.account.uid} isAdmin={role === "admin"} canEdit={canEdit} /> : null}
      {!data.account.connected && tab !== "settings" ? <section className="card p-8 text-center space-y-3"><p className="text-sm text-text-muted">先连接哔哩哔哩账号，再开始投稿和管理评论。</p><button className="btn btn-primary text-xs" onClick={() => changeTab("settings")}>前往账号连接</button></section> : null}
      {tab === "uploads" ? <div className="space-y-4">
        {canEdit && data.account.connected ? <CandidatePicker data={data} selected={selected} onSelect={value => { setSelected(value); setParams(previous => { const next = new URLSearchParams(previous); next.delete("task"); if (value) next.set("render", value); else next.delete("render"); return next; }); }} /> : null}
        {taskId && data.account.connected ? <FocusedUpload key={taskId} taskId={taskId} canEdit={canEdit} run={run} busy={busy} /> : null}
        {selected && !taskId && canEdit && data.account.connected ? <UploadForm key={selected} renderId={selected} run={run} busy={busy || !data.biliup_ready} onDone={task => { setSelected(""); setParams(previous => { const next = new URLSearchParams(previous); next.delete("render"); if (task.id && data.publication_flow) next.set("task", task.id); return next; }); }} /> : null}
        <h2 className="text-sm font-medium">投稿队列与记录<span className="text-text-muted ml-2">{data.uploads.length}</span></h2>
        {data.uploads.length ? <ul className="space-y-3">{data.uploads.filter(task => task.id !== taskId).map(task => <UploadRow key={task.id} task={task} canEdit={canEdit} run={run} busy={busy} />)}</ul> : !taskId ? <p className="card p-8 text-center text-sm text-text-muted">还没有投稿任务。加入队列后可在这里查看进度。</p> : null}
      </div> : null}
      {tab === "comments" ? <div className="space-y-4">
        <section className="card p-4 space-y-3"><div className="flex flex-wrap gap-2"><select aria-label="评论视频" className="input text-xs flex-1 min-w-[180px] w-auto" value={bvid} onChange={e => { setBvid(e.target.value); setPage(1); setMore(false); }}><option value="">全部已管理视频</option>{bvid && !data.videos.some(v => v.bvid === bvid) ? <option value={bvid}>{bvid} · 待同步评论</option> : null}{data.videos.map(v => <option key={v.bvid} value={v.bvid}>{v.title}</option>)}</select><select aria-label="评论状态" className="input text-xs w-auto" value={filter} onChange={e => setFilter(e.target.value)}><option value="">全部状态</option>{Object.entries(COMMENT_STATES).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select>{canEdit ? <button className="btn text-xs" disabled={busy || !bvid} onClick={() => void run(async () => { const result = await platformAction<{ has_more: boolean }>("/sync", { bvid }); setPage(1); setMore(result.has_more); })}>同步最新评论</button> : null}{canEdit && more ? <button disabled={busy || !bvid} className="btn text-xs" onClick={() => void run(async () => { const result = await platformAction<{ has_more: boolean }>("/sync", { bvid, page: page + 1 }); setPage(page + 1); setMore(result.has_more); })}>继续读取更早评论</button> : null}</div>
          {canEdit && data.account.connected ? <form className="flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); const input = new FormData(e.currentTarget).get("bvid") as string; void run(async () => { await platformAction("/videos", { bvid: input }); setBvid(input); setPage(1); setMore(false); await platformAction("/sync", { bvid: input }); }); }}><input aria-label="添加视频 BV 号" name="bvid" required pattern="BV[0-9A-Za-z]{10}" placeholder="添加已发布视频的 BV 号" className="input text-xs flex-1 min-w-0" /><button disabled={busy} className="btn text-xs">添加并同步</button></form> : null}
          <p className="text-xs text-text-muted">最近同步的 500 条评论；主列表含楼中楼预览，可逐页读取完整对话。已发送的回复有本地记录，重复同步会保留处理状态。</p>
          {data.videos.find(v => v.bvid === bvid)?.sync_error ? <p className="text-xs text-warning">上次同步失败：{data.videos.find(v => v.bvid === bvid)?.sync_error}</p> : null}
        </section>
        {comments.length ? <ul className="space-y-3">{comments.map(row => <CommentRow key={`${row.bvid}/${row.rpid}`} row={row} canEdit={canEdit} run={run} busy={busy} />)}</ul> : <section className="card p-8 text-center text-sm text-text-muted"><CheckCircle2 className="w-6 h-6 mx-auto mb-2" />当前筛选下没有评论。选择视频并同步后，新增评论会出现在这里。</section>}
      </div> : null}
      {tab === "settings" ? <div className="grid lg:grid-cols-2 gap-4 items-start">{role === "admin" ? <><Connection data={data} run={run} busy={busy} /><Policy key={JSON.stringify(data.settings)} initial={data.settings} run={run} busy={busy} ready={data.biliup_ready && data.account.connected} /></> : <p className="card p-6 text-sm text-text-muted">账号连接和自动化策略由管理员配置；编辑者可以投稿、同步评论和发送回复。</p>}<div className="card p-4 text-xs text-text-muted space-y-2 lg:col-span-2"><p>投稿组件：biliup 原生 CLI。安装后重启后端，或通过 AREC_BILIUP_BIN 指向已安装的可执行文件。</p><p>登录凭证仅保存在本机受限文件中；投稿、回复和处理状态保留在本地数据库。投稿结果待核对时，先到创作中心检查，再回填 BV 号。</p></div></div> : null}
    </> : null}
  </div>;
}
