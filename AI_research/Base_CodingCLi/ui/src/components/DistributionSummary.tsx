import type { Distribution, Publication } from "@/api/video";
import { DISTRIBUTION_LABEL, VISIBLE_PLATFORM_NAMES, publishedPlatformCount } from "@/lib/distributions";

export function DistributionSummary({ rows = [], publications = [] }: { rows?: Distribution[]; publications?: Publication[] }) {
  const count = publishedPlatformCount(rows, publications);
  return <div className="flex flex-wrap gap-2 text-xs" aria-label="各平台发布状态">
    {count ? <span className="text-success">已发布至 {count} 个平台</span> : null}
    {Object.entries(VISIBLE_PLATFORM_NAMES).map(([platform, name]) => {
      const targets = rows.filter(r => r.platform === platform);
      const current = targets.find(r => r.current_account) ?? targets.find(r => !r.account_uid) ?? targets[0];
      if (!["bilibili","douyin"].includes(platform) && !targets.length && !publications.some(p => p.platform===platform)) return null;
      const state = current?.status ?? (publications.some(p => (p.platform || "bilibili") === platform) ? "PUBLISHED" : "UNPUBLISHED");
      return <span key={platform} className={`badge border border-border ${state === "PUBLISHED" ? "text-success" : state === "UNKNOWN" || state === "FAILED" ? "text-warning" : "text-text-muted"}`}>{name} · {current?.blocked_reason ? "账号待核对" : !["bilibili","douyin"].includes(platform) && !current?.connected && !current?.account_uid && state === "UNPUBLISHED" ? "未连接" : DISTRIBUTION_LABEL[state] || state}</span>;
    })}
  </div>;
}

export function DistributionFilters({ platform, status, onPlatform, onStatus }: {
  platform: string; status: string; onPlatform: (value: string) => void; onStatus: (value: string) => void;
}) {
  return <><select aria-label="发布平台" className="input w-auto text-xs" value={platform} onChange={e => onPlatform(e.target.value)}><option value="">全部平台</option>{Object.entries(VISIBLE_PLATFORM_NAMES).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select>
    <select aria-label="平台发布状态" className="input w-auto text-xs" value={status} onChange={e => onStatus(e.target.value)}><option value="">全部发布状态</option><option value="UNPUBLISHED">未发布到所选平台</option><option value="DRAFT">已保存发布包</option><option value="QUEUED">排队中</option><option value="UPLOADING">上传中</option><option value="SUBMITTED">已提交，待公开确认</option><option value="PUBLISHED">已发布</option><option value="FAILED">发布失败</option><option value="UNKNOWN">结果待核对</option><option value="never">从未发布到任何平台</option></select></>;
}
