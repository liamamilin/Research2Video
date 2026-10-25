import type { FirstComment } from "@/api/platforms";

export type PublishPayload = { title: string; description: string; tags: string[]; first_comment?: FirstComment; tid?: number };
export type PublishLimits = { platform: string; name: string; title_min: number; title_limit: number; description_limit: number;
  tag_limit: number; tag_min: number; tag_length_limit: number; combined_limit: number | null; combined_label: string;
  counter: "codepoint" | "utf16"; comment_limit: number; comment_counter: "codepoint" | "utf16" };
export type PublishCheck = { limits: PublishLimits; counts: { title: number; description: number; tags: number; combined: number; description_budget: number }; issues: string[]; valid: boolean };
export type PublishAdaptationResult = { payload: PublishPayload; before: PublishPayload; check: PublishCheck; notes: string[];
  references: { title: string; url: string; date?: string; cited?: boolean }[]; source_description: string; references_in_description: number | null };
export const textCount = (value: string, metric = "codepoint") => metric === "utf16" ? value.length : Array.from(value).length;
export function combinedText(platform: string, title: string, description: string, tags: string[]) {
  if (platform === "kuaishou") return (!description.trim() || description.trim() === title.trim() ? title : `${title}\n\n${description}`) + tags.map(tag => `\n#${tag} `).join("");
  if (platform === "douyin") return `${description} ${tags.map(tag => `#${tag}`).join(" ")}`;
  if (platform === "weixin_channels") return `${title.trim()}\n${tags.map(tag => `#${tag} `).join("")}\n${description}`;
  return description;
}
export function publishLimits(platform: string): PublishLimits {
  const names: Record<string,string> = { bilibili: "B站", douyin: "抖音", kuaishou: "快手", xiaohongshu: "小红书", weixin_channels: "微信视频号" };
  return { platform, name: names[platform], title_min: platform === "weixin_channels" ? 6 : 1,
    title_limit: ({ bilibili:80,douyin:30,kuaishou:80,xiaohongshu:20,weixin_channels:16 } as Record<string,number>)[platform],
    description_limit: platform === "bilibili" ? 2000 : platform === "kuaishou" ? 500 : 1000,
    tag_limit: platform === "douyin" ? 5 : platform === "kuaishou" ? 3 : 10, tag_min: platform === "bilibili" ? 1 : 0,
    tag_length_limit: platform === "kuaishou" ? 30 : ["bilibili","douyin"].includes(platform) ? 20 : 100,
    combined_limit: ({ douyin:1000,kuaishou:500,weixin_channels:1000 } as Record<string,number>)[platform] ?? null,
    combined_label: platform === "douyin" ? "简介和话题合计" : "标题、简介和话题合计", counter:"codepoint",
    comment_limit: platform === "bilibili" ? 1000 : platform === "douyin" ? 500 : 250,
    comment_counter: ["kuaishou","xiaohongshu"].includes(platform) ? "utf16" : "codepoint" };
}
export function publishCheck(platform: string, payload: PublishPayload, given?: PublishLimits): PublishCheck {
  const spec = given ?? publishLimits(platform);
  const { description, tags } = payload; const title = payload.title.trim();
  const combined = textCount(combinedText(platform,title,description,tags));
  const descriptionBudget = Math.min(spec.description_limit, spec.combined_limit === null ? spec.description_limit : Math.max(0,
    spec.combined_limit - textCount(combinedText(platform,title,"",tags)) - (platform === "kuaishou" && title ? 2 : 0)));
  const issues: string[] = [];
  if (textCount(title) < spec.title_min || textCount(title) > spec.title_limit) issues.push(`标题需为 ${spec.title_min}–${spec.title_limit} 字，当前 ${textCount(title)} 字。`);
  if (textCount(description) > spec.description_limit) issues.push(`简介最多 ${spec.description_limit} 字，当前 ${textCount(description)} 字。`);
  if (tags.length < spec.tag_min || tags.length > spec.tag_limit) issues.push(`话题需为 ${spec.tag_min}–${spec.tag_limit} 个，当前 ${tags.length} 个。`);
  if (tags.some(tag => !tag.trim() || textCount(tag) > spec.tag_length_limit || /[\r\n,，]/.test(tag) || platform === "douyin" && /\s/.test(tag))) issues.push(`话题格式不正确，每个最多 ${spec.tag_length_limit} 字，不能含分隔符。`);
  if (spec.combined_limit !== null && combined > spec.combined_limit) issues.push(`${spec.combined_label}超限：当前 ${combined} 字，最多 ${spec.combined_limit} 字。`);
  return { limits:spec, counts:{title:textCount(title),description:textCount(description),tags:tags.length,combined,description_budget:descriptionBudget}, issues, valid:!issues.length };
}

export function validFirstComment(platform: string, value: FirstComment) {
  const spec = publishLimits(platform);
  return textCount(value.text,spec.comment_counter) <= spec.comment_limit && (!value.enabled || !!value.text.trim());
}
