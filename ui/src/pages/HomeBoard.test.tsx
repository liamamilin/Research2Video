import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BoardItem, VideoBoard } from "@/api/video";
import { HomeBoardPage } from "./HomeBoard";

const item = (id: string, matrix: string, action: BoardItem["action"], group: BoardItem["group"]): BoardItem => ({
  script_id: id, title: `视频 ${id}`, column_key: "same_recipe", column_label: "专题速览 · 大众", matrix,
  matrix_name: matrix === "ai" ? "AI 矩阵" : matrix === "medical" ? "医疗矩阵" : "独立视频", job: `${matrix}/daily`,
  job_name: matrix === "medical" ? "医疗更新" : "每日更新", status: "APPROVED", stage: "rendered", version: 1,
  created_at: "2026-10-22T10:00:00+0800", updated_at: "2026-10-23T10:00:00+0800",
  credibility: { tier: action === "review" ? "verify" : "trusted", text: action === "review" ? "有待核实" : "有出处可信", reasons: ["检查来源"], cited_share: 1 },
  has_video: true, has_previous_video: false, action, group, action_label: action === "review" ? "核实文稿" : "看片与发布", note: "这是下一步说明",
});
const BOARD: VideoBoard = {
  stages: [], failed: 0, attention: [], matrices: [{ key: "ai", name: "AI 矩阵" }, { key: "medical", name: "医疗矩阵" }, { key: "empty", name: "空矩阵" }],
  items: [item("a", "ai", "review", "todo"), item("b", "ai", "publish", "publish"), item("c", "medical", "review", "todo"),
    item("d", "medical", "publish", "publish"), item("e", "", "working", "working"), item("f", "ai", "published", "done")],
};
const response = (value: unknown) => ({ ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(value) }) as Response;
function stub(board = BOARD) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => response(String(url).includes("/board") ? board : String(url).includes("/alerts") ? [] : { plans: [], runs: [] })));
}
function Location() { return <output aria-label="当前位置">{useLocation().search}</output>; }
const open = (url = "/") => render(<MemoryRouter initialEntries={[url]}><HomeBoardPage /><Location /></MemoryRouter>);
beforeEach(() => stub());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe("Matrix video board", () => {
  it("groups the same recipe by actual source matrix and scopes every count together", async () => {
    open();
    await screen.findByRole("region", { name: "矩阵视频 AI 矩阵" });
    expect(screen.getByRole("region", { name: "矩阵视频 医疗矩阵" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "待处理 2" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "待发布 2" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "医疗矩阵，2 个视频" }));
    expect(screen.getByRole("button", { name: "待处理 1" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "全部视频 2" })).toBeTruthy();
    expect(screen.queryByRole("article", { name: "视频 视频 a" })).toBeNull();
    expect(screen.getByRole("article", { name: "视频 视频 c" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "待发布 1" }));
    expect(screen.getByRole("article", { name: "视频 视频 d" })).toBeTruthy();
    expect(screen.queryByRole("article", { name: "视频 视频 c" })).toBeNull();
    expect(screen.getByLabelText("当前位置").textContent).toContain("matrix=medical");
  });

  it("deep links preserve matrix and view, and primary actions open the matching tab", async () => {
    open("/?matrix=medical&view=all");
    const row = await screen.findByRole("article", { name: "视频 视频 c" });
    expect(within(row).getByRole("link", { name: "核实文稿：视频 c" }).getAttribute("href")).toBe("/video/c?tab=review");
    expect(screen.getByRole("link", { name: "看片与发布：视频 d" }).getAttribute("href")).toBe("/video/d?tab=publish");
    expect(screen.queryByRole("article", { name: "视频 视频 a" })).toBeNull();
  });

  it("previews the highest priority video and exposes every remaining video inside its matrix", async () => {
    stub({ ...BOARD, items: [item("draft", "ai", "approve", "todo"), item("review", "ai", "review", "todo")] });
    open();
    await screen.findByRole("article", { name: "视频 视频 review" });
    expect(screen.queryByRole("article", { name: "视频 视频 draft" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /进入矩阵，查看其余 1 个视频/ }));
    expect(screen.getByRole("article", { name: "视频 视频 draft" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "待处理 2" })).toBeTruthy();
  });

  it("shows an older finished video as history beside its current draft", async () => {
    const row = { ...item("new", "ai", "approve", "todo"), status: "DRAFT" as const, version: 4, has_video: false, has_previous_video: true, action_label: "查看并定稿" };
    stub({ ...BOARD, items: [row] }); open();
    const article = await screen.findByRole("article", { name: "视频 视频 new" });
    expect(within(article).getByText("有旧版成片")).toBeTruthy();
    expect(within(article).getByText("待定稿")).toBeTruthy();
    expect(screen.getByRole("button", { name: "待发布 0" })).toBeTruthy();
  });

  it("loads automation only when opened and never shows the same batch video beside the queue", async () => {
    open(); await screen.findByRole("article", { name: "视频 视频 a" });
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/autopilot"))).toBe(false);
    fireEvent.click(screen.getByText("自动化管理"));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/autopilot"))).toBe(true));
  });

  it("keeps empty scopes, filtered emptiness and independent videos understandable", async () => {
    open(); await screen.findByRole("button", { name: "空矩阵，0 个视频" });
    fireEvent.click(screen.getByRole("button", { name: "空矩阵，0 个视频" }));
    expect(screen.getByText("当前矩阵没有待处理的视频")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "独立视频，1 个视频" }));
    fireEvent.click(screen.getByRole("button", { name: "制作中 1" }));
    expect(screen.getByRole("article", { name: "视频 视频 e" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("搜索视频"), { target: { value: "不存在" } });
    expect(screen.getByText("没有符合筛选的视频")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "清除筛选" })[0]);
    expect(screen.getByRole("article", { name: "视频 视频 e" })).toBeTruthy();
  });

  it("keeps the last successful result when refresh fails and supports retry", async () => {
    open(); await screen.findByRole("article", { name: "视频 视频 a" });
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("offline"));
    fireEvent.click(screen.getByRole("button", { name: "刷新看板" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("article", { name: "视频 视频 a" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("suspends background polling and refreshes when the page becomes visible", async () => {
    vi.useFakeTimers();
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    open(); await act(async () => {});
    const calls = () => vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes("/board")).length;
    expect(calls()).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(calls()).toBe(1);
    hidden.mockReturnValue(false);
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(calls()).toBe(2);
  });
});
