import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OutputsPage } from "./Outputs";

const stamp = (ms: number) => new Date(ms + 8 * 3600000).toISOString().slice(0, 19) + "+0800";
const now = stamp(Date.now()), old = stamp(Date.now() - 60 * 86400000);
const base = { column_key: "flash", archived: false, auto: false, matrix: "ai", job: "monitoring/ai_flash_discovery", manual: false,
  production: "flash", production_name: "快讯", production_guessed: true, render: null, published: false };
const DATA = {
  scripts: [
    { ...base, id: "a", title: "新的一篇", status: "APPROVED", created_at: now, report_path: "output/flash/2026-10-20.md", report_date: "2026-10-20", render: { status: "COMPLETED", created_at: now } },
    { ...base, id: "b", title: "旧的一篇", status: "DRAFT", created_at: old, report_path: "output/flash/2026-08-20.md", report_date: "2026-08-20" },
    { ...base, id: "c", title: "手填的一篇", status: "DRAFT", created_at: now, matrix: "", job: "", manual: true, production: "", production_name: "", production_guessed: false, report_path: "", report_date: "" },
  ],
  jobs: [{ job: "monitoring/ai_flash_discovery", name: "AI Flash Discovery", reports: 5, unwritten: 3, matrix: "ai" }],
  matrices: [{ key: "ai", name: "AI 矩阵" }],
};
const response = (body = DATA) => ({ ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) }) as Response;
let urls: string[] = [];
beforeEach(() => { urls = []; vi.stubGlobal("fetch", vi.fn(async (url: string) => { urls.push(String(url)); return response(); })); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const open = (path = "/outputs") => render(<MemoryRouter initialEntries={[path]}><OutputsPage /></MemoryRouter>);
const titles = () => screen.getAllByRole("listitem").map((li) => li.getAttribute("aria-label"));
const selectAI = async () => { fireEvent.click(await screen.findByRole("button", { name: "查看矩阵 AI 矩阵 的产出" })); };

describe("Outputs", () => {
  it("keeps multiple programme contents inside one output card and one video link", async () => {
    const body = { ...DATA, scripts: [{ ...DATA.scripts[0], content_mode: "episode", episode_items: ["端侧长会话", "模型成本变化"] }] };
    vi.mocked(fetch).mockResolvedValue(response(body));
    open("/outputs?matrix=ai");
    const row = await screen.findByLabelText("文稿 新的一篇");
    expect(within(row).getByText("整期节目 · 2 个内容 · 端侧长会话 / 模型成本变化")).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(within(row).getByRole("link", { name: "查看成片：新的一篇" }).getAttribute("href")).toBe("/video/a?tab=video");
  });

  it("retries failed reads, then preserves the result if a later refresh fails", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("offline"));
    open();
    expect((await screen.findByRole("alert")).textContent).toContain("加载失败");
    expect(screen.queryByText("还没有产出文稿")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await selectAI();
    expect(screen.queryByRole("alert")).toBeNull();
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("offline"));
    fireEvent.click(screen.getByRole("button", { name: "刷新产出" }));
    expect(await screen.findByText("暂时显示上次读取的产出。")).toBeTruthy();
    expect(screen.getByRole("link", { name: "新的一篇" })).toBeTruthy();
  });

  it("ignores an old response after switching the archive scope", async () => {
    let resolveOld!: (value: Response) => void;
    vi.mocked(fetch).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    open();
    fireEvent.change(screen.getByLabelText("归档范围"), { target: { value: "1" } });
    await selectAI();
    await act(async () => { resolveOld(response({ ...DATA, scripts: [] })); });
    expect(screen.getByRole("link", { name: "新的一篇" })).toBeTruthy();
    expect(urls.at(-1)).toContain("/api/outputs?archived=true");
  });

  it("previews videos immediately, then narrows to one matrix without nested expansion", async () => {
    open();
    expect(await screen.findByRole("link", { name: "新的一篇" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "手填的一篇" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "旧的一篇" })).toBeNull();
    expect(screen.getByRole("link", { name: "查看成片：新的一篇" }).getAttribute("href")).toBe("/video/a?tab=video");
    await selectAI();
    expect(titles()).toEqual(["文稿 新的一篇", "文稿 旧的一篇"]);
    expect(screen.queryByRole("link", { name: "手填的一篇" })).toBeNull();
    const row = screen.getByLabelText("文稿 新的一篇");
    expect(within(row).getByRole("link", { name: "2026-10-20 的报告" }).getAttribute("href")).toBe("/reports/view?path=flash%2F2026-10-20.md");
    fireEvent.click(within(row).getByLabelText("来源详情：新的一篇"));
    expect(row.textContent).toContain("该任务累计还有 3 次运行的报告未写视频");
    expect(within(row).getByRole("link", { name: "查看运行记录" }).getAttribute("href")).toMatch(/^\/matrices\/[^/?]+\?tab=runs&job=/);
    fireEvent.click(screen.getByRole("button", { name: "返回全部矩阵" }));
    expect(screen.getByRole("link", { name: "手填的一篇" })).toBeTruthy();
  });

  it("lists by time, reverses order and filters creation time and current render state", async () => {
    open(); await screen.findByRole("button", { name: "查看矩阵 AI 矩阵 的产出" });
    fireEvent.click(screen.getByRole("button", { name: "按时间" }));
    expect(titles()[2]).toBe("文稿 旧的一篇");
    fireEvent.click(screen.getByRole("button", { name: "筛选" }));
    fireEvent.click(screen.getByRole("button", { name: "最新在前" }));
    expect(titles()[0]).toBe("文稿 旧的一篇");
    fireEvent.change(screen.getByLabelText("文稿时间"), { target: { value: "30" } });
    expect(titles()).not.toContain("文稿 旧的一篇");
    fireEvent.change(screen.getByLabelText("成片状态"), { target: { value: "done" } });
    expect(titles()).toEqual(["文稿 新的一篇"]);
  });

  it("preserves deep links and shows current version, old video and fact audit independently", async () => {
    const body = { ...DATA, scripts: [{ ...DATA.scripts[1], title: "新版文稿", version: 4, has_video: false, has_previous_video: true,
      credibility: { tier: "verify", text: "有待核实", reasons: ["核对事实"], cited_share: .5 } }] };
    vi.mocked(fetch).mockResolvedValue(response(body));
    open("/outputs?matrix=ai&view=time&q=新版");
    const row = await screen.findByLabelText("文稿 新版文稿");
    expect(within(row).getByText("v4")).toBeTruthy();
    expect(within(row).getByText("有旧版成片")).toBeTruthy();
    expect(within(row).getByText("有待核实")).toBeTruthy();
    expect(within(row).queryByText("成片完成")).toBeNull();
    expect((screen.getByLabelText("矩阵") as HTMLSelectElement).value).toBe("ai");
    expect((screen.getByLabelText("搜索产出") as HTMLInputElement).value).toBe("新版");
  });

  it("does not lose a script whose matrix is absent from the current catalog", async () => {
    vi.mocked(fetch).mockResolvedValue(response({ ...DATA, matrices: [] }));
    open();
    await screen.findByRole("button", { name: "查看矩阵 ai 的产出" });
    fireEvent.click(screen.getByRole("button", { name: "按时间" }));
    expect(screen.getByRole("link", { name: "新的一篇" })).toBeTruthy();
  });

  it("opens a selected matrix automatically and keeps other matrices out of its records", async () => {
    open("/outputs?matrix=ai");
    await screen.findByRole("link", { name: "新的一篇" });
    expect(screen.getByRole("button", { name: "返回全部矩阵" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "手填的一篇" })).toBeNull();
    fireEvent.change(screen.getByLabelText("搜索产出"), { target: { value: "不存在" } });
    expect(screen.getByText("没有符合筛选的文稿")).toBeTruthy();
  });
});

it("clears search conditions while preserving the selected matrix", async () => {
  open("/outputs?matrix=ai&q=不存在");
  await screen.findByText("没有符合筛选的文稿");
  fireEvent.click(screen.getAllByRole("button", { name: "清除条件" })[0]);
  expect(screen.getByRole("link", { name: "新的一篇" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "手填的一篇" })).toBeNull();
  expect((screen.getByLabelText("矩阵") as HTMLSelectElement).value).toBe("ai");
});

it("bounds long lists and reveals more without fetching again", async () => {
  vi.mocked(fetch).mockResolvedValue(response({ ...DATA, scripts: Array.from({ length: 28 }, (_, i) => ({ ...DATA.scripts[0], id: `many-${i}`, title: `文稿 ${i}` })) }));
  open("/outputs?matrix=ai");
  await screen.findByLabelText("文稿 文稿 0");
  expect(titles()).toHaveLength(12);
  fireEvent.click(screen.getByRole("button", { name: /加载更多文稿/ }));
  expect(titles()).toHaveLength(24);
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: /加载更多文稿/ }));
  expect(titles()).toHaveLength(28);
  expect(screen.queryByRole("button", { name: /加载更多文稿/ })).toBeNull();
});
