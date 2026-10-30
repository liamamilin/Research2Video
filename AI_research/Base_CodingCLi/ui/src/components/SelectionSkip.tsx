import { Link } from "react-router-dom";
import type { SelectionSkip } from "@/api/video";
import { scriptFromTopic } from "@/lib/topic-progress";

export const skipLabel = (skip: SelectionSkip) => skip.kind === "duplicate" ? "重复选题，已跳过" : "候选不足，已跳过";

export function SelectionSkipLinks({ skip, back, show }: {
  skip: SelectionSkip; back?: string | null; show?: (url: string, label: string) => void;
}) {
  const href = (id: string, tab = "draft") => back ? scriptFromTopic(id, back, tab) : `/video/${encodeURIComponent(id)}?tab=${tab}`;
  return <div className="space-y-2 text-xs text-text-muted">
    <p>{skip.kind === "duplicate" ? "已有内容覆盖本次选题，已跳过重复生产。" : "本次没有剩余的可用选题，按实际素材少做一条。"}</p>
    {skip.originals.map(original => <div key={original.id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <Link className="text-accent hover:underline" to={href(original.id)}>已有文稿：{original.title}</Link>
      {original.video_url ? show ? <button className="btn text-xs" aria-label={`查看已有成片：${original.title}`} onClick={() => show(original.video_url!, `已有成片 · ${original.title}`)}>查看已有成片</button>
        : <Link className="btn text-xs" to={href(original.id, "video")}>查看已有成片</Link>
        : original.render_state === "working" ? <Link className="text-accent hover:underline" to={href(original.id, "video")}>查看制作进度</Link> : null}
    </div>)}
    {skip.reason ? <details><summary className="cursor-pointer">跳过原因</summary><p className="pt-1 break-words">{skip.reason}</p></details> : null}
  </div>;
}
