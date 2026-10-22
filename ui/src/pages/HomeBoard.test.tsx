import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
  it("shows a script that needs two things once, and filters the list by what needs doing", async () => {
    const board = { ...BOARD, attention: [
      { kind: "render_done", script_id: "s2", title: "Gemini 4 Argon 上线", column_key: "flash", text: "成片完成，看片后发布并记录链接", created_at: "2026-10-22T12:00:00+0800" },
      { kind: "recheck", script_id: "s2", title: "Gemini 4 Argon 上线", column_key: "flash", text: "研究是 50 小时前做的", created_at: "2026-10-22T12:00:00+0800" },
      { kind: "review", script_id: "s1", title: "盘点 10 月 19 日", column_key: "roundup", text: "草稿等你审校定稿", created_at: "2026-10-22T11:00:00+0800" },
    ] };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(board) }) as Response));
    render(<MemoryRouter><HomeBoardPage /></MemoryRouter>);
    expect((await screen.findAllByText("Gemini 4 Argon 上线")).length).toBe(1);
    expect(screen.getByRole("button", { name: "全部 2" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "待审校 1" }));
    expect(screen.queryByText("Gemini 4 Argon 上线")).toBeNull();
    expect(screen.getByText("盘点 10 月 19 日")).toBeTruthy();
  });

  it("counts the scripts at each stage and links what needs attention to its script", async () => {
    render(<MemoryRouter><HomeBoardPage /></MemoryRouter>);
    expect(await screen.findByText("草稿")).toBeTruthy();
    expect(screen.getByText("已发布").previousSibling?.textContent).toBe("3");
    const review = (await screen.findByText("盘点 10 月 19 日")).closest("a");
    expect(review?.getAttribute("href")).toBe("/video/s1");
    expect(screen.getAllByText("待发布").length).toBeGreaterThan(0);
    expect(screen.getByText(/另有 1 篇失败/)).toBeTruthy();
  });
});
