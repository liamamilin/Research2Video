import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { VideoScriptDetail } from "@/api/video";
import { ScriptIteration } from "./ScriptIteration";

const BASE = { id: "s1", current_version: 3, approved_version: 1, status: "DRAFT", report_path: "report.md",
  renders: [{ id: "r1", version: 1, status: "COMPLETED", files: [] }],
  configuration: { recipe_available: true, changed_since_generation: true,
    links: [{ label: "文稿配方与提示词", name: "快讯", href: "/video-recipes/flash", scope: "此配方的后续写稿", next: "保存后重新生成文稿" }] }
} as unknown as VideoScriptDetail;
afterEach(cleanup);

function show(detail = BASE, extras = {}) {
  const onRewrite = vi.fn(), onRender = vi.fn();
  render(<MemoryRouter><ScriptIteration detail={detail} canEdit isCurrent editing={false} busy={null}
    onRewrite={onRewrite} onRender={onRender} onVideo={() => {}} {...extras} /></MemoryRouter>);
  return { onRewrite, onRender };
}

describe("Iteration entry points", () => {
  it("distinguishes new draft and old video, links exact config and requires approval before rendering", () => {
    const actions = show();
    expect(screen.getByText(/这个视频仍使用旧稿/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "重新生成视频" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "重新生成文稿" }));
    expect(actions.onRewrite).toHaveBeenCalledOnce();
    expect(screen.getByRole("link", { name: "调整配方与提示词" }).getAttribute("href")).toBe("/video-recipes/flash?from=s1");
  });
  it("renders the approved current draft directly without triggering a rewrite", () => {
    const actions = show({ ...BASE, status: "APPROVED", approved_version: 3 });
    fireEvent.click(screen.getByRole("button", { name: "重新生成视频" }));
    expect(actions.onRender).toHaveBeenCalledOnce();
    expect(actions.onRewrite).not.toHaveBeenCalled();
  });
  it("blocks regeneration for historical views and while another render is running", () => {
    show(BASE, { isCurrent: false });
    expect((screen.getByRole("button", { name: "重新生成文稿" }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    show({ ...BASE, renders: [{ ...BASE.renders[0], status: "RUNNING" }] });
    expect((screen.getByRole("button", { name: "重新生成文稿" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
