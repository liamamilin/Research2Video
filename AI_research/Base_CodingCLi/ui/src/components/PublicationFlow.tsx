import { Fragment, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { Distribution, Publication, VideoRender } from "@/api/video";
import type { UploadTask } from "@/api/platforms";
import { platformUploadLink } from "@/lib/platform-links";
import { DISTRIBUTION_LABEL, PLATFORM_NAMES, isVisiblePlatform, publishedPlatformCount } from "@/lib/distributions";
import { errorMessage } from "@/lib/toast";
import { FocusedUpload as BilibiliFocusedUpload, UploadForm } from "./BilibiliPublishing";
import { SocialFocusedUpload, SocialUploadEditor } from "./SocialPublishing";
import { FocusedUpload as DouyinFocusedUpload } from "./DouyinPlatform";
import { UploadEditor } from "./DouyinPublishing";

export function PublicationFlow({ scriptId, version, render, publications, distributions = [], upload, canEdit, onChanged }: {
  scriptId: string; version: number; render?: VideoRender; publications: Publication[];
  distributions?: Distribution[]; upload?: UploadTask; canEdit: boolean; onChanged?: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const opened = (params.get("prepare") || "").split(",").filter(Boolean);
  const focused = params.get("task_panel") || "";
  const [selected, setSelected] = useState<string[]>(opened);
  const lock = useRef(false);
  const change = (name: string, value: string) => setParams(previous => { const next = new URLSearchParams(previous); if (value) next.set(name, value); else next.delete(name); return next; }, { replace: true });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const hasFile = render?.files?.some(file => file.name === "video.mp4");
  const rows = distributions.filter(d => d.version === version && isVisiblePlatform(d.platform)).sort((a, b) => a.platform.localeCompare(b.platform) || Number(b.current_account) - Number(a.current_account));
  const count = publishedPlatformCount(rows, publications.filter(p => p.version === version));
  const run = async (action: () => Promise<unknown>) => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try { await action(); onChanged?.(); } catch (e) { setError(errorMessage(e)); } finally { lock.current = false; setBusy(false); }
  };
  const done = (key: string) => { change("task_panel", key); onChanged?.(); };
  const prepareSelected = () => { change("prepare", [...new Set([...opened, ...selected])].join(",")); };
  return <section className="card p-4 space-y-3" aria-label="平台发布">
    <div className="flex flex-wrap items-center gap-2"><h2 className="font-medium flex-1">发布到各平台</h2><span className="text-xs text-text-muted">文稿 v{version}{count ? ` · 已发布至 ${count} 个平台` : ""}</span></div>
    <p className="text-xs text-text-muted">复用同一条成片。各平台的发布包、任务和作品记录独立保存。状态按文稿版本汇总；任务使用的具体成片另行标明。</p>
    {canEdit && hasFile ? <div className="flex flex-wrap items-center gap-3 text-xs" aria-label="多平台准备">{rows.filter(row => row.can_prepare).map(row => { const key = `${row.platform}:${row.account_uid}`; return <label key={key} className="flex items-center gap-1"><input type="checkbox" checked={selected.includes(key)} disabled={busy} onChange={e => setSelected(previous => e.target.checked ? [...previous, key] : previous.filter(value => value !== key))} />{PLATFORM_NAMES[row.platform]} · {row.account_name || row.account_uid}</label>; })}<button type="button" className="btn text-xs" disabled={busy || !selected.length} onClick={prepareSelected}>准备所选平台（不投稿）</button><span className="text-text-muted">分别保存草稿；每个平台单独提交。</span></div> : null}
    {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
    <div className="overflow-x-auto"><table className="w-full text-sm min-w-[34rem]"><thead className="text-xs text-text-muted text-left"><tr><th className="py-2 font-normal">平台</th><th className="py-2 font-normal">账号</th><th className="py-2 font-normal">本版发布状态</th><th className="py-2 font-normal text-right">操作</th></tr></thead><tbody>
      {rows.map(row => {
        const key = `${row.platform}:${row.account_uid}`;
        const records = row.publications.filter(p => !p.removed_at);
        const task = row.task;
        const status = DISTRIBUTION_LABEL[row.status] || row.status;
        const editing = opened.includes(key);
        const target = platformUploadLink(scriptId, version, { platform: row.platform, renderId: task?.render_id || render?.id, ...(task ? { taskId: task.id } : {}) });
        return <Fragment key={key}><tr className="border-t border-border align-top" aria-label={`${PLATFORM_NAMES[row.platform] || row.platform}发布目标`}>
          <td className="py-3 pr-3 font-medium">{PLATFORM_NAMES[row.platform] || row.platform}</td>
          <td className="py-3 pr-3"><p>{row.account_name || (row.account_uid ? `UID ${row.account_uid}` : row.publications.length ? "账号待核对" : "尚未连接")}</p>{row.account_name && row.account_uid ? <p className="text-xs text-text-muted mt-1">UID {row.account_uid}</p> : null}{row.account_uid && !row.current_account ? <p className="text-xs text-text-muted">历史账号</p> : null}</td>
          <td className="py-3 pr-3"><span className={`badge border border-border ${row.status === "PUBLISHED" ? "text-success" : row.status === "UNKNOWN" || row.status === "FAILED" ? "text-warning" : ""}`}>{status}</span>{records.length ? <p className="text-xs text-text-muted mt-1">{records.length} 条作品 · {records.some(p => p.confirmed_at || p.recorded_by === "platform-manager") ? "含平台自动确认" : "手动记录，未自动核验"}</p> : null}{task && row.status === "PUBLISHED" && task.status !== "PUBLISHED" && !row.publications.some(p => task.work_id && (p.work_id === task.work_id || p.url.includes(`/video/${task.work_id}`))) ? <p className="text-xs text-warning mt-1">另有任务：{DISTRIBUTION_LABEL[task.status] || task.status}</p> : null}{task && render && task.render_id !== render.id ? <p className="text-xs text-warning break-all mt-1">本版投稿使用另一条成片 {task.render_id}；当前所选 {render.id}。保留原任务绑定。</p> : null}{task?.work_id ? <p className="text-xs text-text-muted break-all mt-1">作品 ID {task.work_id}</p> : null}{row.blocked_reason ? <p className="text-xs text-warning mt-1 max-w-xs">{row.blocked_reason}</p> : null}{task?.error ? <p className="text-xs text-warning max-w-xs mt-1">{task.error}</p> : null}{row.publications.some(p => p.removed_at) ? <p className="text-xs text-text-muted mt-1">已移除的本地记录仍保留投稿防重。</p> : null}</td>
          <td className="py-3 text-right"><div className="flex flex-wrap justify-end gap-2">
            {records.map((p, i) => <a key={p.id} href={p.url} target="_blank" rel="noreferrer" className="btn text-xs">查看作品{records.length > 1 ? ` ${i + 1}` : ""}</a>)}
            {task && row.current_account ? <><button type="button" className="btn text-xs" disabled={busy} onClick={() => change("task_panel", focused === key ? "" : key)}>{focused === key ? "收起投稿进度" : "查看投稿进度"}</button><Link className="text-xs text-accent" to={target}>平台管理 ↗</Link></> : null}
            {canEdit && row.can_prepare && hasFile ? <button className="btn btn-primary text-xs" disabled={busy} aria-label={editing ? `收起${PLATFORM_NAMES[row.platform]}发布包` : `${row.draft ? "编辑" : "准备"}${PLATFORM_NAMES[row.platform]}发布`} onClick={() => { setError(""); change("prepare", (editing ? opened.filter(value => value !== key) : [...opened, key]).join(",")); }}>{editing ? "收起发布包" : row.draft ? "编辑并提交" : "准备发布"}</button> : null}
            {row.blocked_reason ? <a className="btn text-xs" href="#publication-records">核对旧记录</a> : null}
            {task && row.account_uid && !row.current_account ? <Link className="btn text-xs" to={`/platforms?platform=${row.platform}&tab=settings`}>连接原账号核对</Link> : null}
            {!row.connected && !row.account_uid && !row.publications.length ? <Link className="btn text-xs" to={`/platforms?platform=${row.platform}&tab=settings`}>连接账号</Link> : null}
          </div></td></tr>
          {focused === key && task ? <tr><td colSpan={4} className="pb-4"><p className="text-xs text-text-muted mb-2">任务 {task.id} · 成片 {task.render_id}{render && task.render_id !== render.id ? " · 此任务使用本版另一条成片，保留原绑定" : ""}</p>{row.platform === "bilibili" ? <BilibiliFocusedUpload key={task.id} taskId={task.id} canEdit={canEdit && row.current_account} run={run} busy={busy} /> : row.platform === "douyin" ? <DouyinFocusedUpload key={task.id} id={task.id} canEdit={canEdit && row.current_account} run={run} busy={busy} /> : <SocialFocusedUpload key={task.id} platform={row.platform} taskId={task.id} accountUid={row.account_uid} canEdit={canEdit && row.current_account} run={run} busy={busy} />}</td></tr> : null}
          {editing && row.can_prepare && render && canEdit ? <tr><td colSpan={4} className="pb-4">{row.platform === "bilibili" ? <UploadForm key={`${key}:${render.id}`} renderId={render.id} run={run} busy={busy} onDone={() => done(key)} /> : row.platform === "douyin" ? <UploadEditor key={`${key}:${render.id}`} renderId={render.id} run={run} busy={busy} onDone={() => done(key)} /> : <SocialUploadEditor key={`${key}:${render.id}`} platform={row.platform} renderId={render.id} run={run} busy={busy} onDone={() => done(key)} />}</td></tr> : null}
        </Fragment>;
      })}
    </tbody></table></div>
    {!rows.length ? <p className="text-xs text-text-muted">平台状态暂未加载。<Link className="text-accent ml-2" to={platformUploadLink(scriptId, version, { renderId: render?.id, taskId: upload?.id })}>前往平台管理</Link></p> : null}
    {!render && canEdit ? <Link className="btn text-xs" to={`/video/${encodeURIComponent(scriptId)}?tab=video`}>先生成成片</Link> : render && !hasFile ? <p className="text-xs text-warning">成片文件暂不可用，请检查归档磁盘后再投稿。</p> : null}
  </section>;
}
