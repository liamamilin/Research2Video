import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ExternalLink, RefreshCw, Settings, UploadCloud } from "lucide-react";
import { api } from "@/api/client";
import { DOUYIN_API, douyinAction, loadDouyin, type DouyinJob, type DouyinLogin, type DouyinSettings } from "@/api/douyin";
import { DEFAULT_PUBLISH_WINDOW, EMPTY_FIRST_COMMENT, type FirstComment, type PublishWindow, type UploadCandidate } from "@/api/platforms";
import { FirstCommentOptions } from "./FirstCommentOptions";
import { UploadEditor } from "./DouyinPublishing";
import { ErrorState } from "@/components/ErrorState";
import { useAuthStore } from "@/lib/auth-store";
import { documentPublishLink } from "@/lib/platform-links";
import { errorMessage } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { usePageResource } from "@/lib/use-page-resource";
import { DouyinConnectionStatus } from "./DouyinConnectionStatus";

type Run = (action: () => Promise<unknown>) => Promise<void>;
const STATES: Record<string, string> = {
  QUEUED: "排队中",
  RUNNING: "处理中",
  SUBMITTED: "已投稿 · 待核对公开状态",
  PUBLISHED: "已公开",
  UNKNOWN: "结果待核对",
  FAILED: "未完成",
  CANCELLED: "已取消",
  REMOVED: "已在抖音删除",
  DONE: "检查完成",
  RECORDED: "检查完成",
};

function candidateLabel(row: UploadCandidate): string {
  const notes = [
    row.archived ? "已归档" : "",
    row.current_version && row.current_version !== row.version ? `历史版本，当前 v${row.current_version}` : "",
  ].filter(Boolean);
  return `${row.title} · v${row.version}${notes.length ? ` · ${notes.join(" · ")}` : ""}`;
}

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
  const labels: Record<string, string> = {
    WAITING: "等待视频确认公开",
    QUEUED: "等待发送",
    RUNNING: "发送中",
    SENT: first?.read_back ? "已发送并回读" : "已取得评论回执，等待回读",
    UNKNOWN: "结果待核对，不会自动重发",
    FAILED: "未发送成功",
    CANCELLED: "已取消",
  };
  const verification = row.verification;
  return <li className="card p-4 space-y-2">
    <div className="flex flex-wrap justify-between gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{row.payload?.title}</span>
        {row.automatic ? <span className="badge border border-border text-[11px] text-text-muted">自动投稿</span> : null}
      </div>
      <span className={cn("badge border border-border", row.status === "UNKNOWN" && "text-warning")}>{STATES[row.status] || row.status}</span>
    </div>
    <p className="text-xs text-text-muted">文稿 v{row.version} · {row.phase}{row.created_at ? ` · ${new Date(row.created_at * 1000).toLocaleString("zh-CN")}` : ""}</p>
    <div className="flex flex-wrap gap-3 text-xs">
      <Link className="text-accent hover:underline" to={documentPublishLink(row.script_id, row.version, row.render_id)}>查看文稿发布记录 →</Link>
      {row.url ? <a className="text-accent hover:underline inline-flex items-center gap-1" href={row.url} target="_blank" rel="noreferrer">打开抖音作品<ExternalLink className="w-3 h-3" /></a> : null}
    </div>
    {row.work_id ? <p className="text-xs text-text-muted">作品 ID：{row.work_id}</p> : null}
    {row.status === "PUBLISHED" ? <p className="text-xs text-success">已自动回填文稿的抖音发布链接、日期和已发布状态。</p> : null}
    {row.recovery?.note ? <p className="text-xs text-text-muted" role="status">{row.recovery.note}</p> : row.status === "UNKNOWN" && !row.work_id ? <p className="text-xs text-text-muted">系统会自动按标题回查创作中心作品列表，确认前不会重复投稿。</p> : null}
    {verification?.automatic ? <p className="text-xs text-text-muted">{["QUEUED", "RUNNING"].includes(verification.status) ? "正在自动核对公开状态" : "自动核对已启用，完成后约 2 分钟重查"}{verification.count ? ` · 已检查 ${verification.count} 次` : ""}{verification.checked_at ? ` · 最近检查 ${new Date(verification.checked_at * 1000).toLocaleString("zh-CN")}` : ""}</p> : null}
    {verification?.reason ? <p className="text-xs text-warning">{verification.reason}</p> : null}
    {(row.payload?.description || row.payload?.tags?.length) ? <details className="rounded border border-border p-3 text-xs">
      <summary className="cursor-pointer text-text-muted">投稿时发布包</summary>
      <div className="mt-2 space-y-2">
        {row.payload?.description ? <p className="whitespace-pre-wrap break-words">{row.payload.description}</p> : null}
        {row.payload?.tags?.length ? <p className="text-text-muted">{row.payload.tags.map(t => `#${t}`).join(" · ")}</p> : null}
      </div>
    </details> : null}
    {row.has_diagnostic ? <details className="rounded border border-border p-3 text-xs">
      <summary className="cursor-pointer text-warning">查看失败时页面截图</summary>
      <img src={`${DOUYIN_API}/uploads/${encodeURIComponent(row.id)}/diagnostic`} alt="抖音上传失败时的页面截图" className="mt-2 max-w-full rounded border border-border" />
    </details> : null}
    {first ? <section className="rounded border border-border p-3 space-y-2 text-xs" aria-label="抖音作者评论进度"><p className="font-medium">作者评论 · {labels[first.status] || first.status}</p><p className="whitespace-pre-wrap break-words">{first.text}</p>{first.comment_id ? <p className="text-text-muted break-all">评论 ID：{first.comment_id}</p> : null}{first.error ? <p className="text-warning">{first.error}</p> : null}{canEdit && ["WAITING", "QUEUED"].includes(first.status) ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction(`/uploads/${row.id}/first-comment/cancel`))}>取消作者评论</button> : null}{canEdit && first.can_retry ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction(`/uploads/${row.id}/first-comment/retry`))}>重新检查并发送评论</button> : null}{pendingCommentCheck && canEdit ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction(`/uploads/${row.id}/first-comment/check`))}>只读核对评论</button> : null}{pendingCommentCheck && row.url ? <a className="text-accent" href={row.url} target="_blank" rel="noreferrer">到作品下核对评论</a> : null}</section> : canEdit && ["SUBMITTED", "PUBLISHED"].includes(row.status) && row.work_id ? <button className="btn text-xs" onClick={() => setAddingComment(!addingComment)}>{addingComment ? "收起作者评论" : "添加作者评论"}</button> : null}
    {addingComment && !first ? <form aria-label="为已有抖音作品添加作者评论" className="space-y-2" onSubmit={e => { e.preventDefault(); void run(async () => { await douyinAction(`/uploads/${row.id}/first-comment`, comment); setAddingComment(false); }); }}><FirstCommentOptions platform="douyin" value={comment} onChange={setComment} /><button className="btn btn-primary text-xs" disabled={busy || !comment.enabled || !comment.text.trim() || comment.text.length > 500}>确认安排这条作者评论</button></form> : null}
    {row.error ? <p role="alert" className="text-xs text-warning">{row.error}</p> : null}
    <div className="flex flex-wrap gap-2 items-center">
      {canEdit && row.status === "UNKNOWN" && !row.work_id ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction(`/uploads/${row.id}/lookup`))}>自动回查一次</button> : null}
      {canEdit && row.status === "QUEUED" ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction(`/uploads/${row.id}/cancel`))}>取消排队</button> : null}
      {canEdit && row.can_retry ? <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction(`/uploads/${row.id}/retry`))}>重新检查并投稿</button> : null}
      {canEdit && ["SUBMITTED", "PUBLISHED"].includes(row.status) ? <button className="btn text-xs" disabled={busy} onClick={() => {
        if (window.confirm("确认这条作品已经在抖音删除吗？标记后会移除本版失效的抖音发布记录，之后可重新投稿。")) {
          void run(() => douyinAction(`/uploads/${row.id}/removed`));
        }
      }}>标记已在抖音删除</button> : null}
      {canEdit && row.status === "REMOVED" ? <button className="btn btn-primary text-xs" disabled={busy} onClick={() => void run(() => douyinAction(`/uploads/${row.id}/reupload`))}>重新投稿</button> : null}
    </div>
    {canEdit && (row.status === "UNKNOWN" || row.status === "SUBMITTED") ? <form className="flex gap-2" onSubmit={e => { e.preventDefault(); void run(() => douyinAction(`/uploads/${row.id}/reconcile`, { video })); }}>
      {!row.work_id ? <input aria-label="核对抖音作品" className="input text-xs flex-1 min-w-0" placeholder="核对创作中心后填写作品 ID 或完整链接" value={video} onChange={e => setVideo(e.target.value)} required /> : null}
      <button className="btn text-xs" disabled={busy || (!row.work_id && !video.trim())}>立即核对一次</button>
    </form> : null}
  </li>;
}

export function FocusedUpload({ id, canEdit, run, busy, template }: { id: string; canEdit: boolean; run: Run; busy: boolean; template?: FirstComment }) {
  const [active, setActive] = useState(true);
  const load = useCallback(async (signal: AbortSignal) => {
    const row = await api<DouyinJob>(`${DOUYIN_API}/uploads/${encodeURIComponent(id)}`, { signal });
    if (!signal.aborted) {
      const busyRow = ["QUEUED", "RUNNING", "SUBMITTED", "UNKNOWN"].includes(row.status) || Boolean(row.verification?.automatic) || Boolean(row.first_comment && ["WAITING", "QUEUED", "RUNNING"].includes(row.first_comment.status));
      setActive(busyRow);
    }
    return row;
  }, [id]);
  const { data, error, reload } = usePageResource(load, active ? 4000 : 0);
  return <section aria-label="当前抖音投稿任务" className="space-y-2"><h2 className="text-sm font-medium">当前抖音投稿任务</h2>{error ? <ErrorState error={error} onRetry={reload} compact /> : data ? <ul><UploadRow row={data} template={template} canEdit={canEdit} run={async action => { await run(action); await reload(); }} busy={busy} /></ul> : <p className="text-xs text-text-muted">正在读取投稿回执…</p>}</section>;
}

function DouyinPolicy({ initial, run, busy, ready }: { initial: DouyinSettings; run: Run; busy: boolean; ready: boolean }) {
  const [config, setConfig] = useState<DouyinSettings>(initial);
  const [saved, setSaved] = useState(false);
  const firstComment = config.first_comment || EMPTY_FIRST_COMMENT;
  const hours = config.publish_window || DEFAULT_PUBLISH_WINDOW;
  const setWindow = (patch: Partial<PublishWindow>) => { setSaved(false); setConfig({ ...config, publish_window: { ...hours, ...patch } }); };
  const invalidComment = firstComment.enabled && (!firstComment.text.trim() || firstComment.text.length > 500);
  const invalidWindow = hours.enabled && (hours.start >= hours.end || hours.spacing < 5 || hours.spacing > 180);
  return <form className="card p-4 space-y-4" aria-label="抖音自动化策略" onSubmit={e => {
    e.preventDefault();
    void run(async () => {
      await douyinAction("/settings", {
        auto_upload: config.auto_upload,
        headless: config.headless,
        publish_window: hours,
        first_comment: { ...firstComment, pin: false },
      }, "PUT");
      setSaved(true);
    });
  }}>
    <h2 className="text-sm font-medium">自动化策略</h2>
    <label className="flex gap-2 text-sm items-center">
      <input type="checkbox" checked={config.auto_upload} disabled={!ready && !config.auto_upload}
        onChange={e => { setSaved(false); setConfig({ ...config, auto_upload: e.target.checked }); }} />
      出片后自动投稿到抖音
    </label>
    <p className="text-xs text-text-muted">启用后仅处理此后新完成的当前定稿成片；沿用抖音发布包与双比例封面，自动跳过未审计、有严重事实或时效警告以及排除配置里的链路。旧成片可在投稿页手动选择。</p>
    <div className="space-y-2">
      <label className="flex gap-2 text-sm items-center">
        <input type="checkbox" checked={hours.enabled} onChange={e => setWindow({ enabled: e.target.checked })} />
        只在白天时段自动投稿
      </label>
      {hours.enabled ? <div className="flex flex-wrap gap-2 items-center text-xs">
        <label>从 <input aria-label="抖音公开时段开始" type="number" min={0} max={23} className="input w-16 text-xs" value={hours.start} onChange={e => setWindow({ start: Number(e.target.value) })} /> 点</label>
        <label>到 <input aria-label="抖音公开时段结束" type="number" min={1} max={24} className="input w-16 text-xs" value={hours.end} onChange={e => setWindow({ end: Number(e.target.value) })} /> 点</label>
        <label>间隔 <input aria-label="抖音定时间隔分钟" type="number" min={5} max={180} className="input w-16 text-xs" value={hours.spacing} onChange={e => setWindow({ spacing: Number(e.target.value) })} /> 分钟</label>
      </div> : null}
      <p className="text-xs text-text-muted">时段外或距上一条不足间隔时，自动投稿会在本机队列排队错峰发出；手动投稿不受时段限制。</p>
    </div>
    <div className="space-y-1">
      <label className="flex gap-2 text-sm items-center">
        <input type="checkbox" checked={config.headless} onChange={e => { setSaved(false); setConfig({ ...config, headless: e.target.checked }); }} />
        后台静默投稿（无头浏览器）
      </label>
      <p className="text-xs text-text-muted">开启后投稿不再弹出浏览器窗口打扰工作；若遇到滑块或短信验证，可临时取消勾选切回有头窗口完成验证。</p>
    </div>
    <FirstCommentOptions template platform="douyin" value={firstComment} onChange={v => { setSaved(false); setConfig({ ...config, first_comment: v }); }} />
    <div className="flex items-center gap-3">
      <button disabled={busy || invalidComment || invalidWindow} className="btn btn-primary text-xs">保存抖音策略</button>
      {saved ? <span className="text-xs text-success">策略已保存，仅影响后续投稿。</span> : null}
    </div>
  </form>;
}

export function DouyinPlatform() {
  const [params, setParams] = useSearchParams();
  const renderParam = params.get("render") || "";
  const taskId = params.get("task") || "";
  const sourceScript = params.get("from_script") || "";
  const sourceVersion = Number(params.get("version"));
  const requestedTab = params.get("tab") || "";
  const [pollingActive, setPollingActive] = useState(false);
  const loadOverview = useCallback(async (signal: AbortSignal) => {
    const next = await loadDouyin(signal);
    if (!signal.aborted) {
      const hasActive = Boolean(next.login_id) || next.uploads.some(r => ["QUEUED", "RUNNING", "SUBMITTED", "UNKNOWN"].includes(r.status) || Boolean(r.verification?.automatic) || Boolean(r.first_comment && ["WAITING", "QUEUED", "RUNNING"].includes(r.first_comment.status))) || next.checks.some(c => ["QUEUED", "RUNNING"].includes(c.status));
      setPollingActive(hasActive);
    }
    return next;
  }, []);
  const { data, error, loading, reload } = usePageResource(loadOverview, pollingActive ? 4000 : 30000);
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
  const tab = requestedTab || (data && !data.account.connected ? "settings" : "uploads");
  const changeTab = (value: string) => setParams(old => { const next = new URLSearchParams(old); next.set("tab", value); return next; });
  const rows = (data?.candidates || []).filter(r => (!query.trim() || `${r.title} v${r.version}`.toLowerCase().includes(query.trim().toLowerCase())) &&
    (scope === "all" || (scope === "archived" && r.archived) || (scope === "history" && r.current_version !== undefined && r.version !== r.current_version) ||
      (scope === "current" && !r.archived && r.script_status === "APPROVED" && r.current_version === r.version && r.approved_version === r.version)));
  const summary = data?.candidate_summary;
  const policySettings: DouyinSettings = data?.settings || {
    first_comment: EMPTY_FIRST_COMMENT,
    auto_upload: false,
    headless: true,
    publish_window: DEFAULT_PUBLISH_WINDOW,
  };

  return <div className="space-y-4 max-w-6xl">
    <header className="flex justify-between gap-3">
      <div><h1 className="text-xl font-semibold">平台管理</h1><p className="text-xs text-text-muted mt-1">抖音账号连接、自动与手动投稿、封面预览及发布核对。</p></div>
      <button className="btn text-xs" disabled={loading} onClick={() => void reload()}><RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />刷新抖音</button>
    </header>
    {error ? <ErrorState error={error} onRetry={reload} compact /> : null}
    {sourceScript && sourceVersion > 0 ? <Link className="text-sm text-accent block" to={documentPublishLink(sourceScript, sourceVersion, params.get("render") || undefined)}>← 返回文稿发布页（v{sourceVersion}）</Link> : null}
    {actionError ? <p role="alert" className="card p-3 text-sm text-danger">{actionError}</p> : null}
    {!data && !error ? <p className="text-sm text-text-muted">正在读取抖音…</p> : null}
    {data ? <>
      <section className="card p-4 flex flex-wrap items-center gap-4">
        <div className="w-11 h-11 rounded-xl bg-accent/10 text-accent flex items-center justify-center font-bold">抖</div>
        <div className="flex-1">
          <h2 className="text-sm font-medium">抖音</h2>
          <p className="text-xs text-text-muted mt-1">{data.account.connected ? `${data.account.name} · UID ${data.account.uid}${policySettings.auto_upload ? " · 自动投稿已开启" : ""}` : "尚未连接账号"}</p>
        </div>
        <span className={`text-xs ${data.runtime_ready ? "text-success" : "text-warning"}`}>{data.runtime_ready ? "投稿组件可用" : "投稿组件待安装"}</span>
        <a href="https://creator.douyin.com/creator-micro/content/manage" target="_blank" rel="noreferrer" className="btn text-xs">抖音创作中心 ↗</a>
      </section>

      <div role="tablist" className="flex gap-1 border-b border-border">
        {[{ id: "uploads", label: "视频投稿", icon: UploadCloud }, { id: "settings", label: "账号与策略", icon: Settings }].map(item => (
          <button key={item.id} role="tab" aria-selected={tab === item.id}
            className={cn("px-3 py-2 text-sm flex gap-2 items-center border-b-2", tab === item.id ? "text-accent border-accent" : "text-text-muted border-transparent")}
            onClick={() => changeTab(item.id)}>
            <item.icon className="w-4 h-4" />{item.label}
          </button>
        ))}
      </div>

      {!data.account.connected && tab !== "settings" ? <section className="card p-8 text-center space-y-3">
        <p className="text-sm text-text-muted">先连接抖音账号，再开始投稿或配置自动化策略。</p>
        <button className="btn btn-primary text-xs" onClick={() => changeTab("settings")}>前往账号连接</button>
      </section> : null}

      {tab === "uploads" && data.account.connected ? <div className="space-y-4">
        {canEdit ? <>
          <section className="card p-4 space-y-3">
            <div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-medium">选择抖音投稿成片</h2><span className="text-xs text-text-muted">{data.candidates.length} 个文稿版本</span></div>
            {summary ? <p className="text-xs text-text-muted">已完成 {summary.completed_renders} 次出片，文件可用 {summary.available_versions} 个版本；已记录发布 {summary.published_versions} 个，已有投稿任务 {summary.queued_versions} 个{summary.unavailable_versions ? `，文件暂不可用 ${summary.unavailable_versions} 个` : ""}。</p> : null}
            <div className="flex flex-wrap gap-2">
              <input aria-label="搜索抖音成片" className="input text-xs flex-1 min-w-[180px]" placeholder="搜索标题或版本" value={query} onChange={e => setQuery(e.target.value)} />
              <select aria-label="抖音成片范围" className="input text-xs w-auto" value={scope} onChange={e => setScope(e.target.value)}>
                <option value="current">当前已定稿</option>
                <option value="all">全部可投稿</option>
                <option value="archived">已归档成片</option>
                <option value="history">历史版本</option>
              </select>
            </div>
            <select aria-label="抖音投稿成片" className="input w-full" value={selected} onChange={e => { setSelected(e.target.value); setParams(old => { const next = new URLSearchParams(old); next.delete("task"); if (e.target.value) next.set("render", e.target.value); else next.delete("render"); return next; }); }}>
              <option value="">选择一条成片，预览发布包</option>
              {selected && !rows.some(r => r.render_id === selected) ? <option value={selected}>当前已选成片</option> : null}
              {rows.map(r => <option key={r.render_id} value={r.render_id}>{candidateLabel(r)}</option>)}
            </select>
            <p className="text-xs text-text-muted">显示 {rows.length} / {data.candidates.length} 个版本。已在 B 站发布的成片仍可投稿到抖音。</p>
          </section>
          {selected && !taskId ? <UploadEditor key={selected} renderId={selected} run={run} busy={busy || !data.runtime_ready} onDone={task => { setSelected(""); setParams(old => { const next = new URLSearchParams(old); next.delete("render"); if (task?.id && task.kind === "UPLOAD") next.set("task", task.id); return next; }); }} /> : null}
        </> : null}
        {taskId ? <FocusedUpload key={taskId} id={taskId} template={policySettings.first_comment} canEdit={canEdit} run={run} busy={busy} /> : null}
        <h2 className="text-sm font-medium">抖音投稿队列与回执 · {data.uploads.length}</h2>
        {data.uploads.length ? <ul className="space-y-3">{data.uploads.filter(row => row.id !== taskId).map(row => <UploadRow key={row.id} row={row} template={policySettings.first_comment} canEdit={canEdit} run={run} busy={busy} />)}</ul> : !taskId ? <p className="card p-8 text-center text-sm text-text-muted">还没有抖音投稿任务。</p> : null}
      </div> : null}

      {tab === "settings" ? <div className="grid lg:grid-cols-2 gap-4 items-start">
        <div className="space-y-4">
          <section className="card p-4 space-y-3">
            <h2 className="text-sm font-medium">抖音账号连接</h2>
            <p className="text-xs text-text-muted">扫码连接后验证实际账号身份，登录凭证保存在本机。扫码与身份验证使用独立浏览器窗口。</p>
            {admin ? <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary text-xs" disabled={busy || !data.runtime_ready} onClick={() => void run(async () => { const row = await douyinAction("/login"); setLoginId(row.id); })}>{data.account.connected ? "重新扫码连接抖音" : "扫码连接抖音"}</button>
              {data.account.connected ? <>
                <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction("/verify"))}>验证抖音登录</button>
                <button className="btn text-xs" disabled={busy} onClick={() => void run(() => douyinAction("/account", undefined, "DELETE"))}>断开抖音账号</button>
              </> : null}
            </div> : <p className="text-xs text-text-muted">账号连接由管理员配置。</p>}
            {!data.runtime_ready ? <p className="text-xs text-warning">本机抖音运行环境尚未准备好，请先完成组件安装。</p> : null}
          </section>
          {admin && activeLogin ? <LoginStatus key={activeLogin} id={activeLogin} onConnected={onConnected} /> : null}
          <DouyinConnectionStatus account={data.account} checks={data.checks} />
        </div>
        {admin && data.account.connected ? <DouyinPolicy key={`${data.account.uid}:${JSON.stringify(policySettings)}`} initial={policySettings} run={run} busy={busy} ready={data.runtime_ready && data.account.connected} /> : <section className="card p-4 text-xs text-text-muted space-y-2"><p>连接抖音账号后，管理员可在这里配置自动投稿、白天错峰时段、静默无头模式与常用作者评论。</p><p>登录凭证仅保存在本机受限文件中；每次验证或投稿成功会自动刷新会话状态以延长有效期。</p></section>}
      </div> : null}
    </> : null}
  </div>;
}
