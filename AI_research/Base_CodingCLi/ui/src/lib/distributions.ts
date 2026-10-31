import type { DistributionState, Publication } from "@/api/video";

export const PLATFORM_NAMES: Record<string, string> = { bilibili: "B 站", douyin: "抖音", kuaishou: "快手", xiaohongshu: "小红书", weixin_channels: "微信视频号" };
// Deferred by the user on 2026-10-25; retain names for historical records.
export const VISIBLE_PLATFORM_NAMES = Object.fromEntries(Object.entries(PLATFORM_NAMES).filter(([key]) => key !== "weixin_channels"));
export const isVisiblePlatform = (platform: string) => Boolean(VISIBLE_PLATFORM_NAMES[platform]);
export const DISTRIBUTION_LABEL: Record<string, string> = {
  UNPUBLISHED: "未发布", DRAFT: "待发布", QUEUED: "排队中", UPLOADING: "上传中",
  SUBMITTED: "已提交，待公开确认", PUBLISHED: "已发布", UNKNOWN: "结果待核对", FAILED: "发布失败", CANCELLED: "已取消",
};
export function publishedPlatformCount(rows: DistributionState[] = [], publications: Publication[] = []) {
  return new Set([...rows.filter(r => r.status === "PUBLISHED").map(r => r.platform), ...publications.map(p => p.platform || "bilibili")]).size;
}
export function matchesDistribution(rows: DistributionState[] = [], platform: string, state: string, legacyPublished = false) {
  if (state === "never") return !legacyPublished && !rows.some(r => r.status === "PUBLISHED");
  if (!state && !platform) return true;
  const targets = rows.filter(r => (!platform || r.platform === platform) && (r.current_account || !r.account_uid));
  if (!state) return true;
  if (state === "UNPUBLISHED") return targets.length ? targets.every(r => ["UNPUBLISHED", "DRAFT", "CANCELLED"].includes(r.status)) : !legacyPublished;
  return targets.some(r => r.status === state);
}

export const SOCIAL_PLATFORM_KEYS = ["kuaishou", "xiaohongshu"];
export const PLATFORM_LINK_PLACEHOLDERS: Record<string, string> = {
  bilibili: "https://www.bilibili.com/video/BV…", douyin: "https://www.douyin.com/video/作品ID",
  kuaishou: "https://www.kuaishou.com/short-video/作品ID", xiaohongshu: "https://www.xiaohongshu.com/explore/作品ID",
  weixin_channels: "https://weixin.qq.com/sph/分享短码",
};
export function platformFromLink(value: string): string | undefined {
  try {
    const host = new URL(value).hostname;
    const domains: Record<string,string[]> = { bilibili:["bilibili.com","b23.tv"], douyin:["douyin.com","iesdouyin.com"], kuaishou:["kuaishou.com"], xiaohongshu:["xiaohongshu.com"], weixin_channels:["weixin.qq.com"] };
    return Object.entries(domains).find(([,list]) => list.some(domain => host===domain || host.endsWith(`.${domain}`)))?.[0];
  } catch { return undefined; }
}
