import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopicsPage } from "./Topics";

const TOPICS = {
  round: "2026-10-19",
  topics: [
    { id: "2026-10-19/F1", round: "2026-10-19", code: "F1", title: "Gemini 4 Argon 上线", fields: {}, score: null, scripted: true, published: true },
    { id: "2026-10-19/F2", round: "2026-10-19", code: "F2", title: "GPT-5.5 从 ChatGPT 退役", fields: {}, score: null, scripted: false, published: false },
  ],
};
const CELLS = [
  { id: "ai-developer-quick-take-long", key: "k", label: "快讯", usable: true, status: "enabled", parts: { type: { key: "quick_take" } }, column: { topic_kind: "single" } },
];

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true, status: 200, statusText: "ok",
    text: async () => JSON.stringify(String(url).includes("/cells") ? CELLS : TOPICS),
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
    expect(link?.getAttribute("href")).toBe("/video?cell=ai-developer-quick-take-long");
    expect(screen.getByText(/还有 1 条没写/)).toBeTruthy();
  });
});
