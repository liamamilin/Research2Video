import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { VideoScriptDetail } from "@/api/video";
import { ScriptIteration } from "./ScriptIteration";

const BASE = { id: "s1", current_version: 3, approved_version: 1, status: "DRAFT", report_path: "report.md",
  versions: [], renders: [{ id: "r1", version: 1, status: "COMPLETED", created_at: "2026-10-30", files: [{ name: "video.mp4" }] }],
  configuration: { recipe_available: true, changed_since_generation: true,
    links: [{ label: "文稿配方与提示词", name: "快讯", href: "/video-recipes/flash", scope: "此配方的后续写稿", next: "保存后重新生成文稿" }] }
} as unknown as VideoScriptDetail;
afterEach(cleanup);
function show(detail = BASE, extras = {}) {
  const onRewrite = vi.fn(), onVideo = vi.fn();
  render(<MemoryRouter><ScriptIteration detail={detail} canEdit isCurrent editing={false} busy={null}
    onRewrite={onRewrite} onVideo={onVideo} {...extras} /></MemoryRouter>);
  return { onRewrite, onVideo };
}
describe("Script state and iteration", () => {
  it("keeps older video accessible without calling it the current draft's output", () => {
    const actions = show();
    expect(screen.getByText("v3 尚未出片")).toBeTruthy();
    expect(screen.getByText(/这个视频仍使用旧稿/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "查看旧版 v1 成片" }));
    expect(actions.onVideo).toHaveBeenCalledWith("r1");
    expect(screen.queryByRole("button", { name: "重新生成视频" })).toBeNull();
  });
  it("shows a failed latest attempt alongside the retained usable movie", () => {
    const failed = { ...BASE.renders[0], id: "failed", version: 3, status: "FAILED", files: [] };
    const actions = show({ ...BASE, renders: [failed, BASE.renders[0]] } as VideoScriptDetail);
    expect(screen.getByLabelText("最近出片尝试").textContent).toContain("v3 · 制作失败");
    expect(screen.getByText("已有可用成片仍保留")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "查看此次记录" }));
    expect(actions.onVideo).toHaveBeenCalledWith("failed");
  });
  it.each([["RUNNING", "制作中"], ["COMPLETED", "成片完成"]])("shows the recovered %s movie instead of a newer cancelled duplicate", (status, label) => {
    const cancelled = { ...BASE.renders[0], id: "duplicate", version: 3, status: "CANCELLED", files: [] };
    const recovered = { ...BASE.renders[0], id: "recovered", version: 3, status };
    show({ ...BASE, renders: [cancelled, recovered] } as VideoScriptDetail);
    expect(screen.getByText(`v3 · ${label}`)).toBeTruthy();
    expect(screen.getByLabelText("最近出片尝试").textContent).toContain("v3 · 已取消");
  });
  it("retains regeneration and exact shared-config links in a secondary section", () => {
    const actions = show();
    fireEvent.click(screen.getByText("重新生成与共用设置"));
    fireEvent.click(screen.getByRole("button", { name: "重新生成文稿" }));
    expect(actions.onRewrite).toHaveBeenCalledOnce();
    expect(screen.getByRole("link", { name: "调整配方与提示词" }).getAttribute("href")).toBe("/video-recipes/flash?from=s1");
    expect(screen.getByRole("button", { name: "重新生成文稿" }).className).not.toContain("btn-primary");
  });
  it("blocks re-generation while working, editing or viewing history", () => {
    show(BASE, { isCurrent: false });
    fireEvent.click(screen.getByText("重新生成与共用设置"));
    expect((screen.getByRole("button", { name: "重新生成文稿" }) as HTMLButtonElement).disabled).toBe(true);
    cleanup(); show(BASE, { editing: true });
    expect(screen.queryByRole("button", { name: "重新生成文稿" })).toBeNull();
  });
  it("distinguishes optional review, stale model results and approved text", () => {
    show({ ...BASE, status: "APPROVED", approved_version: 3, version_shown: { version: 3, checks: { mode: "on_request" } } } as VideoScriptDetail);
    expect(screen.getByText("未发起（按需）")).toBeTruthy();
    cleanup(); show({ ...BASE, version_shown: { version: 3, checks: { model_checks_stale: true } } } as VideoScriptDetail);
    expect(screen.getByText("修改后尚未重审")).toBeTruthy();
  });
});
