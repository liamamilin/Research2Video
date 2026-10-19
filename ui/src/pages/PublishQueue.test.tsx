import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PublishQueuePage } from "./PublishQueue";

const ROWS = [
  { script_id: "s1", title: "Gemini 4 Argon 上线", column_key: "flash", approved_version: 3, created_at: "", render: { id: "r1", status: "COMPLETED", version: 3, error: null }, video_url: "/api/video/renders/r1/files/video.mp4", publications: [], warnings: ["快讯超过 24 小时建议复核"] },
  { script_id: "s2", title: "已发的一期", column_key: "flash", approved_version: 1, created_at: "", render: null, video_url: null, publications: [{ id: "p1", url: "https://b23.tv/x", published_on: "2026-10-19" }], warnings: [] },
];

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(ROWS) }) as Response));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Publish queue", () => {
  it("shows what is ready to publish, what is published and the freshness warnings", async () => {
    render(<MemoryRouter><PublishQueuePage /></MemoryRouter>);
    expect(await screen.findByText("待发布")).toBeTruthy();
    expect(screen.getByText("已发布")).toBeTruthy();
    expect(screen.getByText("快讯超过 24 小时建议复核")).toBeTruthy();
    expect(screen.getByText("看成片").getAttribute("href")).toBe("/api/video/renders/r1/files/video.mp4");
    expect(screen.getByText("发布包与记录 →").closest("a")?.getAttribute("href")).toBe("/video/s1?tab=publish");
  });
});
