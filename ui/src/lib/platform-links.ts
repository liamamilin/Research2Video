export const UPLOAD_LABEL: Record<string, string> = {
  QUEUED: "等待上传", UPLOADING: "上传中", SUBMITTED: "待平台审核", PUBLISHED: "已发布",
  UNKNOWN: "投稿结果待核对", FAILED: "投稿检查失败", CANCELLED: "投稿已取消",
};

export function documentPublishLink(scriptId: string, version: number) {
  return `/video/${encodeURIComponent(scriptId)}?tab=publish&publish_version=${version}`;
}

export function platformUploadLink(scriptId: string, version: number, target: { renderId?: string; taskId?: string }) {
  const query = new URLSearchParams({ tab: "uploads", from_script: scriptId, version: String(version) });
  if (target.taskId) query.set("task", target.taskId);
  else if (target.renderId) query.set("render", target.renderId);
  return `/platforms?${query}`;
}
