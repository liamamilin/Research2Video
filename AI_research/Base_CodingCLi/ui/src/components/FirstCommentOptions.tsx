import type { FirstComment } from "@/api/platforms";
import { publishLimits, textCount } from "@/lib/publish-text";

export function FirstCommentOptions({ value, onChange, template = false, platform = "bilibili" }: {
  value: FirstComment; onChange: (value: FirstComment) => void; template?: boolean; platform?: "bilibili" | "douyin" | "kuaishou" | "xiaohongshu";
}) {
  const douyin = platform === "douyin";
  const kuaishou = platform === "kuaishou";
  const xiaohongshu = platform === "xiaohongshu";
  const limit = publishLimits(platform).comment_limit;
  const noPin = platform !== "bilibili";
  const name = douyin ? "作者评论" : "首条评论";
  const size = textCount(value.text,publishLimits(platform).comment_counter);
  return <fieldset className="rounded-lg border border-border p-3 space-y-3">
    <legend className="px-1 text-sm font-medium">{template ? `常用${name}` : `${name}（可选）`}</legend>
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={value.enabled} onChange={e => onChange({ ...value, enabled: e.target.checked })} />{template ? `新投稿默认添加${name}` : `视频公开后添加${name}`}</label>
    {value.enabled ? <>
      <label className="block text-xs">{template ? `常用${name}内容` : `${name}内容`}<textarea required aria-invalid={!value.text.trim() || size>limit} rows={3} className="input w-full mt-1" placeholder="填写想放在视频下方的评论，例如固定说明、补充资料或交流引导。" value={value.text} onChange={e => onChange({ ...value, text: e.target.value })} /></label>
      {!value.text.trim() ? <p className="text-xs text-warning">请填写{name}内容，或关闭上方选项。<button type="button" className="text-accent ml-2" onClick={() => onChange({...value,enabled:false,pin:false})}>本次不添加评论</button></p> : size>limit ? <p className="text-xs text-warning">{name}超过 {limit} 字，请缩短后再提交。</p> : null}
      <div className="flex flex-wrap items-center justify-between gap-2"><label className="flex items-center gap-2 text-xs"><input type="checkbox" disabled={noPin} checked={noPin ? false : value.pin} onChange={e => onChange({ ...value, pin: e.target.checked })} />{douyin ? "自动置顶（抖音暂不支持）" : kuaishou ? "自动置顶（快手待接入）" : xiaohongshu ? "自动置顶（本期不做）" : template ? "默认置顶首条评论" : "置顶这条评论"}</label><span className="text-xs text-text-muted">{size} / {limit} 字</span></div>
    </> : null}
    {douyin ? <p className="text-xs text-text-muted">作者评论由平台决定展示顺序，无法保证早于所有观众评论。</p> : null}
    <p className="text-xs text-text-muted">{template ? "用于后续手动和自动投稿，每次手动投稿都能修改或关闭；不补发到已有投稿。" : noPin ? "作品确认公开后发送一次，发送结果待核对时不会自动重发。评论展示顺序由平台决定。" : "视频审核通过并公开后发送，再按选项置顶。不会占用视频简介，也无法保证早于所有观众评论。"}</p>
  </fieldset>;
}
