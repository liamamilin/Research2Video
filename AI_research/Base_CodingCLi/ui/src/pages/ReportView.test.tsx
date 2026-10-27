import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportViewPage } from "./ReportView";
import { useAuthStore } from "@/lib/auth-store";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useAuthStore.setState({ user: null }); });

describe("Report reader", () => {
  it("gives repeated headings distinct anchors shared with the table of contents", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ content: "# 报告\n## 结论\n第一部分\n## 结论\n第二部分" }) }) as Response));
    const { container } = render(<MemoryRouter initialEntries={["/reports/view?path=a.md"]}><ReportViewPage /></MemoryRouter>);
    await screen.findByText("第一部分");
    expect([...container.querySelectorAll("h2")].map((h) => h.id)).toEqual(["结论", "结论-1"]);
    expect(screen.getAllByRole("button", { name: "结论" })).toHaveLength(2);
    const originalBody = container.querySelector(".md-body h2");
    fireEvent.click(screen.getByRole("button", { name: "目录" }));
    fireEvent.click(screen.getByRole("button", { name: "目录" }));
    expect(container.querySelector(".md-body h2")).toBe(originalBody);
    expect([...container.querySelectorAll("h2")].map((h) => h.id)).toEqual(["结论", "结论-1"]);
  });

  it("keeps the report readable after a refresh fails and offers retry", async () => {
    const fetch = vi.fn().mockResolvedValueOnce({ ok: true, status: 200, text: async () => JSON.stringify({ content: "# 保留的报告\n可读的内容" }) })
      .mockRejectedValue(new TypeError("offline"));
    vi.stubGlobal("fetch", fetch);
    render(<MemoryRouter initialEntries={["/reports/view?path=a.md"]}><ReportViewPage /></MemoryRouter>);
    await screen.findByText("可读的内容");
    fireEvent.click(screen.getByRole("button", { name: "刷新报告" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText("可读的内容")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });
});


it("folds a legacy model preamble while showing the report, and preserves literal tags in code examples", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ content: "<think>辅助内容</think>\n# 正式报告\n正文\n```xml\n<think>示例标签</think>\n```" }) }) as Response));
  const { container } = render(<MemoryRouter initialEntries={["/reports/view?path=a.md"]}><ReportViewPage /></MemoryRouter>);
  await screen.findByText("正文");
  expect(screen.getByRole("heading", { name: "正式报告" })).toBeTruthy();
  expect(screen.getByText("<think>示例标签</think>")).toBeTruthy();
  expect(container.querySelector("details")?.open).toBe(false);
});
