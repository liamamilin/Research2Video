import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScriptScreenshots } from "./ScriptScreenshots";
import * as api from "@/api/screenshots";

vi.mock("@/api/screenshots", () => ({ getScreenshots: vi.fn(), captureScreenshot: vi.fn(), registerScreenshot: vi.fn(), saveScreenshotSelection: vi.fn() }));

const ASSET = { id: "a1", version: 1, image_id: "registered", preview_url: "/image.png", url: "https://example.com/pricing", final_url: "https://example.com/pricing",
  title: "官方价格页", captured_at: "2026-10-25T10:00:00Z", width: 1440, height: 900, mode: "viewport" as const, selector: "", clip: null };
const DATA = { sources: [{ url: ASSET.url, title: ASSET.title }], assets: [ASSET], chapters: [{ title: "费用", paragraphs: ["先看适用条件。", "再看价格。"] }],
  selection: { form: "object_showcase" as const, materials: [] }, problem: "" };

afterEach(cleanup);
beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.getScreenshots).mockResolvedValue(structuredClone(DATA)); vi.mocked(api.saveScreenshotSelection).mockResolvedValue(DATA.selection); });

describe("网页截图与段落配图", () => {
  it("shows automatically applied pictures and fallback reasons without asking for confirmation", async () => {
    vi.mocked(api.getScreenshots).mockResolvedValue({ ...structuredClone(DATA), automation_enabled: true,
      selection: { form: "object_showcase", origin: "automatic", materials: [{ screenshot_id: "a1", caption: "来源截图", role: "detail", placements: [{ chapter_index: 0, paragraph_index: 1 }] }] },
      automatic: { state: "completed", reason: "自动配图1张", images: 1, needs: 2, accepted: 1, text: 1,
        decisions: [{ need_id: "c1p1", status: "text", reason: "计费周期不一致" }] } });
    render(<ScriptScreenshots id="s1" version={1} canEdit />);
    fireEvent.click(screen.getByRole("button", { name: /网页截图与段落配图/ }));
    await screen.findByLabelText("自动配图结果");
    expect(screen.getByLabelText("自动配图结果").textContent).toContain("计费周期不一致");
    expect((screen.getByLabelText("使用截图：官方价格页") as HTMLInputElement).checked).toBe(true);
    expect(api.saveScreenshotSelection).not.toHaveBeenCalled();
    expect(screen.getByText(/自动配图已用于本次制作/)).toBeTruthy();
  });
  it("loads only when expanded, requires a paragraph, preserves edits across collapse and saves explicit positions", async () => {
    render(<ScriptScreenshots id="s1" version={1} canEdit />);
    expect(api.getScreenshots).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /网页截图与段落配图/ }));
    await screen.findByLabelText("使用截图：官方价格页");
    fireEvent.click(screen.getByLabelText("使用截图：官方价格页"));
    expect((screen.getByRole("button", { name: "确认并保存段落配图" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText(/费用 · 第2段/));
    fireEvent.change(screen.getByLabelText("画面说明：官方价格页"), { target: { value: "当前官方价格" } });
    fireEvent.click(screen.getByRole("button", { name: /网页截图与段落配图/ }));
    fireEvent.click(screen.getByRole("button", { name: /网页截图与段落配图/ }));
    expect((screen.getByLabelText("画面说明：官方价格页") as HTMLInputElement).value).toBe("当前官方价格");
    fireEvent.click(screen.getByRole("button", { name: "确认并保存段落配图" }));
    await waitFor(() => expect(api.saveScreenshotSelection).toHaveBeenCalledWith("s1", 1, { form: "object_showcase", materials: [
      { screenshot_id: "a1", caption: "当前官方价格", role: "reference", placements: [{ chapter_index: 0, paragraph_index: 1 }] },
    ] }));
    expect(api.getScreenshots).toHaveBeenCalledTimes(1);
  });

  it("keeps an unregistered screenshot visible and offers registration retry", async () => {
    vi.mocked(api.captureScreenshot).mockResolvedValue({ asset: { ...ASSET, id: "a2", image_id: null }, problem: "截图已保存，视频引擎登记失败" });
    render(<ScriptScreenshots id="s1" version={1} canEdit />);
    fireEvent.click(screen.getByRole("button", { name: /网页截图与段落配图/ }));
    await screen.findByLabelText("截图来源网址");
    fireEvent.click(screen.getByRole("button", { name: "获取网页截图" }));
    await screen.findByRole("button", { name: "重新登记图片" });
    expect(screen.getByRole("alert").textContent).toContain("截图已保存");
    expect(api.captureScreenshot).toHaveBeenCalledWith("s1", { version: 1, url: ASSET.url, mode: "viewport" });
  });

  it("shows historical version assets without capture or editing controls", async () => {
    render(<ScriptScreenshots id="s1" version={1} canEdit={false} />);
    fireEvent.click(screen.getByRole("button", { name: /网页截图与段落配图/ }));
    await screen.findByAltText("官方价格页");
    expect(screen.queryByRole("button", { name: "获取网页截图" })).toBeNull();
    expect(screen.queryByRole("button", { name: "确认并保存段落配图" })).toBeNull();
  });
});
