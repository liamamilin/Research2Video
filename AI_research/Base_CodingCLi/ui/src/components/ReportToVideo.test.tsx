import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReportToVideo } from "./ReportToVideo";

const CELLS = [
  { id: "a", key: "point_short", label: "一个知识点 · 开发者", usable: true, status: "enabled", column: { topic_kind: "single" } },
  { id: "b", key: "ai-developer-roundup-long", label: "盘点", usable: true, status: "experimental", column: { topic_kind: "roundup" } },
];
let PRODUCTION: Record<string, unknown>[] = [];
let report: Record<string, unknown> = {};
let posts: { url: string; body: unknown }[] = [];

beforeEach(() => {
  posts = [];
  PRODUCTION = [];
  report = {
    id: "r9", report_path: "output/monitoring/2026-10-15_人工智能.md", kind: "other", kind_label: "其他报告", job: "人工智能",
    topic_id: "", topic_title: "", research: "", created_by: "admin", created_at: "", on_disk: true, scripts: [],
    title: "人工智能一周监控",
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (init?.method === "POST") posts.push({ url: u, body: JSON.parse(String(init.body)) });
    const body = u.endsWith("/api/video/reports") ? report : u.includes("/cells") ? CELLS
      : u.endsWith("/api/production") ? PRODUCTION : u.includes("/api/production/") ? [{ id: "s7" }] : { id: "s7" };
    return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const mount = () =>
  render(
    <MemoryRouter initialEntries={["/reports/view"]}>
      <Routes>
        <Route path="/reports/view" element={<ReportToVideo path="monitoring/2026-10-15_人工智能.md" />} />
        <Route path="/video/s7" element={<div>new script page</div>} />
      </Routes>
    </MemoryRouter>,
  );

describe("ReportToVideo", () => {
  it("records the report and writes a video from it, with no topic and no research", async () => {
    mount();
    expect(posts).toEqual([]); // nothing is recorded until asked
    fireEvent.click(screen.getByRole("button", { name: "写成视频" }));
    const select = await screen.findByLabelText("写成哪种视频");
    expect(posts[0]).toEqual({ url: expect.stringContaining("/api/video/reports"), body: { path: "monitoring/2026-10-15_人工智能.md" } });
    expect(screen.queryByRole("option", { name: "盘点" })).toBeNull();
    expect((screen.getByLabelText("视频题目") as HTMLInputElement).placeholder).toBe("人工智能一周监控");
    fireEvent.change(select, { target: { value: "point_short" } });
    fireEvent.click(screen.getByRole("button", { name: "写这一篇" }));
    expect(await screen.findByText("new script page")).toBeTruthy();
    expect(posts[1].body).toEqual({ column: "point_short", report_id: "r9" });
  });

  it("offers the Job's own whole-report configs first and writes through one, so the script records it", async () => {
    PRODUCTION = [
      { key: "deep", name: "深度解读 · 开发者", job: "monitoring/人工智能", split: "whole", errors: [] },
      { key: "flash", name: "快讯", job: "monitoring/人工智能", split: "each", errors: [] },  // items: not for a whole report
      { key: "other", name: "别的 Job 的", job: "research/x", split: "whole", errors: [] },
    ];
    mount();
    fireEvent.click(screen.getByRole("button", { name: "写成视频" }));
    await screen.findByLabelText("写成哪种视频");
    expect(screen.queryByRole("button", { name: "快讯" })).toBeNull();
    expect(screen.queryByRole("button", { name: "别的 Job 的" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "深度解读 · 开发者" }));
    expect(await screen.findByText("new script page")).toBeTruthy();
    expect(posts[1]).toEqual({ url: "/api/production/deep/write", body: { report_path: "output/monitoring/2026-10-15_人工智能.md" } });
  });

  it("sends a typed title", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "写成视频" }));
    fireEvent.change(await screen.findByLabelText("写成哪种视频"), { target: { value: "point_short" } });
    fireEvent.change(screen.getByLabelText("视频题目"), { target: { value: "  这周最值得知道的一件事 " } });
    fireEvent.click(screen.getByRole("button", { name: "写这一篇" }));
    await screen.findByText("new script page");
    expect(posts[1].body).toEqual({ column: "point_short", report_id: "r9", topic: { title: "这周最值得知道的一件事" } });
  });

  it("sends a topic-source report to the topics page instead", async () => {
    report = { ...report, kind: "topic_source", kind_label: "选题来源报告" };
    mount();
    fireEvent.click(screen.getByRole("button", { name: "写成视频" }));
    expect((await screen.findByRole("link", { name: "选题页" })).getAttribute("href")).toBe("/topics");
    expect(screen.queryByLabelText("写成哪种视频")).toBeNull();
  });
});
