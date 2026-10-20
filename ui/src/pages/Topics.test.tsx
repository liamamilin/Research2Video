import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopicsPage } from "./Topics";

const TOPICS = {
  round: "2026-10-19",
  topics: [
    { id: "2026-10-19/F1", round: "2026-10-19", code: "F1", title: "Gemini 4 Argon 上线", fields: { 发布日期: "2026-10-14" }, score: 9, scripted: true, published: true, scripts: [{ id: "s9", column_key: "flash", status: "APPROVED", created_at: "2026-10-19T14:45:00+0800" }] },
    { id: "2026-10-19/F2", round: "2026-10-19", code: "F2", title: "GPT-5.5 从 ChatGPT 退役", fields: {}, score: null, scripted: false, published: false },
  ],
};
const CELLS = [
  { id: "ai-developer-quick-take-long", key: "flash", label: "快讯", usable: true, status: "enabled", parts: { type: { key: "quick_take" } }, column: { topic_kind: "single" } },
];

const COVERAGE = [
  { cell: "flash", label: "快讯", status: "enabled", sources: ["flash"], open: 1, written: 1, report_date: "2026-10-19" },
  { cell: "cmp", label: "对比", status: "experimental", sources: [], open: 0, written: 0, report_date: null },
];

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true, status: 200, statusText: "ok",
    text: async () => JSON.stringify(String(url).includes("/coverage") ? COVERAGE : String(url).includes("/cells") ? CELLS : TOPICS),
  }) as Response));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Topics page", () => {
  it("flags written and published topics and links the rest to the cell picker", async () => {
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    expect(await screen.findByText("已发布")).toBeTruthy();
    const link = (await screen.findByText("用这个写稿")).closest("a");
    expect(link?.getAttribute("href")).toBe("/video?cell=ai-developer-quick-take-long&topic=2026-10-19%2FF2");
    expect(screen.getByText(/还有 1 条没写/)).toBeTruthy();
  });

  it("shows the event date next to the report date and which script already covers a topic, and filters", async () => {
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    expect(await screen.findByText("事件日期 2026-10-14")).toBeTruthy();
    expect(screen.getByText("已有文稿").getAttribute("href")).toBe("/video/s9");
    fireEvent.change(screen.getByLabelText("写稿"), { target: { value: "no" } });
    await waitFor(() => expect(screen.queryByText("Gemini 4 Argon 上线")).toBeNull());
    expect(screen.getByText("GPT-5.5 从 ChatGPT 退役")).toBeTruthy();
  });

  it("has a tab per cell; a cell without a source says it is chosen by hand", async () => {
    render(<MemoryRouter><TopicsPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("tab", { name: /对比（实验）/ }));
    expect(await screen.findByText(/还没有选题来源/)).toBeTruthy();
    expect(screen.queryByText("GPT-5.5 从 ChatGPT 退役")).toBeNull();
  });
});
