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

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(PACK) }) as Response));
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
});
