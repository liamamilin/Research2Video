import type { TopicScript, VideoTopic } from "@/api/video";

export const cellOf = (s: TopicScript) => s.cell_key ?? s.column_key.replace(/(?:-m\d+(?:p5)?)?(?:-nr)?$/, "");
export const scriptsHere = (t: VideoTopic, column?: string | null) => (t.scripts ?? []).filter(s => cellOf(s) === column)
  .sort((a, b) => Number(Boolean(a.archived)) - Number(Boolean(b.archived)) || b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
export const topicTaken = (t: VideoTopic, column?: string | null) => scriptsHere(t, column).some(s => s.status !== "FAILED" || s.selection_skip);
export const videoState = (s?: TopicScript) => s?.render_state ?? "none";

export const TASK_LABEL = { unwritten: "待写", writing: "写稿中", draft: "文稿待处理", ready: "待出片", rendering: "出片中", done: "已出片", skipped: "已跳过", failed: "异常" };
export type TopicTask = keyof typeof TASK_LABEL;

export function topicTask(t: VideoTopic, column?: string | null): TopicTask {
  const s = scriptsHere(t, column)[0];
  if (!s) return "unwritten";
  if (s.selection_skip) return "skipped";
  if (s.status === "FAILED" || videoState(s) === "failed") return "failed";
  if (videoState(s) === "done") return "done";
  if (videoState(s) === "working") return "rendering";
  if (["RESEARCHING", "WRITING"].includes(s.status)) return "writing";
  return s.status === "APPROVED" ? "ready" : "draft";
}

/** Only this app's topic list is a valid return destination. */
export function topicReturn(value: string | null): string | null {
  return value && /^\/topics(?:[?#]|$)/.test(value) && !/[\r\n\\]/.test(value) ? value : null;
}

export function scriptFromTopic(id: string, back: string, tab = "draft"): string {
  return `/video/${encodeURIComponent(id)}?${new URLSearchParams({ tab, return_to: back })}`;
}
