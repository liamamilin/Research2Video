import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import type { Distribution, Publication, VideoRender } from "@/api/video";
import type { UploadTask } from "@/api/platforms";
import { platformUploadLink } from "@/lib/platform-links";
import { DISTRIBUTION_LABEL, PLATFORM_NAMES, isVisiblePlatform, publishedPlatformCount } from "@/lib/distributions";
import { errorMessage } from "@/lib/toast";
import { UploadForm } from "./BilibiliPublishing";
import { SocialUploadEditor } from "./SocialPublishing";
import { UploadEditor } from "./DouyinPublishing";

export function PublicationFlow({ scriptId, version, render, publications, distributions = [], upload, canEdit, onChanged }: {
  scriptId: string; version: number; render?: VideoRender; publications: Publication[];
  distributions?: Distribution[]; upload?: UploadTask; canEdit: boolean; onChanged?: () => void;
}) {
  const [editing, setEditing] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const hasFile = render?.files?.some(file => file.name === "video.mp4");
  const rows = distributions.filter(d => d.version === version && isVisiblePlatform(d.platform)).sort((a, b) => a.platform.localeCompare(b.platform) || Number(b.current_account) - Number(a.current_account));
  const count = publishedPlatformCount(rows, publications.filter(p => p.version === version));
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await action(); onChanged?.(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  const done = () => { setEditing(""); onChanged?.(); };
  return <section className="card p-4 space-y-3" aria-label="平台发布">
    <div className="flex flex-wrap items-center gap-2"><h2 className="font-medium flex-1">发布到各平台</h2><span className="text-xs text-text-muted">文稿 v{version}{count ? ` · 已发布至 ${count} 个平台` : ""}</span></div>
    <p className="text-xs text-text-muted">复用同一条成片。各平台的发布包、任务和作品记录独立保存。</p>
    {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
    <div className="overflow-x-auto"><table className="w-full text-sm min-w-[34rem]"><thead className="text-xs text-text-muted text-left"><tr><th className="py-2 font-normal">平台</th><th className="py-2 font-normal">账号</th><th className="py-2 font-normal">本版发布状态</th><th className="py-2 font-normal text-right">操作</th></tr></thead><tbody>
      {rows.map(row => {
        const key = `${row.platform}:${row.account_uid}`;
        const records = row.publications.filter(p => !p.removed_at);
        const task = row.task;
        const status = DISTRIBUTION_LABEL[row.status] || row.status;
        const target = platformUploadLink(scriptId, version, { platform: row.platform, ...(task ? { taskId: task.id } : render ? { renderId: render.id } : {}) });
        return <Fragment key={key}><tr className="border-t border-border align-top" aria-label={`${PLATFORM_NAMES[row.platform] || row.platform}发布目标`}>
          <td className="py-3 pr-3 font-medium">{PLATFORM_NAMES[row.platform] || row.platform}</td>
          <td className="py-3 pr-3"><p>{row.account_name || (row.account_uid ? `UID ${row.account_uid}` : row.publications.length ? "账号待核对" : "尚未连接")}</p>{row.account_name && row.account_uid ? <p className="text-xs text-text-muted mt-1">UID {row.account_uid}</p> : null}{row.account_uid && !row.current_account ? <p className="text-xs text-text-muted">历史账号</p> : null}</td>
          <td className="py-3 pr-3"><span className={`badge border border-border ${row.status === "PUBLISHED" ? "text-success" : row.status === "UNKNOWN" || row.status === "FAILED" ? "text-warning" : ""}`}>{status}</span>{records.length ? <p className="text-xs text-text-muted mt-1">{records.length} 条作品 · {records.some(p => p.confirmed_at || p.recorded_by === "platform-manager") ? "含平台自动确认" : "手动记录，未自动核验"}</p> : null}{task && row.status === "PUBLISHED" && task.status !== "PUBLISHED" && !row.publications.some(p => task.work_id && (p.work_id === task.work_id || p.url.includes(`/video/${task.work_id}`))) ? <p className="text-xs text-warning mt-1">另有任务：{DISTRIBUTION_LABEL[task.status] || task.status}</p> : null}{task?.work_id ? <p className="text-xs text-text-muted break-all mt-1">作品 ID {task.work_id}</p> : null}{row.blocked_reason ? <p className="text-xs text-warning mt-1 max-w-xs">{row.blocked_reason}</p> : null}{task?.error ? <p className="text-xs text-warning max-w-xs mt-1">{task.error}</p> : null}{row.publications.some(p => p.removed_at) ? <p className="text-xs text-text-muted mt-1">已移除的本地记录仍保留投稿防重。</p> : null}</td>
          <td className="py-3 text-right"><div className="flex flex-wrap justify-end gap-2">
            {records.map((p, i) => <a key={p.id} href={p.url} target="_blank" rel="noreferrer" className="btn text-xs">查看作品{records.length > 1 ? ` ${i + 1}` : ""}</a>)}
            {task && row.current_account ? <Link className="btn text-xs" to={target}>查看{row.status === "PUBLISHED" ? "平台记录" : "投稿进度"}</Link> : null}
            {canEdit && row.can_prepare && hasFile ? <button className="btn btn-primary text-xs" aria-label={editing === key ? `收起${PLATFORM_NAMES[row.platform]}发布包` : `${row.draft ? "编辑" : "准备"}${PLATFORM_NAMES[row.platform]}发布`} onClick={() => { setError(""); setEditing(editing === key ? "" : key); }}>{editing === key ? "收起发布包" : row.draft ? "编辑并提交" : "准备发布"}</button> : null}
            {row.blocked_reason ? <a className="btn text-xs" href="#publication-records">核对旧记录</a> : null}
            {task && row.account_uid && !row.current_account ? <Link className="btn text-xs" to={`/platforms?platform=${row.platform}&tab=settings`}>连接原账号核对</Link> : null}
            {!row.connected && !row.account_uid && !row.publications.length ? <Link className="btn text-xs" to={`/platforms?platform=${row.platform}&tab=settings`}>连接账号</Link> : null}
          </div></td></tr>
          {editing === key && row.can_prepare && render && canEdit ? <tr><td colSpan={4} className="pb-4">{row.platform === "bilibili" ? <UploadForm key={`${key}:${render.id}`} renderId={render.id} run={run} busy={busy} onDone={done} /> : row.platform === "douyin" ? <UploadEditor key={`${key}:${render.id}`} renderId={render.id} run={run} busy={busy} onDone={done} /> : <SocialUploadEditor key={`${key}:${render.id}`} platform={row.platform} renderId={render.id} run={run} busy={busy} onDone={done} />}</td></tr> : null}
        </Fragment>;
      })}
    </tbody></table></div>
    {!rows.length ? <p className="text-xs text-text-muted">平台状态暂未加载。<Link className="text-accent ml-2" to={platformUploadLink(scriptId, version, { renderId: render?.id, taskId: upload?.id })}>前往平台管理</Link></p> : null}
    {!render && canEdit ? <Link className="btn text-xs" to={`/video/${encodeURIComponent(scriptId)}?tab=video`}>先生成成片</Link> : render && !hasFile ? <p className="text-xs text-warning">成片文件暂不可用，请检查归档磁盘后再投稿。</p> : null}
  </section>;
}
