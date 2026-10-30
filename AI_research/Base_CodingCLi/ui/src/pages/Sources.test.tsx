import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SourcesPage } from "./Sources";

const BODY = {
  sources: [
    { id: "ieee_spectrum", name: "IEEE Spectrum", spec: "rss:https://spectrum.ieee.org/feeds/feed.rss", tags: ["tech", "industry"], note: "",
      used_by: [{ job: "monitoring/tech_weekly", name: "科技周报" }, { job: "monitoring/industry_weekly", name: "产业周报" }],
      last: { at: "2026-10-22T11:00:00+0800", job: "monitoring/tech_weekly", items: 10, error: "" } },
    { id: "bls", name: "BLS", spec: "rss:https://www.bls.gov/feed/bls_latest.rss", tags: ["economy"], note: "常常 403",
      used_by: [{ job: "monitoring/economy_weekly", name: "经济周报" }],
      last: { at: "2026-10-22T11:00:00+0800", job: "monitoring/economy_weekly", items: 0, error: "失败：HTTP Error 403: Forbidden" } },
    { id: "nasa", name: "NASA", spec: "rss:https://www.nasa.gov/feed/", tags: ["tech", "space"], note: "", used_by: [], last: null },
    { id: "", name: "rss:https://example.org/feed", spec: "rss:https://example.org/feed", tags: [], note: "",
      used_by: [{ job: "monitoring/x", name: "x" }], last: null },
  ],
  problems: [{ job: "monitoring/broken", problem: "信息源注册表里没有标签 nothing 的源" }],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderPage(body: unknown = BODY, ok = true) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok, status: ok ? 200 : 500, statusText: "x",
    text: async () => JSON.stringify(ok ? body : { detail: { message: "读不了" } }) }) as Response));
  render(<MemoryRouter><SourcesPage /></MemoryRouter>);
}

describe("Sources page", () => {
  it("identifies automatic sources without showing an unregistered warning", async () => {
    renderPage({ sources: [{ id: "", automatic: true, name: "AI release notes", spec: "page:https://vendor.example/updates",
      tags: ["ai_media"], note: "报告引用 2 页，变化 3 条（不等于成片数）", used_by: [], last: null }], problems: [] });
    expect(await screen.findByText("自动接入")).toBeTruthy();
    expect(screen.queryByText("没登记")).toBeNull();
    expect(screen.getByText(/报告引用 2 页/)).toBeTruthy();
  });

  it("keeps automatic sources from different matrices distinct when their URLs match", async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const shared = { id: '', automatic: true, spec: 'rss:https://institute.example/feed', note: '', used_by: [], last: null };
    try {
      renderPage({ sources: [
        { ...shared, name: '科技研究', tags: ['tech', '自动来源'] },
        { ...shared, name: '科学研究', tags: ['science', '自动来源'] },
      ], problems: [] });
      expect(await screen.findByText('科技研究')).toBeTruthy();
      expect(screen.getByText('科学研究')).toBeTruthy();
      expect(errors.mock.calls.flat().join(' ')).not.toMatch(/same key/);
    } finally { errors.mockRestore(); }
  });

  it("lists every source with who uses it and how its last fetch went, and names Jobs that point at a missing source", async () => {
    renderPage();
    expect(await screen.findByText("IEEE Spectrum")).toBeTruthy();
    expect(screen.getByText("科技周报").closest("a")?.getAttribute("href")).toBe("/jobs/monitoring%2Ftech_weekly");
    expect(screen.getByText("产业周报")).toBeTruthy();
    expect(screen.getByText(/失败：HTTP Error 403/)).toBeTruthy();
    expect(screen.getByText("没登记")).toBeTruthy();
    expect(screen.getByLabelText("引用错误").textContent).toContain("monitoring/broken");
  });

  it("filters by use, by failure and by tag", async () => {
    renderPage();
    await screen.findByText("IEEE Spectrum");
    fireEvent.click(screen.getByRole("button", { name: "还没人用" }));
    expect(screen.queryByText("IEEE Spectrum")).toBeNull();
    expect(screen.getByText("NASA")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /最近抓取失败 1/ }));
    expect(screen.getByText("BLS")).toBeTruthy();
    expect(screen.queryByText("NASA")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^全部/ }));
    fireEvent.change(screen.getByLabelText("标签"), { target: { value: "space" } });
    await waitFor(() => expect(screen.queryByText("IEEE Spectrum")).toBeNull());
    expect(screen.getByText("NASA")).toBeTruthy();
  });

  it("shows a load failure as a failure, not as an empty list", async () => {
    renderPage(null, false);
    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});
