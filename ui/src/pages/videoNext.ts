import type { VideoScriptDetail } from "@/api/video";

export type ScriptTab = "draft" | "review" | "video" | "publish";

export const TAB_LABEL: Record<ScriptTab, string> = {
  draft: "稿件",
  review: "审校",
  video: "成片",
  publish: "发布",
};

export interface NextStep {
  tab: ScriptTab;
  text: string;
  tone: "wait" | "todo" | "done" | "bad";
}

const ACTIVE = ["SUBMITTING", "QUEUED", "RUNNING", "DOWNLOADING"];

/** What the person should do next with a script, and the tab where they do it. */
export function nextStep(detail: VideoScriptDetail): NextStep {
  const latest = detail.renders[0] ?? null;
  switch (detail.status) {
    case "RESEARCHING":
    case "WRITING":
      return { tab: "draft", text: "正在研究和写稿，完成后会出现草稿。可以离开此页。", tone: "wait" };
    case "FAILED":
      return { tab: "draft", text: "这篇没有完成，原因见下面。", tone: "bad" };
    case "DRAFT": {
      const checks = detail.version_shown?.checks;
      const hint = checks?.summary && !checks.summary.passed ? `审校有 ${checks.summary.serious} 处必须改，` : "";
      return { tab: "draft", text: `${hint}读稿、看过「审校」页后，在上面点「定稿」。`, tone: "todo" };
    }
    default:
      break;
  }
  if (detail.publications?.length) return { tab: "publish", text: "已发布，链接已记录。", tone: "done" };
  if (latest && ACTIVE.includes(latest.status)) return { tab: "video", text: "成片生成中，通常 15–30 分钟，可以离开此页。", tone: "wait" };
  if (latest?.status === "COMPLETED") return { tab: "publish", text: "成片完成：看片、到 B 站手动上传，再回来记录链接。", tone: "todo" };
  if (latest && (latest.status === "FAILED" || latest.status === "CANCELLED")) return { tab: "video", text: "上次出片没成功，看原因后重新出片。", tone: "bad" };
  return { tab: "video", text: "已定稿，下一步生成成片。", tone: "todo" };
}
