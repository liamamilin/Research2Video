import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReportReuse } from "./ReportReuse";

const REPORT = {
  id: "r1", report_path: "output/video/x.md", kind: "video_research", kind_label: "文稿调研", job: "video/deep_dive_abc",
  topic_id: "2026-09-29/C2", topic_title: "长上下文计费陷阱", research: "deep_dive_developer", created_by: "admin",
  created_at: "2026-10-20T12:25:40+0800", on_disk: true,
  scripts: [
    { id: "s1", column_key: "deep_dive", status: "APPROVED", title: "长上下文计费陷阱", archived: false, created_at: "" },
    { id: "s2", column_key: "point_short", status: "DRAFT", title: "一个时间戳", archived: false, created_at: "" },
  ],
};
const CELLS = [
  { id: "a", key: "point_short", label: "一个知识点 · 开发者", usable: true, status: "enabled", column: { topic_kind: "single" } },
  { id: "b", key: "ai-developer-roundup-long", label: "盘点", usable: true, status: "experimental", column: { topic_kind: "roundup" } },
];
let posted: unknown = null;

beforeEach(() => {
  posted = null;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (init?.method === "POST") posted = JSON.parse(String(init.body));
    const body = u.includes("/reports/") ? REPORT : u.includes("/cells") ? CELLS : { id: "s3" };
    return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ReportReuse", () => {
  it("lists the other scripts from the same research and writes one more without researching again", async () => {
    render(
      <MemoryRouter initialEntries={["/video/s1"]}>
        <Routes>
          <Route path="/video/s1" element={<ReportReuse scriptId="s1" reportId="r1" canEdit />} />
          <Route path="/video/s3" element={<div>new script page</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect((await screen.findByRole("link", { name: "一个时间戳" })).getAttribute("href")).toBe("/video/s2");
    const select = await screen.findByLabelText("用这份研究写的配方");
    expect(screen.queryByRole("option", { name: "盘点" })).toBeNull(); // a roundup needs items picked, not one topic
    fireEvent.change(select, { target: { value: "point_short" } });
    fireEvent.click(screen.getByRole("button", { name: "用这份研究再写一篇" }));
    expect(await screen.findByText("new script page")).toBeTruthy();
    expect(posted).toEqual({ column: "point_short", report_id: "r1" });
  });

  it("only lists for a viewer", async () => {
    render(<MemoryRouter><ReportReuse scriptId="s1" reportId="r1" canEdit={false} /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("link", { name: "一个时间戳" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: "用这份研究再写一篇" })).toBeNull();
  });
});
