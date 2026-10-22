import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth-store";
import { AlertsCard } from "./AlertsCard";

const ALERTS = [
  { key: "batch-empty:r1", kind: "batch_empty", at: 1792443629, title: "10-20 05:00 的周计划批次一篇也没写",
    detail: "今天的快讯发现没有在限时内完成，用最新的一期；没有可写的新选题", link: "/" },
  { key: "job-cancels:monitoring/ai_flash_discovery:2026-10-20", kind: "job_restarted", at: 1792443000,
    title: "Job monitoring/ai_flash_discovery 在 2026-10-20 被取消了 70 次", detail: "共花了 234 次搜索", link: "/jobs/monitoring%2Fai_flash_discovery" },
];

function stub(rows: unknown[], calls: { url: string; method?: string; body?: string }[] = []) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method, body: init?.body as string | undefined });
    const body = url.endsWith("/ack") ? { acked: true } : rows;
    return { ok: true, status: 200, statusText: "ok", text: async () => JSON.stringify(body) } as Response;
  }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useAuthStore.setState({ user: null });
});

describe("Automation alerts card", () => {
  it("lists what went wrong on its own, with the cause, and an editor can acknowledge one", async () => {
    useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
    const calls: { url: string; method?: string; body?: string }[] = [];
    stub(ALERTS, calls);
    render(<MemoryRouter><AlertsCard /></MemoryRouter>);
    expect(await screen.findByText("10-20 05:00 的周计划批次一篇也没写")).toBeTruthy();
    expect(screen.getByText(/限时内完成/)).toBeTruthy();
    expect(screen.getByText(/被取消了 70 次/).closest("a")?.getAttribute("href")).toBe("/jobs/monitoring%2Fai_flash_discovery");
    fireEvent.click(screen.getAllByText("知道了")[1]);
    await waitFor(() => expect(screen.queryByText(/被取消了 70 次/)).toBeNull());
    const ack = calls.find((c) => c.url.endsWith("/api/video/alerts/ack"));
    expect(ack?.method).toBe("POST");
    expect(JSON.parse(ack?.body ?? "{}").key).toBe(ALERTS[1].key);
  });

  it("shows nothing when nothing went wrong, and a viewer gets no acknowledge button", async () => {
    useAuthStore.setState({ user: { username: "v", role: "viewer" } as never });
    stub([]);
    const { container } = render(<MemoryRouter><AlertsCard /></MemoryRouter>);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
    cleanup();
    stub(ALERTS);
    render(<MemoryRouter><AlertsCard /></MemoryRouter>);
    expect(await screen.findByText(/一篇也没写/)).toBeTruthy();
    expect(screen.queryByText("知道了")).toBeNull();
  });

  it("folds a long list, and 全部知道了 acknowledges every one", async () => {
    useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
    const rows = Array.from({ length: 5 }, (_, i) => ({ key: `k${i}`, kind: "job_failed", at: 1792443629 - i, title: `第${i}条`, detail: "", link: "" }));
    const calls: { url: string; method?: string; body?: string }[] = [];
    stub(rows, calls);
    render(<MemoryRouter><AlertsCard /></MemoryRouter>);
    expect(await screen.findByText("第0条")).toBeTruthy();
    expect(screen.queryByText("第3条")).toBeNull();
    fireEvent.click(screen.getByText("还有 2 条，展开"));
    expect(screen.getByText("第4条")).toBeTruthy();
    fireEvent.click(screen.getByText("全部知道了"));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/ack")).length).toBe(5));
    await waitFor(() => expect(screen.queryByText("第0条")).toBeNull());
  });
});
