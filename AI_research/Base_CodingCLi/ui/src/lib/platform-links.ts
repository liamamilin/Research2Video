export const UPLOAD_LABEL: Record<string, string> = {
  QUEUED: "等待上传", UPLOADING: "上传中", SUBMITTED: "已提交，待公开确认", PUBLISHED: "已发布",
  UNKNOWN: "投稿结果待核对", FAILED: "投稿检查失败", CANCELLED: "投稿已取消",
};

export function workflowReturn(value: string | null) {
  if (!value || !/^\/(topics|publish)(?:[?#]|$)/.test(value)) return null;
  return value;
}
export function documentPublishLink(scriptId: string, version: number, renderId?: string, back?: string) {
  const query = new URLSearchParams({ tab: "publish", publish_version: String(version) });
  if (renderId) query.set("render", renderId);
  if (workflowReturn(back ?? null)) query.set("return_to", back!);
  return `/video/${encodeURIComponent(scriptId)}?${query}`;
}

export function platformUploadLink(scriptId: string, version: number, target: { renderId?: string; taskId?: string; platform?: string }) {
  const query = new URLSearchParams({ tab: "uploads", from_script: scriptId, version: String(version) });
  if (target.platform) query.set("platform", target.platform);
  if (target.taskId) query.set("task", target.taskId);
  if (target.renderId) query.set("render", target.renderId);
  return `/platforms?${query}`;
}
