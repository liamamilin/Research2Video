import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "@/api/client";
import { DOUYIN_API, douyinAction, loadDouyin, type DouyinJob, type DouyinLogin } from "@/api/douyin";
import { EMPTY_FIRST_COMMENT, type FirstComment } from "@/api/platforms";
import { FirstCommentOptions } from "./FirstCommentOptions";
import { UploadEditor } from "./DouyinPublishing";
import { ErrorState } from "@/components/ErrorState";
import { useAuthStore } from "@/lib/auth-store";
import { documentPublishLink } from "@/lib/platform-links";
import { errorMessage } from "@/lib/toast";
import { usePageResource } from "@/lib/use-page-resource";
import { DouyinConnectionStatus } from "./DouyinConnectionStatus";

type Run = (action: () => Promise<unknown>) => Promise<void>;
const STATES: Record<string, string> = { QUEUED: "排队中", RUNNING: "处理中", SUBMITTED: "已投稿 · 待核对公开状态", PUBLISHED: "已公开", UNKNOWN: "结果待核对", FAILED: "未完成", CANCELLED: "已取消", DONE: "检查完成", RECORDED: "检查完成" };

function LoginStatus({ id, onConnected }: { id: string; onConnected: () => void }) {
  const load = useCallback((signal: AbortSignal) => api<DouyinLogin>(`${DOUYIN_API}/login/${id}`, { signal }), [id]);
  const { data, error, reload } = usePageResource(load, 2000);
  useEffect(() => { if (data?.connected) onConnected(); }, [data?.connected, onConnected]);
  return <section className="card p-4 space-y-3" aria-label="抖音扫码连接">
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    {data?.connected ? <p className="text-sm text-success">抖音账号已连接并验证。</p> : <>
      <p className="text-sm">{data?.phase || "正在准备抖音登录窗口…"}</p>
      {data?.image ? <img src={data.image} alt="使用抖音 App 扫码连接" width={220} height={220} className="bg-white rounded p-2" /> : null}
      <p className="text-xs text-text-muted">请使用抖音 App 扫码，并在手机确认。需要短信或其他身份验证时，请在本机弹出的独立浏览器中完成。</p>
      {data?.error ? <p role="alert" className="text-sm text-warning">{data.error}</p> : null}
    </>}
  </section>;
}

function UploadRow({ row, canEdit, run, busy, template }: { row: DouyinJob; canEdit: boolean; run: Run; busy: boolean; template?: FirstComment }) {
  const [video, setVideo] = useState("");
  const [addingComment, setAddingComment] = useState(false);
  const [comment, setComment] = useState<FirstComment>({ ...(template ?? EMPTY_FIRST_COMMENT), enabled: true, pin: false });
  const first = row.first_comment;
  const pendingCommentCheck = first && ["UNKNOWN", "SENT"].includes(first.status) && !first.read_back;
  const labels: Record<string, string> = { WAITING: "等待视频确认公开", QUEUED: "等待发送", RUNNING: "发送中", SENT: first?.read_back ? "已发送并回读" : "已取得评论回执，等待回读", UNKNOWN: "结果待核对，不会自动重发", FAILED: "未发送成功", CANCELLED: "已取消" };
  const verification = row.verification;
  return <li className="card p-4 space-y-2">
    <div className="flex justify-between gap-3"><span className="text-sm font-medium">{row.payload.title}</span><span className="badge border border-border">{STATES[row.status] || row.status}</span></div>
    <p className="text-xs text-text-muted">文稿 v{row.version} · {row.phase}</p>
    <div className="flex flex-wrap gap-3 text-xs"><Link className="text-accent" to={documentPublishLink(row.script_id, row.version)}>查看文稿发布记录 →</Link>{row.url ? <a className="text-accent" href={row.url} target="_blank" rel="noreferrer">打开抖音作品</a> : null}</div>
    {row.work_id ? <p className="text-xs text-text-muted">作品 ID：{row.work_id}</p> : null}
    {verification?.automatic ? <p className="text-xs text-text-muted">{["QUEUED", "RUNNING"].includes(verification.status) ? "正在自动核对公开状态" : "自动核对已启用，完成后约 2 分钟重查"}{verification.count ? ` · 已检查 ${verification.count} 次` : ""}{verification.checked_at ? ` · 最近检查 ${new Date(verification.checked_at * 1000).toLocaleString("zh-CN")}` : ""}</p> : null}
    {verification?.reason ? <p className="text-xs text-warning">{verification.reason}</p> : null}
    {first ? <section className="rounded border border-border p-3 space-y-2 text-xs" aria-label="抖音作者评论进度"><p className="font-medium">作者评论 · {labels[first.status] || first.status}</p><p className="whitespace-pre-wrap break-words">{first.text}</p>{first.comment_id ? <p className="text-text-muted break-all">评论 ID：{first.comment_id}</p> : null}{first.error ? <p className="text-warning">{first.error}</p> : null}{canEdit && ["WAITING", "QUEUED"].includes(first.status) ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction(`/uploads/${row.id}/first-comment/cancel`))}>取消作者评论</button> : null}{canEdit && first.can_retry ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction(`/uploads/${row.id}/first-comment/retry`))}>重新检查并发送评论</button> : null}{pendingCommentCheck && canEdit ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction(`/uploads/${row.id}/first-comment/check`))}>只读核对评论</button> : null}{pendingCommentCheck && row.url ? <a className="text-accent" href={row.url} target="_blank" rel="noreferrer">到作品下核对评论</a> : null}</section> : canEdit && ["SUBMITTED", "PUBLISHED"].includes(row.status) && row.work_id ? <button className="btn text-xs" onClick={() => setAddingComment(!addingComment)}>{addingComment ? "收起作者评论" : "添加作者评论"}</button> : null}
    {addingComment && !first ? <form aria-label="为已有抖音作品添加作者评论" className="space-y-2" onSubmit={e => { e.preventDefault(); void run(async () => { await douyinAction(`/uploads/${row.id}/first-comment`, comment); setAddingComment(false); }); }}><FirstCommentOptions platform="douyin" value={comment} onChange={setComment} /><button className="btn btn-primary text-xs" disabled={busy || !comment.enabled || !comment.text.trim() || comment.text.length > 500}>确认安排这条作者评论</button></form> : null}
    {row.error ? <p role="alert" className="text-xs text-warning">{row.error}</p> : null}
    {canEdit && (row.status === "UNKNOWN" || row.status === "SUBMITTED") ? <form className="flex gap-2" onSubmit={e => { e.preventDefault(); void run(() => douyinAction(`/uploads/${row.id}/reconcile`, { video })); }}>
      {!row.work_id ? <input aria-label="核对抖音作品" className="input text-xs flex-1 min-w-0" placeholder="核对创作中心后填写作品 ID 或完整链接" value={video} onChange={e => setVideo(e.target.value)} required /> : null}
      <button className="btn text-xs" disabled={busy || (!row.work_id && !video.trim())}>立即核对一次</button>
    </form> : null}
    {canEdit && row.status === "QUEUED" ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction(`/uploads/${row.id}/cancel`))}>取消排队</button> : null}
    {canEdit && row.can_retry ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction(`/uploads/${row.id}/retry`))}>重新检查并投稿</button> : null}
  </li>;
}

function FocusedUpload({ id, canEdit, run, busy, template }: { id: string; canEdit: boolean; run: Run; busy: boolean; template?: FirstComment }) {
  const load = useCallback((signal: AbortSignal) => api<DouyinJob>(`${DOUYIN_API}/uploads/${encodeURIComponent(id)}`, { signal }), [id]);
  const { data, error, reload } = usePageResource(load, 4000);
  return <section aria-label="当前抖音投稿任务" className="space-y-2"><h2 className="text-sm font-medium">当前抖音投稿任务</h2>{error ? <ErrorState error={error} onRetry={reload} compact /> : data ? <ul><UploadRow row={data} template={template} canEdit={canEdit} run={async action => { await run(action); await reload(); }} busy={busy} /></ul> : <p className="text-xs text-text-muted">正在读取投稿回执…</p>}</section>;
}

function CommentTemplate({ initial, run, busy }: { initial: FirstComment; run: Run; busy: boolean }) {
  const [value, setValue] = useState(initial);
  const [saved, setSaved] = useState(false);
  return <details className="card p-4"><summary className="text-sm cursor-pointer">常用作者评论设置</summary><form className="mt-3 space-y-3" onSubmit={e => { e.preventDefault(); void run(async () => { await douyinAction("/first-comment-template", value, "PUT"); setSaved(true); }); }}><FirstCommentOptions template platform="douyin" value={value} onChange={v => { setValue(v); setSaved(false); }} /><button className="btn text-xs" disabled={busy || value.text.length > 500 || (value.enabled && !value.text.trim())}>保存抖音常用评论</button>{saved ? <span className="ml-2 text-xs text-success">已保存，仅影响后续投稿。</span> : null}</form></details>;
}

export function DouyinPlatform() {
  const [params, setParams] = useSearchParams();
  const renderParam = params.get("render") || "";
  const taskId = params.get("task") || "";
  const sourceScript = params.get("from_script") || "";
  const sourceVersion = Number(params.get("version"));
  const { data, error, loading, reload } = usePageResource(loadDouyin, 4000);
  const role = useAuthStore(s => s.user?.role);
  const admin = role === "admin", canEdit = admin || role === "editor";
  const [busy, setBusy] = useState(false), [actionError, setActionError] = useState("");
  const [loginId, setLoginId] = useState("");
  const [selected, setSelected] = useState(renderParam), [query, setQuery] = useState(""), [scope, setScope] = useState("current");
  const run = useCallback<Run>(async action => {
    setBusy(true); setActionError("");
    try { await action(); await reload(); } catch (e) { setActionError(errorMessage(e, "抖音操作未完成")); }
    finally { setBusy(false); }
  }, [reload]);
  const onConnected = useCallback(() => { setLoginId(""); void reload(); }, [reload]);
  const activeLogin = loginId || data?.login_id || "";
  useEffect(() => { setSelected(renderParam); setActionError(""); }, [renderParam, data?.account.uid]);
  const rows = (data?.candidates || []).filter(r => (!query || `${r.title} v${r.version}`.toLowerCase().includes(query.toLowerCase())) &&
    (scope === "all" || (!r.archived && r.script_status === "APPROVED" && r.current_version === r.version && r.approved_version === r.version)));
  return <div className="space-y-4 max-w-6xl">
    <header className="flex justify-between gap-3"><div><h1 className="text-xl font-semibold">平台管理</h1><p className="text-xs text-text-muted mt-1">抖音账号连接、视频投稿与发布结果核对。</p></div><button className="btn text-xs" disabled={loading} onClick={() => void reload()}>刷新抖音</button></header>
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    {sourceScript && sourceVersion > 0 ? <Link className="text-sm text-accent block" to={documentPublishLink(sourceScript, sourceVersion)}>← 返回文稿发布页（v{sourceVersion}）</Link> : null}
    {actionError ? <p role="alert" className="card p-3 text-sm text-danger">{actionError}</p> : null}
    {!data && !error ? <p className="text-sm text-text-muted">正在读取抖音…</p> : null}
    {data ? <>
      <section className="card p-4 flex flex-wrap items-center gap-4"><div className="w-11 h-11 rounded-xl bg-accent/10 text-accent flex items-center justify-center font-bold">抖</div><div className="flex-1"><h2 className="text-sm font-medium">抖音</h2><p className="text-xs text-text-muted mt-1">{data.account.connected ? `${data.account.name} · UID ${data.account.uid}` : "尚未连接账号"}</p></div><span className={`text-xs ${data.runtime_ready ? "text-success" : "text-warning"}`}>{data.runtime_ready ? "投稿组件可用" : "投稿组件待安装"}</span><a href="https://creator.douyin.com/creator-micro/content/manage" target="_blank" rel="noreferrer" className="btn text-xs">抖音创作中心 ↗</a></section>
      <section className="card p-4 space-y-3"><h2 className="text-sm font-medium">抖音账号连接</h2>
        <p className="text-xs text-text-muted">扫码连接后验证实际账号身份，登录凭证保存在本机。首次连接与后续身份验证使用独立浏览器窗口。</p>
        {admin ? <div className="flex flex-wrap gap-2"><button className="btn btn-primary text-xs" disabled={busy || !data.runtime_ready} onClick={() => void run(async () => { const row = await douyinAction("/login"); setLoginId(row.id); })}>{data.account.connected ? "重新扫码连接抖音" : "扫码连接抖音"}</button>{data.account.connected ? <><button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction("/verify"))}>验证抖音登录</button><button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction("/account", undefined, "DELETE"))}>断开抖音账号</button></> : null}</div> : <p className="text-xs text-text-muted">账号连接由管理员配置。</p>}
        {!data.runtime_ready ? <p className="text-xs text-warning">本机抖音运行环境尚未准备好，请先完成组件安装。</p> : null}
      </section>
      {admin && data.account.connected ? <CommentTemplate key={`${data.account.uid}:${JSON.stringify(data.settings?.first_comment)}`} initial={data.settings?.first_comment ?? EMPTY_FIRST_COMMENT} run={run} busy={busy} /> : null}
      {admin && activeLogin ? <LoginStatus key={activeLogin} id={activeLogin} onConnected={onConnected} /> : null}
      <DouyinConnectionStatus account={data.account} checks={data.checks} />
      {canEdit && data.account.connected ? <>
        <section className="card p-4 space-y-3"><h2 className="text-sm font-medium">选择抖音投稿成片</h2><div className="flex gap-2"><input aria-label="搜索抖音成片" className="input text-xs flex-1 min-w-0" placeholder="搜索标题或版本" value={query} onChange={e => setQuery(e.target.value)} /><select aria-label="抖音成片范围" className="input text-xs w-auto" value={scope} onChange={e => setScope(e.target.value)}><option value="current">当前已定稿</option><option value="all">全部可投稿</option></select></div>
          <select aria-label="抖音投稿成片" className="input w-full" value={selected} onChange={e => { setSelected(e.target.value); setParams(old => { const next = new URLSearchParams(old); next.delete("task"); if (e.target.value) next.set("render", e.target.value); else next.delete("render"); return next; }); }}><option value="">选择一条成片，预览发布包</option>{selected && !rows.some(r => r.render_id === selected) ? <option value={selected}>当前已选成片</option> : null}{rows.map(r => <option key={r.render_id} value={r.render_id}>{r.title} · v{r.version}{r.archived ? " · 已归档" : ""}</option>)}</select>
          <p className="text-xs text-text-muted">显示 {rows.length} 个版本。已在 B 站发布的成片仍可投稿到抖音。</p>
        </section>
        {selected && !taskId ? <UploadEditor key={selected} renderId={selected} run={run} busy={busy || !data.runtime_ready} onDone={() => { setSelected(""); setParams(old => { const next = new URLSearchParams(old); next.delete("render"); return next; }); }} /> : null}
      </> : null}
      {taskId && data.account.connected ? <FocusedUpload key={taskId} id={taskId} template={data.settings?.first_comment} canEdit={canEdit} run={run} busy={busy} /> : null}
      <h2 className="text-sm font-medium">抖音投稿队列与回执 · {data.uploads.length}</h2>
      {data.uploads.length ? <ul className="space-y-3">{data.uploads.filter(row => row.id !== taskId).map(row => <UploadRow key={row.id} row={row} template={data.settings?.first_comment} canEdit={canEdit} run={run} busy={busy} />)}</ul> : <p className="card p-8 text-center text-sm text-text-muted">还没有抖音投稿任务。</p>}
    </> : null}
  </div>;
}
