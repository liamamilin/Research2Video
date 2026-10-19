import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PublishPanel } from "./VideoPublish";

const PACK = {
  title: "Gemini 4 Argon：限时价 12 月翻倍",
  description: "摘要。\n\n00:00 开场\n00:29 展开\n00:49 结论",
  chapters: [],
  chapter_note: "章节格式来自第三方资料，没有找到 B 站官方说明。",
  tags: ["快讯", "开发者", "AI"],
  cover: { headline: "Gemini 4 Argon", badge: "快讯" },
  references: [],
  warnings: ["1 个来源没有发布日期，简介里只写了网址。"],
  version: 2,
};

const writeText = vi.fn(async () => undefined);

let calls: { url: string; method: string; body?: any }[] = [];

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
      return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(PACK) } as Response;
    }),
  );
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  writeText.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Publish pack", () => {
  it("shows the pack, its warnings and the unverified chapter format, and copies the description", async () => {
    render(<PublishPanel scriptId="s1" version={2} />);
    await screen.findByText(PACK.title);
    expect(screen.getByText(PACK.warnings[0])).toBeTruthy();
    expect(screen.getByText(PACK.chapter_note)).toBeTruthy();
    expect(screen.getByText("快讯 开发者 AI")).toBeTruthy();
    fireEvent.click(screen.getAllByText("复制")[1]);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(PACK.description));
  });

  it("starts a recheck for a flash script and shows the latest verdict", async () => {
    const done = { id: "r1", version: 2, status: "DONE", verdict: "有变化", summary: "输入价 12 月涨到 4 美元", error: null,
      created_at: "2026-10-19T10:00:00+0800", finished_at: "2026-10-19T10:05:00+0800" } as const;
    render(<PublishPanel scriptId="s1" version={2} canEdit recheckEnabled rechecks={[done]} />);
    await screen.findByText(PACK.title);
    expect(screen.getByText("有变化")).toBeTruthy();
    expect(screen.getByText("输入价 12 月涨到 4 美元")).toBeTruthy();
    fireEvent.click(screen.getByText("发布前复核"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/recheck"))).toBe(true));
  });

  it("offers no recheck for other columns, and records a publication link", async () => {
    render(<PublishPanel scriptId="s1" version={2} canEdit publications={[{ id: "p1", script_id: "s1", version: 2, render_id: null,
      platform: "bilibili", url: "https://www.bilibili.com/video/BV1old", published_on: "2026-10-18", title: "", note: "", recorded_by: "e" }]} />);
    await screen.findByText(PACK.title);
    expect(screen.queryByText("发布前复核")).toBeNull();
    expect(screen.getByText("https://www.bilibili.com/video/BV1old")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText(/B 站视频链接/), { target: { value: "https://b23.tv/abcdef" } });
    fireEvent.click(screen.getByText("记录发布"));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ url: "https://b23.tv/abcdef" }));
  });
});
