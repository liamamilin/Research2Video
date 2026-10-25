import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PublishPanel, RenderFolder } from "./VideoPublish";

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

  it("includes report material in the copied description and offers clickable original links", async () => {
    const references = [
      { title: "日食传说", url: "https://museum.example/eclipse/", date: "2026-10-21", cited: true },
      { title: "补充资料", url: "https://library.example/letter/", date: "2026-10-21", cited: false },
    ];
    const linked = { ...PACK, references, description: `${PACK.description}\n\n参考来源（文稿引用）：\n${references[0].url}\n\n报告补充资料：\n${references[1].url}` };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify(linked) })));
    render(<PublishPanel scriptId="s1" version={2} />);
    await screen.findByText("资料来源（2）");
    fireEvent.click(screen.getByText("资料来源（2）"));
    expect(screen.getByRole("link", { name: "日食传说" }).getAttribute("href")).toBe(references[0].url);
    expect(screen.getByRole("link", { name: "补充资料" }).getAttribute("href")).toBe(references[1].url);
    fireEvent.click(screen.getAllByText("复制")[1]);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(linked.description));
    fireEvent.click(screen.getByRole("button", { name: "复制资料链接" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(references.map((r) => `${r.title}\n${r.url}`).join("\n\n")));
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
    fireEvent.change(screen.getByLabelText("补录视频链接"), { target: { value: "https://b23.tv/abcdef" } });
    fireEvent.click(screen.getByText("记录发布"));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ url: "https://b23.tv/abcdef", version: 2, platform: "bilibili", account_uid: "" }));
  });
});

describe("Render folder", () => {
  const RENDER = {
    id: "r1", script_id: "s1", version: 2, status: "COMPLETED", title: "x", autovideo_job_id: null, autovideo_url: null,
    active_stage: null, attempt_count: 1, resolved_video_form: null, error_code: null, error_message: null, source_chars: 0,
    files: [{ name: "video.mp4", content_type: "video/mp4", bytes: 5242880, url: "/api/video/renders/r1/files/video.mp4" }],
    submitted_at: null, completed_at: null, created_at: "2026-10-20T10:00:00+08:00",
  } as never;

  it("lists the files and asks the app to show the folder in Finder", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method ?? "GET" });
      return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify({ path: "/s/r1/video.mp4", folder: "/s/r1", opened: true }) } as Response;
    }));
    render(<RenderFolder render={RENDER} canEdit />);
    expect(screen.getByText("5.0 MB")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "在访达中显示" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.includes("/api/video/renders/r1/reveal"))).toBe(true));
    expect(await screen.findByText("/s/r1/video.mp4")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "复制路径" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("/s/r1/video.mp4"));
  });

  it("has no buttons for a viewer", () => {
    render(<RenderFolder render={RENDER} />);
    expect(screen.queryByRole("button", { name: "在访达中显示" })).toBeNull();
  });
});

it("recognizes Douyin links and records the chosen account and platform without replacing Bilibili", async () => {
  const dyTarget = { platform: "douyin", account_uid: "456", account_name: "抖音创作者", current_account: true } as never;
  render(<PublishPanel scriptId="s1" version={2} canEdit distributions={[dyTarget]} publications={[{ id: "b", version: 2, platform: "bilibili", url: "https://b23.tv/old", published_on: "2026-10-20" } as never]} />);
  await screen.findByText(PACK.title);
  fireEvent.change(screen.getByLabelText("补录视频链接"), { target: { value: "https://www.douyin.com/video/7700436961890209034" } });
  expect(screen.getByLabelText("补录平台")).toHaveProperty("value", "douyin");
  await waitFor(() => expect(screen.getByLabelText("补录账号")).toHaveProperty("value", "456"));
  fireEvent.click(screen.getByRole("button", { name: "记录发布" }));
  await waitFor(() => expect(calls.find(c => c.method === "POST")?.body).toEqual({ url: "https://www.douyin.com/video/7700436961890209034", version: 2, platform: "douyin", account_uid: "456" }));
  expect(screen.getByRole("link", { name: "https://b23.tv/old" })).toBeTruthy();
});
