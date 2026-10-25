import type { FirstComment } from "@/api/platforms";

export function FirstCommentOptions({ value, onChange, template = false }: {
  value: FirstComment; onChange: (value: FirstComment) => void; template?: boolean;
}) {
  return <fieldset className="rounded-lg border border-border p-3 space-y-3">
    <legend className="px-1 text-sm font-medium">{template ? "常用首条评论" : "首条评论（可选）"}</legend>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={value.enabled} onChange={e => onChange({ ...value, enabled: e.target.checked })} />{template ? "新投稿默认添加首条评论" : "视频公开后添加首条评论"}</label>
    {value.enabled ? <>
      <label className="block text-xs">{template ? "常用首条评论内容" : "首条评论内容"}<textarea required maxLength={1000} rows={3} className="input w-full mt-1" placeholder="填写想放在视频下方的评论，例如固定说明、补充资料或交流引导。" value={value.text} onChange={e => onChange({ ...value, text: e.target.value })} /></label>
      <div className="flex flex-wrap items-center justify-between gap-2"><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={value.pin} onChange={e => onChange({ ...value, pin: e.target.checked })} />{template ? "默认置顶首条评论" : "置顶这条评论"}</label><span className="text-xs text-text-muted">{value.text.length} / 1000 字</span></div>
    </> : null}
    <p className="text-xs text-text-muted">{template ? "用于后续手动和自动投稿，每次手动投稿都能修改或关闭；不补发到已有投稿。" : "视频审核通过并公开后发送，再按选项置顶。不会占用视频简介，也无法保证早于所有观众评论。"}</p>
  </fieldset>;
}
