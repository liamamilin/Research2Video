import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HomeBoardPage } from "./HomeBoard";

const BOARD = {
  stages: [
    { key: "researching", label: "研究", count: 0 },
    { key: "draft", label: "草稿", count: 2 },
    { key: "approved", label: "定稿", count: 1 },
    { key: "rendering", label: "成片中", count: 0 },
    { key: "rendered", label: "成片完成", count: 1 },
    { key: "published", label: "已发布", count: 3 },
  ],
  failed: 1,
  attention: [
    { kind: "review", script_id: "s1", title: "盘点 10 月 19 日", column_key: "roundup", text: "草稿等你审校定稿" },
    { kind: "render_done", script_id: "s2", title: "Gemini 4 Argon 上线", column_key: "flash", text: "成片完成，看片后发布并记录链接" },
  ],
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(BOARD) }) as Response));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Home board", () => {
  it("counts the scripts at each stage and links what needs attention to its script", async () => {
    render(<MemoryRouter><HomeBoardPage /></MemoryRouter>);
    expect(await screen.findByText("草稿")).toBeTruthy();
    expect(screen.getByText("已发布").previousSibling?.textContent).toBe("3");
    const review = (await screen.findByText("盘点 10 月 19 日")).closest("a");
    expect(review?.getAttribute("href")).toBe("/video/s1");
    expect(screen.getByText("待发布")).toBeTruthy();
    expect(screen.getByText(/另有 1 篇失败/)).toBeTruthy();
  });
});
