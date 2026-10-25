import { publishedPlatformCount } from "@/lib/distributions";
import type { VideoScriptDetail, VideoRender } from "@/api/video";

export type ScriptTab = "draft" | "review" | "video" | "publish" | "compare";

export const TAB_LABEL: Record<ScriptTab, string> = {
  draft: "稿件",
  review: "审校",
  video: "成片",
  publish: "发布",
  compare: "版本对比",
};

export interface NextStep {
  tab: ScriptTab;
  text: string;
  tone: "wait" | "todo" | "done" | "bad";
}

const ACTIVE = ["SUBMITTING", "QUEUED", "RUNNING", "DOWNLOADING"];

/** Prefer an in-flight attempt, then a usable completed video, for this version. */
export function renderForVersion(detail: VideoScriptDetail, version = detail.current_version): VideoRender | null {
  const renders = detail.renders.filter((r) => r.version === version);
  return renders.find((r) => ACTIVE.includes(r.status))
    ?? renders.find((r) => r.status === "COMPLETED" && r.files?.some((f) => f.name === "video.mp4"))
    ?? renders.find((r) => r.status === "COMPLETED") ?? renders[0] ?? null;
}

export function currentPublications(detail: VideoScriptDetail) {
  return (detail.publications ?? []).filter((p) => p.version === detail.current_version);
}

/** What the person should do next with a script, and the tab where they do it. */
export function nextStep(detail: VideoScriptDetail): NextStep {
  const latest = renderForVersion(detail);
  switch (detail.status) {
    case "RESEARCHING":
    case "WRITING":
      if (detail.topic?.article) return { tab: "draft", text: "正在根据文章生成文稿，原文会保留。可以离开此页。", tone: "wait" };
      return { tab: "draft", text: "正在研究和写稿，完成后会出现草稿。可以离开此页。", tone: "wait" };
    case "PLAN_READY":
      return { tab: "draft", text: "讲述方案定好了：读一下，可以改字、带意见重做或放弃；确认后才开始写稿。", tone: "todo" };
    case "FAILED":
      return { tab: "draft", text: "这篇没有完成，原因见下面。", tone: "bad" };
    case "DRAFT": {
      const checks = detail.version_shown?.checks;
      if (detail.stage) return { tab: "review", text: detail.topic?.article ? "正在对照文章审校当前文稿。可以离开此页。" : "正在复审当前文稿，完成后再检查事实与来源。可以离开此页。", tone: "wait" };
      if (checks?.mode === "on_request") return { tab: "draft", text: "文稿已生成，读稿后可定稿出片，也可以修改或重新生成。", tone: "todo" };
      const hint = checks?.summary?.serious ? `事实审计有 ${checks.summary.serious} 处严重问题，请先核实，` : "";
      return { tab: "draft", text: `${hint}读稿、看过「审校」页后，在上面点「定稿」。`, tone: "todo" };
    }
    default:
      break;
  }
  const count = publishedPlatformCount(detail.distributions?.filter(d => d.version === detail.current_version), currentPublications(detail));
  if (count) return { tab: "publish", text: `已发布至 ${count} 个平台，可继续分发到其他平台；链接见发布记录。`, tone: "done" };
  const upload = detail.distributions?.find(d => d.version === detail.current_version && ["QUEUED", "UPLOADING", "SUBMITTED"].includes(d.status))?.task ?? detail.platform_uploads?.find(task => task.version === detail.current_version);
  if (upload && ["QUEUED", "UPLOADING", "RUNNING", "SUBMITTED"].includes(upload.status)) return { tab: "publish", text: "投稿正在处理中，公开后自动回填发布记录。", tone: "wait" };
  if (latest && ACTIVE.includes(latest.status)) return { tab: "video", text: "成片正在后台制作，完成后自动回传，可以离开此页。", tone: "wait" };
  if (latest?.status === "COMPLETED") return { tab: "publish", text: "成片完成：看片后到平台管理投稿，公开后自动记录发布链接。", tone: "todo" };
  if (latest && (latest.status === "FAILED" || latest.status === "CANCELLED")) return { tab: "video", text: "上次出片没成功，看原因后重新出片。", tone: "bad" };
  return { tab: "video", text: "已定稿，下一步生成成片。", tone: "todo" };
}
