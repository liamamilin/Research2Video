import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OutputsPage } from "./Outputs";

// 后端的格式：+0800，没有冒号
const stamp = (ms: number) => new Date(ms + 8 * 3600000).toISOString().slice(0, 19) + "+0800";
const now = stamp(Date.now());
const old = stamp(Date.now() - 60 * 86400000);
const base = { column_key: "flash", archived: false, auto: false, matrix: "ai", job: "monitoring/ai_flash_discovery", manual: false,
  production: "flash", production_name: "快讯", production_guessed: true, render: null, published: false };
const DATA = {
  scripts: [
    { ...base, id: "a", title: "新的一篇", status: "APPROVED", created_at: now, report_path: "output/flash/2026-10-20.md", report_date: "2026-10-20",
      render: { status: "COMPLETED", created_at: now } },
    { ...base, id: "b", title: "旧的一篇", status: "DRAFT", created_at: old, report_path: "output/flash/2026-08-20.md", report_date: "2026-08-20" },
    { ...base, id: "c", title: "手填的一篇", status: "DRAFT", created_at: now, matrix: "", job: "", manual: true, production: "", production_name: "",
      production_guessed: false, report_path: "", report_date: "" },
  ],
  jobs: [{ job: "monitoring/ai_flash_discovery", name: "AI Flash Discovery", reports: 5, unwritten: 3, matrix: "ai" }],
  matrices: [{ key: "ai", name: "AI 矩阵" }],
};

let urls: string[] = [];
beforeEach(() => {
  urls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(String(url));
    return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(DATA) } as Response;
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const open = () => render(<MemoryRouter><OutputsPage /></MemoryRouter>);
const titles = () => screen.getAllByRole("listitem").map((li) => li.getAttribute("aria-label"));

describe("Outputs", () => {
  it("groups scripts by matrix › Job › report and says how many runs were not made into videos", async () => {
    open();
    const job = await screen.findByLabelText("Job AI Flash Discovery");
    expect(within(job).getByText("2026-10-20 那次运行的报告").getAttribute("href")).toBe("/reports/view?path=flash%2F2026-10-20.md"); // the report page reads inside output/
    expect(job.textContent).toContain("另外 3 次运行的报告还没做成视频");
    expect(within(job).getByRole("link", { name: "看这个 Job 的运行" }).getAttribute("href")).toMatch(/^\/matrices\/[^/?]+\?tab=runs&job=/); // the matrix's runs, that Job only
    expect(screen.getByRole("link", { name: "新的一篇" }).getAttribute("href")).toBe("/video/a");
    expect(screen.getByText("手填选题写的")).toBeTruthy();
    expect(titles()).toEqual(["文稿 新的一篇", "文稿 旧的一篇", "文稿 手填的一篇"]); // newest report first
  });

  it("lists everything by time, oldest first on request, and filters by time and render", async () => {
    open();
    await screen.findByLabelText("Job AI Flash Discovery");
    fireEvent.click(screen.getByRole("button", { name: "按时间" }));
    expect(titles()[2]).toBe("文稿 旧的一篇");
    fireEvent.click(screen.getByRole("button", { name: "最新在前" }));
    expect(titles()[0]).toBe("文稿 旧的一篇");
    fireEvent.change(screen.getByLabelText("文稿时间"), { target: { value: "30" } });
    expect(titles()).not.toContain("文稿 旧的一篇");
    fireEvent.change(screen.getByLabelText("成片状态"), { target: { value: "done" } });
    expect(titles()).toEqual(["文稿 新的一篇"]);
  });

  it("shows how far a finished script got with its video, not just that it was approved", async () => {
    open();
    const done = await screen.findByLabelText("文稿 新的一篇"); // approved with a finished render
    expect(within(done).getByText("成片完成")).toBeTruthy();
    expect(within(done).queryByText("已定稿")).toBeNull();
    expect(within(screen.getByLabelText("文稿 旧的一篇")).getByText("待审稿")).toBeTruthy(); // no video yet: the script's own status
  });

  it("puts the matrix with the newest script first and marks a put-away matrix", async () => {
    const wide = { ...DATA, matrices: [{ key: "old", name: "旧矩阵", archived: true }, { key: "ai", name: "AI 矩阵" }],
      scripts: [...DATA.scripts, { ...base, id: "z", title: "旧矩阵的", status: "APPROVED", created_at: old, matrix: "old", job: "x/y", report_path: "output/x.md", report_date: "2026-08-01" }] };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(wide) }) as Response));
    open();
    await screen.findByLabelText("矩阵 ai");
    const order = screen.getAllByLabelText(/^矩阵 /).map((e) => e.getAttribute("aria-label"));
    expect(order.indexOf("矩阵 ai")).toBeLessThan(order.indexOf("矩阵 old"));
    expect(within(screen.getByLabelText("矩阵 old")).getByText("已归档")).toBeTruthy();
  });

  it("asks for archived scripts when told to", async () => {
    open();
    await screen.findByLabelText("Job AI Flash Discovery");
    fireEvent.click(screen.getByLabelText(/看已归档的/));
    await screen.findByLabelText("Job AI Flash Discovery");
    expect(urls.at(-1)).toContain("/api/outputs?archived=true");
  });
});
