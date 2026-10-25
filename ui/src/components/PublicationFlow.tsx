import { Link } from "react-router-dom";
import type { Publication, VideoRender } from "@/api/video";
import type { UploadTask } from "@/api/platforms";
import { platformUploadLink, UPLOAD_LABEL } from "@/lib/platform-links";

export function PublicationFlow({ scriptId, version, render, publications, upload, canEdit }: {
  scriptId: string;
  version: number;
  render?: VideoRender;
  publications: Publication[];
  upload?: UploadTask;
  canEdit: boolean;
}) {
  const published = publications.find(row => row.version === version);
  const hasFile = render?.files?.some(file => file.name === "video.mp4");
  return <section className="card p-4 space-y-3" aria-label="平台发布">
    <div className="flex flex-wrap items-center gap-2"><h2 className="font-medium flex-1">发布到哔哩哔哩</h2><span className="badge border border-border">v{version} · {published ? "已发布" : upload ? UPLOAD_LABEL[upload.status] || upload.status : render ? "待投稿" : "尚未出片"}</span></div>
    <p className="text-xs text-text-muted">{published ? "发布链接和日期已写入下方记录，所选版本已标记为已发布。" : upload ? "本版本已有投稿任务。确认视频公开后，系统会自动回填发布记录，并更新文稿的已发布标签。" : render ? "进入平台管理后会选中这条成片。确认投稿信息并加入队列，公开后自动回填发布记录。" : "完成出片后，可从这里发起投稿。平台确认视频公开后会自动记录发布链接。"}</p>
    <div className="flex flex-wrap gap-2">
      {published ? <a className="btn btn-primary text-xs" href={published.url} target="_blank" rel="noreferrer">查看已发布视频</a> : null}
      {upload ? <Link className="btn text-xs" to={platformUploadLink(scriptId, version, { taskId: upload.id })}>{published ? "查看平台记录" : "查看投稿进度"}</Link> : !published && canEdit && render && hasFile ? <Link className="btn btn-primary text-xs" to={platformUploadLink(scriptId, version, { renderId: render.id })}>投稿到哔哩哔哩</Link> : null}
      {!published && !upload && !render && canEdit ? <Link className="btn text-xs" to={`/video/${encodeURIComponent(scriptId)}?tab=video`}>先生成成片</Link> : null}
    </div>
    {!published && !upload && render && !hasFile ? <p className="text-xs text-warning">成片文件暂不可用，请检查归档磁盘后再投稿。</p> : null}
    {upload?.error && !published ? <p className="text-xs text-warning">{upload.error}</p> : null}
  </section>;
}
