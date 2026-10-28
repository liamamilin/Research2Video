import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ThemeUsage } from "./ThemeUsage";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("reads recorded shared usage on demand and keeps unknown cost distinct from zero", async () => {
  const fetcher = vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify({
    stages: [{ key: "ideas", name: "整批出题", shared: true, batch_topics: 6, requests: 1, total_tokens: 1234,
      searches: 0, duration_seconds: 4.2, cost_usd: null, attempts: [{ status: "success" }] }], note: "整批共享，不等同实际账单",
  }) }));
  vi.stubGlobal("fetch", fetcher);
  render(<ThemeUsage matrix="demo" id="one" />);
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "查看阶段用量" }));
  expect(await screen.findByRole("table", { name: "阶段实际用量" })).toBeTruthy();
  expect(screen.getByText("整批出题 · 6题共享")).toBeTruthy();
  expect(screen.getByText("未知")).toBeTruthy();
  expect(screen.getByText("0", { exact: true })).toBeTruthy();
  expect(screen.queryByText("$0.0000")).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(1);
});
